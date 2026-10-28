<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ApiError, errorMessage, isAbort } from '../api/client'
import { boundedRequest } from '../api/boundedRequest'
import { changedSettingsDraft, type SettingsDraft } from '../api/settingsDraft'
import { bytes, parseUserIds } from '../api/format'
import { validationBlockReason } from '../api/localApi'
import { connectionEditBlockReason, connectionFields } from '../api/settingsConnection'
import { proxyLabels } from '../api/proxy'
import { confirmationVariables, defaultConfirmationTemplate, defaultFilenameTemplate, filenameVariables, templateExamples } from '../api/templates'
import type { BotRequestSafety, DiagnosticLogStatus, LocalApiStatus, Settings, SettingsValues } from '../api/types'
import { useSystemStatus } from '../composables/useSystemStatus'
import BotSafetyPanel from '../components/BotSafetyPanel.vue'
import DiagnosticLogPanel from '../components/DiagnosticLogPanel.vue'
import SettingsSource from '../components/SettingsSource.vue'
import LocalApiPanel from '../components/LocalApiPanel.vue'
import SettingsBackup from '../components/SettingsBackup.vue'

const emit = defineEmits<{ dirty: [value: boolean] }>()
const settings = ref<Settings>()
const section = ref('connection')
const sections = [{ id: 'connection', label: '连接 Telegram' }, { id: 'download', label: '下载与保存' }, { id: 'templates', label: '提示与命名' }, { id: 'maintenance', label: '维护与备份' }]
const form = reactive<SettingsValues>({
  bot_api_mode: 'cloud',
  telegram_api_id: '', telegram_api_hash: '',
  confirmation_template: defaultConfirmationTemplate, filename_template: defaultFilenameTemplate,
  bot_token: '', allowed_user_ids: [], confirmation_timeout_seconds: 1800, download_concurrency: 3,
  max_auto_retries: 3, download_root_id: 'default', timezone: 'Asia/Shanghai',
  diagnostic_log_retention_days: 7,
  diagnostic_log_cleanup_time: '08:00',
  proxy: { enabled: false, scheme: 'socks5', host: '', port: 1080, username: '', password: '' },
})
const userIds = ref('')
const clearBotToken = ref(false)
const clearProxyPassword = ref(false)
const clearApiHash = ref(false)
const busy = ref(false)
const writing = ref(false)
const syncing = ref(false)
const writeOutcomeUnknown = ref(false)
const controller = new AbortController()
onUnmounted(() => controller.abort())
const backupBusy = ref(false)
const loading = ref(true)
const error = ref('')
const conflict = ref(false)
type ValidationResult = { warnings: string[]; bot: { id: number; username: string } | null }
const snapshot = ref('')
const awaitingConnectionRevision = ref<number>()
const { data: liveSystem, error: statusError, refresh: refreshStatus, debug } = useSystemStatus()
const roots = computed(() => liveSystem.value?.storage.roots ?? [])
function botSafetyChanged(status: BotRequestSafety) { if (liveSystem.value) liveSystem.value.bot.request_safety = status; void refreshStatus() }
function diagnosticLogChanged(status: DiagnosticLogStatus) { if (liveSystem.value) liveSystem.value.diagnostic_log = status; void refreshStatus() }
function localApiChanged(status: LocalApiStatus) {
  if (liveSystem.value) liveSystem.value.local_api = status
  void refreshStatus()
}
const proxyStatus = computed(() => {
  const saved = settings.value?.proxy_status
  const live = liveSystem.value?.proxy
  // A diagnostic request started before PATCH may resolve after it. Never show its older revision as current.
  return live && (live.desired_revision ?? 0) >= (saved?.desired_revision ?? 0) ? live : saved
})
const proxyMessages: Record<string, string> = { disabled: '当前未启用代理。', pending: '代理变更等待后台确认，尚未生效。', applying: '正在应用代理配置，请稍候。', active: '代理配置已应用。', failed: '代理配置应用失败，请检查代理地址、认证和可用性。', unavailable: '网络组件未连接，请检查连接设置与本地服务。' }
const connectionBlocked = computed(() => writeOutcomeUnknown.value ? '上次保存的结果尚未确认，请重新同步状态后核对配置。未保存输入会保留，系统不会自动重复保存。' : connectionEditBlockReason(liveSystem.value?.local_api, !!statusError.value, awaitingConnectionRevision.value !== undefined))
const connectionLocked = computed(() => !!connectionBlocked.value)
watch(liveSystem, status => {
  // Only a new poll response can acknowledge a save; an optimistic proxy patch cannot.
  if (!statusError.value && awaitingConnectionRevision.value !== undefined && (status?.proxy.desired_revision ?? 0) >= awaitingConnectionRevision.value) awaitingConnectionRevision.value = undefined
})
const validationBlocked = computed(() => connectionBlocked.value || (liveSystem.value ? validationBlockReason(liveSystem.value.local_api, proxyStatus.value) : '正在获取服务状态，请稍候。'))
const current = computed(() => JSON.stringify({ form, userIds: userIds.value, clearBotToken: clearBotToken.value, clearProxyPassword: clearProxyPassword.value, clearApiHash: clearApiHash.value }))
const dirty = computed(() => !!snapshot.value && snapshot.value !== current.value)
const examples = computed(() => templateExamples(form.confirmation_template, form.filename_template, form.confirmation_timeout_seconds))
const confirmationMinutes = computed({
  get: () => form.confirmation_timeout_seconds / 60,
  set: (minutes: number | undefined) => { if (minutes !== undefined) form.confirmation_timeout_seconds = Math.round(minutes * 60) },
})
// The shell uses this notification to guard navigation without persisting secrets in local storage.
watch(dirty, value => emit('dirty', value))
const editable = (field: string) => settings.value?.editable[field] !== false

function assign(value: Settings) {
  settings.value = value
  Object.assign(form, structuredClone(value.values))
  form.bot_token = ''
  form.telegram_api_hash = ''
  form.proxy.password = ''
  userIds.value = form.allowed_user_ids.join('\n')
  clearBotToken.value = false; clearProxyPassword.value = false; clearApiHash.value = false
  snapshot.value = current.value; conflict.value = false
}
async function load() {
  loading.value = true; error.value = ''
  try {
    const saved = await boundedRequest<Settings>('/settings', { signal: controller.signal })
    if (!controller.signal.aborted) { assign(saved); awaitingConnectionRevision.value = saved.revision; void refreshStatus() }
  } catch (cause) { if (!isAbort(cause)) { error.value = errorMessage(cause); ElMessage.error(error.value) } }
  finally { loading.value = false }
}
function payload(includeConnection = true) {
  if (examples.value.confirmationError || examples.value.fileError) section.value = 'templates'
  if (includeConnection && form.proxy.enabled && form.proxy.scheme === 'socks5') {
    const encoder = new TextEncoder()
    if (encoder.encode(form.proxy.username).length > 255 || (!clearProxyPassword.value && encoder.encode(form.proxy.password).length > 255)) {
      throw new Error('SOCKS5 用户名和密码各自不能超过 255 字节；中文等字符会占用多个字节，请缩短后重试。')
    }
  }
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(form.diagnostic_log_cleanup_time)) throw new Error('日志自动清理时间须为 00:00–23:59。')
  if (!Number.isInteger(form.diagnostic_log_retention_days) || form.diagnostic_log_retention_days < 0 || form.diagnostic_log_retention_days > 3650) throw new Error('诊断日志保留天数须为 0–3650 的整数。')
  if (examples.value.confirmationError || examples.value.fileError) throw new Error(examples.value.confirmationError || examples.value.fileError)
  if (includeConnection && form.telegram_api_id && !/^[1-9]\d*$/.test(form.telegram_api_id)) throw new Error('API ID 请填写正整数，或留空。')
  const values = { ...form, allowed_user_ids: parseUserIds(userIds.value), proxy: { ...form.proxy } }
  const changed: Record<string, unknown> = {}
  for (const key of Object.keys(values) as (keyof SettingsValues)[]) {
    if ((includeConnection || !connectionFields.has(key)) && editable(key) && JSON.stringify(values[key]) !== JSON.stringify(settings.value?.values[key])) changed[key] = values[key]
  }
  if (includeConnection && clearBotToken.value && editable('bot_token')) changed.clear_bot_token = true
  if (includeConnection && clearProxyPassword.value && editable('proxy')) changed.clear_proxy_password = true
  if (includeConnection && clearApiHash.value && editable('telegram_api_hash')) changed.clear_telegram_api_hash = true
  return changed
}
async function save() {
  if (!settings.value || busy.value || syncing.value || backupBusy.value || conflict.value) return
  busy.value = true; error.value = ''
  try {
    const deferConnection = connectionLocked.value
    const updates = payload(!deferConnection)
    if (!Object.keys(updates).length) {
      ElMessage.info(deferConnection ? '当前连接修改尚未保存，请等待连接操作结束后再保存。' : '没有需要保存的修改。')
      return
    }
    const preserved = deferConnection ? { bot_api_mode: form.bot_api_mode, bot_token: form.bot_token, telegram_api_id: form.telegram_api_id, telegram_api_hash: form.telegram_api_hash, proxy: { ...form.proxy }, clearBotToken: clearBotToken.value, clearProxyPassword: clearProxyPassword.value, clearApiHash: clearApiHash.value } : undefined
    writing.value = true
    const saved = await boundedRequest<Settings>('/settings', { method: 'PATCH', signal: controller.signal, body: JSON.stringify({ revision: settings.value.revision, ...updates }) })
    writing.value = false
    assign(saved)
    if (preserved) {
      const { clearBotToken: token, clearProxyPassword: password, clearApiHash: hash, ...values } = preserved
      Object.assign(form, values)
      clearBotToken.value = token; clearProxyPassword.value = password; clearApiHash.value = hash
    }
    if (Object.keys(updates).some(key => connectionFields.has(key))) awaitingConnectionRevision.value = saved.revision
    // Prefer this response until the diagnostic poll catches up to the saved revision.
    if (liveSystem.value) liveSystem.value.proxy = saved.proxy_status
    await refreshStatus()
    if (controller.signal.aborted) return
    ElMessage.success(preserved && dirty.value ? '其他设置已保存；连接修改已保留，待当前操作结束后再保存。' : '设置已保存，请查看各项生效状态。')
  } catch (cause) {
    if (isAbort(cause)) return
    if (writing.value && cause instanceof ApiError && cause.status === 0) {
      writeOutcomeUnknown.value = true
      void refreshStatus()
    }
    error.value = errorMessage(cause)
    if (cause instanceof ApiError && cause.code === 'REVISION_CONFLICT') {
      conflict.value = true
      ElMessage({ type: 'warning', message: '配置已被其他会话修改。你的输入已保留，请先重新读取，再应用修改。', duration: 8000, showClose: true })
    } else ElMessage({ type: 'error', message: error.value, duration: 6000, showClose: true })
  } finally { busy.value = false; writing.value = false }
}
async function validate() {
  if (busy.value || syncing.value || backupBusy.value || conflict.value || validationBlocked.value) return
  busy.value = true; error.value = ''
  const checked = current.value
  try {
    const result = await boundedRequest<ValidationResult>('/settings/validate', { method: 'POST', signal: controller.signal, body: JSON.stringify(payload()) }, 30000)
    if (checked === current.value) {
      const summary = result.bot ? '校验通过，Bot：@' + result.bot.username : '配置校验通过（未配置 Bot）'
      ElMessage({ type: result.warnings.length ? 'warning' : 'success', message: [summary, ...result.warnings, '校验不会保存配置。'].join('；'), duration: result.warnings.length ? 8000 : 4000, showClose: true })
    } else ElMessage.warning('校验期间配置已修改，请重新校验当前输入。')
  }
  catch (cause) { if (!isAbort(cause)) ElMessage({ type: 'error', message: checked === current.value ? errorMessage(cause) : '校验期间配置已修改，请重新校验当前输入。', duration: 6000, showClose: true }) }
  finally { busy.value = false }
}
async function reload() {
  if (dirty.value) {
    try { await ElMessageBox.confirm('重新读取将放弃尚未保存的修改。', '重新读取配置', { confirmButtonText: '放弃并读取', cancelButtonText: '保留修改' }) }
    catch { return }
  }
  await load()
}
function settingsRestored(saved: Settings) {
  assign(saved)
  awaitingConnectionRevision.value = saved.revision
  void refreshStatus()
}
async function resyncConnection() {
  if (syncing.value || writing.value || backupBusy.value || controller.signal.aborted) return
  syncing.value = true
  try {
    const saved = await boundedRequest<Settings>('/settings', { signal: controller.signal })
    const edits = snapshot.value ? changedSettingsDraft(JSON.parse(snapshot.value) as SettingsDraft, JSON.parse(current.value) as SettingsDraft) : undefined
    assign(saved)
    if (edits) {
      Object.assign(form, edits.form)
      Object.assign(form.proxy, edits.proxy)
      if (edits.userIds !== undefined) userIds.value = edits.userIds
      if (edits.clearBotToken !== undefined) clearBotToken.value = edits.clearBotToken
      if (edits.clearProxyPassword !== undefined) clearProxyPassword.value = edits.clearProxyPassword
      if (edits.clearApiHash !== undefined) clearApiHash.value = edits.clearApiHash
    }
    writeOutcomeUnknown.value = false
    awaitingConnectionRevision.value = saved.revision
    await refreshStatus()
    if (controller.signal.aborted) return
    ElMessage({ type: statusError.value || dirty.value ? 'warning' : 'success', message: statusError.value ? '配置已重新读取，服务状态仍未更新，请检查应用连接后再次同步。输入已保留。' : dirty.value ? '服务端配置已重新读取，未保存输入已保留；请核对这些输入后再保存。' : '状态已重新同步，请查看当前连接进度。', duration: 6000, showClose: true })
  } catch (cause) { if (!isAbort(cause)) ElMessage.error(errorMessage(cause)) }
  finally { syncing.value = false }
}
onMounted(load)
</script>
<template>
  <section class="settings-view">
    <div class="settings-scroll">
      <nav class="settings-tabs" aria-label="设置分类"><button v-for="item in sections" :key="item.id" type="button" :class="{ active: section === item.id }" :aria-current="section === item.id ? 'true' : undefined" @click="section = item.id">{{ item.label }}</button></nav>
      <el-skeleton v-if="loading" :rows="8" animated />
      <div v-else-if="!settings" class="panel settings-section" role="alert"><p>{{ error || '暂时无法读取配置。' }}</p><button type="button" class="connection-resync" @click="load">重新读取设置</button></div>
      <el-form v-else-if="settings" label-position="top" id="settings-form" class="settings-form" :disabled="busy || backupBusy" novalidate @submit.prevent="save">
        <section v-show="section === 'connection'" class="panel settings-section">
          <div class="section-heading"><div><h2>连接 Telegram</h2><p class="muted">选择 API 服务，设置 Bot 与允许使用的人。</p></div></div>
          <el-alert v-if="connectionBlocked" class="connection-application-notice" :title="connectionBlocked" type="info" :closable="false" show-icon role="status" />
          <div v-if="connectionBlocked || conflict" class="connection-resync-row"><button type="button" class="connection-resync" :disabled="syncing || writing || backupBusy" :aria-busy="syncing" @click="resyncConnection">{{ syncing ? '正在同步状态…' : '重新同步状态' }}</button><span>保留当前输入，不重复保存，也不会跳过后台的安全等待。</span></div>
          <p v-if="liveSystem?.bot.username" class="field-hint">当前 Bot：@{{ liveSystem.bot.username }}{{ statusError ? '（上次结果，等待更新）' : '' }}，请向此 Bot 发送媒体。</p>
          <el-form-item label="Bot API 服务" label-for="bot-api-mode">
            <template #label><span class="setting-label">Bot API 服务<SettingsSource :source="settings.sources.bot_api_mode" :editable="editable('bot_api_mode')" /></span></template>
            <el-select id="bot-api-mode" aria-label="Bot API 服务" v-model="form.bot_api_mode" :disabled="connectionLocked || !editable('bot_api_mode')"><el-option label="Telegram 官方云端" value="cloud" /><el-option label="本地 Bot API" value="local" /></el-select>
            <p v-if="form.bot_api_mode === 'cloud'" class="field-hint mode-hint">只需 Bot Token，无需 API ID / Hash。单个文件最多下载 <strong>20 MB</strong>。</p>
            <p v-else class="field-hint mode-hint">支持超过 20 MB 的文件。{{ liveSystem?.local_api?.managed ? '填写下方 API ID / Hash 并保存，后台将协调启动和切换。' : '需由部署环境配置 API ID / Hash 并启动本地服务；从云端迁移须完成官方 logOut 流程。' }}</p>
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="Bot Token" label-for="bot-token">
              <template #label><span class="setting-label">Bot Token<SettingsSource :source="settings.sources.bot_token" :editable="editable('bot_token')" /></span></template>
              <el-input id="bot-token" aria-label="Bot Token" v-model="form.bot_token" type="password" show-password autocomplete="new-password" :disabled="connectionLocked || !editable('bot_token') || clearBotToken" :placeholder="settings.configured.bot_token ? '已配置 · 留空保持不变' : '填写 @BotFather 提供的 Token'" />
              <p class="field-hint">从 @BotFather 获取。已保存的 Token 留空不变。</p>
              <el-checkbox v-if="settings.configured.bot_token && editable('bot_token')" v-model="clearBotToken" :disabled="connectionLocked">清除已保存的 Token</el-checkbox>
            </el-form-item>
            <el-form-item label="允许使用的 Telegram 用户 ID" label-for="user-ids">
              <template #label><span class="setting-label">允许使用的 Telegram 用户 ID<SettingsSource :source="settings.sources.allowed_user_ids" :editable="editable('allowed_user_ids')" /></span></template>
              <el-input id="user-ids" aria-label="允许使用的 Telegram 用户 ID" v-model="userIds" type="textarea" :rows="3" :disabled="!editable('allowed_user_ids')" placeholder="每行一个 ID，或使用逗号分隔" />
              <p class="field-hint">正整数 ID，每行一个或逗号分隔。配置 Bot 时至少填写一个；仅名单内用户可私聊下载。</p>
            </el-form-item>
          </div>
          <div v-if="form.bot_api_mode === 'local' || liveSystem?.local_api?.desired_mode === 'local' || liveSystem?.local_api?.applied_mode === 'local'" class="local-api-credentials">
            <h3>Telegram 应用凭据</h3>
            <p class="field-hint">通过 <a href="https://my.telegram.org" target="_blank" rel="noopener noreferrer">my.telegram.org</a> 的 API development tools 获取，与 Bot Token 不同。云端模式无需填写。</p>
            <div class="form-grid">
              <el-form-item label="API ID" label-for="telegram-api-id"><template #label><span class="setting-label">API ID<SettingsSource :source="settings.sources.telegram_api_id" :editable="editable('telegram_api_id')" /></span></template><el-input id="telegram-api-id" aria-label="API ID" v-model="form.telegram_api_id" inputmode="numeric" autocomplete="off" :disabled="connectionLocked || !editable('telegram_api_id')" placeholder="填写数字 API ID" /></el-form-item>
              <el-form-item label="API Hash" label-for="telegram-api-hash"><template #label><span class="setting-label">API Hash<SettingsSource :source="settings.sources.telegram_api_hash" :editable="editable('telegram_api_hash')" /></span></template><el-input id="telegram-api-hash" aria-label="API Hash" v-model="form.telegram_api_hash" type="password" show-password autocomplete="new-password" :disabled="connectionLocked || !editable('telegram_api_hash') || clearApiHash" :placeholder="settings.configured.telegram_api_hash ? '已配置 · 留空保持不变' : '填写 API Hash'" /><p class="field-hint">已保存的 Hash 不回显，留空保持不变。</p><el-checkbox v-if="settings.configured.telegram_api_hash && editable('telegram_api_hash')" v-model="clearApiHash" :disabled="connectionLocked">清除已保存的 API Hash</el-checkbox></el-form-item>
            </div>
            <p v-if="liveSystem?.local_api && !liveSystem.local_api.managed" class="field-hint">当前 API 由外部管理，保存这些凭据不会修改外部进程；需先升级为配套管理组件。</p>
          </div>
        <div class="connection-proxy">
          <div class="section-heading"><div><h3>网络代理</h3><p class="muted">用于 Telegram 连接，WebUI 内网访问保持直连。</p></div></div>
          <el-form-item label="启用代理" label-for="proxy-enabled">
            <template #label><span class="setting-label">启用代理<SettingsSource :source="settings.sources.proxy" :editable="editable('proxy')" /></span></template>
            <el-switch id="proxy-enabled" aria-label="启用代理" v-model="form.proxy.enabled" :disabled="connectionLocked || !editable('proxy')" /><span class="switch-label">{{ form.proxy.enabled ? '已开启（当前表单）' : '已关闭（当前表单）' }}</span>
          </el-form-item>
          <div v-if="form.proxy.enabled" class="form-grid"><el-form-item label="协议" label-for="proxy-scheme"><el-select id="proxy-scheme" v-model="form.proxy.scheme" :disabled="connectionLocked || !editable('proxy')"><el-option label="SOCKS5" value="socks5" /><el-option label="HTTP CONNECT" value="http" /></el-select></el-form-item><el-form-item label="代理地址" label-for="proxy-host"><el-input id="proxy-host" v-model="form.proxy.host" :disabled="connectionLocked || !editable('proxy')" placeholder="代理服务的 IP 或域名" /></el-form-item><el-form-item label="端口" label-for="proxy-port"><el-input-number id="proxy-port" v-model="form.proxy.port" :min="1" :max="65535" :disabled="connectionLocked || !editable('proxy')" /></el-form-item><el-form-item label="用户名（可选）" label-for="proxy-username"><el-input id="proxy-username" v-model="form.proxy.username" :disabled="connectionLocked || !editable('proxy')" autocomplete="off" /></el-form-item><el-form-item label="密码（可选）" label-for="proxy-password"><el-input id="proxy-password" v-model="form.proxy.password" type="password" show-password autocomplete="new-password" :disabled="connectionLocked || !editable('proxy') || clearProxyPassword" :placeholder="settings.configured.proxy_password ? '已配置 · 留空保持不变' : '无认证可留空'" /><p class="field-hint">已保存的密码留空不变。</p><el-checkbox v-if="settings.configured.proxy_password && editable('proxy')" v-model="clearProxyPassword" :disabled="connectionLocked">清除已保存的代理密码</el-checkbox></el-form-item></div>
          <p class="field-hint proxy-mode-hint">云端和本地 API 使用相同的代理设置。开启后通过所填代理连接 Telegram；关闭后使用容器默认网络，不会自动读取宿主机的系统代理。保存后自动应用，无需额外 Compose 文件。代理地址须可从容器访问，127.0.0.1 指向容器自身。</p>
          <div class="proxy-saved-state"><div class="proxy-state-heading"><strong>代理生效状态</strong><span class="proxy-state-label">{{ proxyLabels[proxyStatus?.state ?? ''] || proxyStatus?.state || '等待确认' }}</span></div><el-alert :title="proxyStatus?.message || proxyMessages[proxyStatus?.state ?? ''] || '代理状态等待后台确认。'" :type="proxyStatus?.state === 'active' ? 'success' : ['failed', 'unavailable'].includes(proxyStatus?.state ?? '') ? 'error' : 'info'" :closable="false" show-icon /><details v-if="debug" class="settings-details"><summary>查看配置详情</summary><div class="details-content"><p>已保存版本 {{ proxyStatus?.desired_revision ?? '—' }} / 已应用版本 {{ proxyStatus?.applied_revision ?? '—' }}</p><p>状态对应已保存配置；Bot 是否连接请查看上方服务状态。</p></div></details></div>
        </div>
          <BotSafetyPanel :status="liveSystem?.bot.request_safety" :disabled="busy || backupBusy || dirty || conflict" :stale="!!statusError" @changed="botSafetyChanged" />
          <LocalApiPanel :detailed="debug" :status="liveSystem?.local_api" :bot="liveSystem?.bot" :draft="dirty" :disabled="busy || syncing || backupBusy || writeOutcomeUnknown" :stale="!!statusError" @changed="localApiChanged" @sync="refreshStatus" />
          <p v-if="dirty" class="field-hint">服务重试使用已保存配置；当前未保存的输入会保留。</p>
          <details class="settings-details"><summary>连接与切换须知</summary><div class="details-content"><p>保存与连接校验分开；已保存选择与实际使用可能暂时不同，请查看服务状态。切换期间 Bot 会暂停接收，已排队任务会保留。</p><p v-if="liveSystem?.local_api?.managed">填写本地 API 凭据、按需设置下方代理并保存，后台等待网络就绪和下载安全结束后切换。云端退出后回切需等待至少 10 分钟，WebUI 持续可用。</p><p v-else>当前为外部管理模式，请按部署指南配置本地服务。</p><p>其他程序须停止使用同一个 Bot。更换 Bot 身份前，请结束原 Bot 的活动和待确认请求。</p></div></details>
        </section>
        <section v-show="section === 'download'" class="panel settings-section">
          <div class="section-heading"><div><h2>下载与保存</h2><p class="muted">后续任务使用新设置，已有任务保留保存位置。</p></div></div>
          <div class="form-grid">
            <el-form-item label="下载根目录" label-for="download-root">
              <template #label><span class="setting-label">下载根目录<SettingsSource :source="settings.sources.download_root_id" :editable="editable('download_root_id')" /></span></template>
              <el-select id="download-root" aria-label="下载根目录" v-model="form.download_root_id" :disabled="!editable('download_root_id')"><el-option v-for="root in roots" :key="root.id" :value="root.id" :label="root.label + (root.available ? '' : '（不可用）')" /><el-option v-if="!roots.some(root => root.id === form.download_root_id)" :value="form.download_root_id" :label="form.download_root_id" /></el-select>
              <p class="field-hint">可选目录由部署挂载决定。修改后，旧待确认卡片需重新确认。</p>
            </el-form-item>
            <el-form-item label="归档时区" label-for="timezone">
              <template #label><span class="setting-label">归档时区<SettingsSource :source="settings.sources.timezone" :editable="editable('timezone')" /></span></template>
              <el-input id="timezone" aria-label="归档时区" v-model="form.timezone" :disabled="!editable('timezone')" placeholder="Asia/Shanghai" />
              <p class="field-hint">用于归档日期、文件名时间和每日日志清理，例如 Asia/Shanghai。</p>
            </el-form-item>
            <el-form-item label="确认有效期（分钟）" label-for="confirmation-timeout">
              <template #label><span class="setting-label">确认有效期（分钟）<SettingsSource :source="settings.sources.confirmation_timeout_seconds" :editable="editable('confirmation_timeout_seconds')" /></span></template>
              <el-input-number id="confirmation-timeout" aria-label="确认有效期（分钟）" v-model="confirmationMinutes" :min="0.5" :max="1440" :step="1" :disabled="!editable('confirmation_timeout_seconds')" /><p class="field-hint">默认 30 分钟，可设 0.5–1440 分钟；不影响已发卡片。</p>
            </el-form-item>
            <el-form-item label="同时下载的文件数" label-for="download-concurrency">
              <template #label><span class="setting-label">同时下载的文件数<SettingsSource :source="settings.sources.download_concurrency" :editable="editable('download_concurrency')" /></span></template>
              <el-input-number id="download-concurrency" aria-label="同时下载的文件数" v-model="form.download_concurrency" :min="1" :max="16" :disabled="!editable('download_concurrency')" /><p class="field-hint">默认 3 个，媒体组共用额度；调低不会中断当前下载。</p>
            </el-form-item>
            <el-form-item label="失败自动重试次数" label-for="max-retries">
              <template #label><span class="setting-label">失败自动重试次数<SettingsSource :source="settings.sources.max_auto_retries" :editable="editable('max_auto_retries')" /></span></template>
              <el-input-number id="max-retries" aria-label="失败自动重试次数" v-model="form.max_auto_retries" :min="0" :max="10" :disabled="!editable('max_auto_retries')" /><p class="field-hint">默认 3 次，不含首次执行。空间与权限问题需手动处理。</p>
            </el-form-item>
          </div>
          <div class="storage-overview"><h3>存储空间</h3><div v-for="root in roots" :key="root.id" class="storage-row"><div><strong>{{ root.label }}</strong><p :class="root.available ? 'muted' : 'warning-text'">{{ root.message || (root.available ? '可用' : '不可用，请检查挂载和权限') }}</p></div><p>剩余 {{ bytes(root.free_bytes) }} / {{ bytes(root.total_bytes) }}</p></div><p class="field-hint">Telegram 缓存剩余 {{ bytes(liveSystem?.storage.cache.free_bytes ?? null) }} / {{ bytes(liveSystem?.storage.cache.total_bytes ?? null) }}。使用本地 API 下载时，缓存与保存目录均需足够空间。</p></div>
          <details class="settings-details"><summary>文件归档规则</summary><div class="details-content"><p>单文件直接保存在日期目录（年-月-日）；同一媒体组另建文字摘要与短编号命名的目录，图片和视频放在一起。同名文件不会覆盖。</p></div></details>
        </section>
        <section v-show="section === 'templates'" class="panel settings-section">
          <div class="section-heading"><div><h2>提示与命名</h2><p class="muted">仅新生成的确认卡生效，已有卡片、任务和重试保留原计划。</p></div></div>
          <div class="template-block">
            <div class="template-layout">
              <div class="template-editor">
                <el-form-item label="Bot 确认提示模板" label-for="confirmation-template" :error="examples.confirmationError">
                  <template #label><span class="setting-label">Bot 确认提示模板<SettingsSource :source="settings.sources.confirmation_template" :editable="editable('confirmation_template')" /></span></template>
                  <el-input id="confirmation-template" aria-label="Bot 确认提示模板" v-model="form.confirmation_template" type="textarea" :rows="5" :disabled="!editable('confirmation_template')" /><p class="field-hint">1–2000 个字符，可直接换行。确认、取消按钮由系统提供。</p>
                </el-form-item>
                <el-button class="template-reset" :disabled="!editable('confirmation_template')" @click="form.confirmation_template = defaultConfirmationTemplate">恢复默认提示</el-button>
              </div>
              <div class="template-preview"><div class="preview-heading"><strong>确认提示示例</strong><span>演示数据</span></div><pre data-testid="confirmation-preview">{{ examples.confirmationError ? '请修正模板后查看示例。' : examples.text }}</pre></div>
            </div>
            <dl class="template-variables" aria-label="确认提示变量"><div v-for="[key, description] in confirmationVariables" :key="key"><dt><code>{{ '{' + key + '}' }}</code></dt><dd>{{ description }}</dd></div></dl>
          </div>
          <div class="template-block">
            <div class="template-layout">
              <div class="template-editor">
                <el-form-item label="保存文件名模板" label-for="filename-template" :error="examples.fileError">
                  <template #label><span class="setting-label">保存文件名模板<SettingsSource :source="settings.sources.filename_template" :editable="editable('filename_template')" /></span></template>
                  <el-input id="filename-template" aria-label="保存文件名模板" v-model="form.filename_template" :disabled="!editable('filename_template')" /><p class="field-hint">1–160 个字符，只填名称。扩展名自动保留，归档目录不变。</p>
                </el-form-item>
                <el-button class="template-reset" :disabled="!editable('filename_template')" @click="form.filename_template = defaultFilenameTemplate">恢复默认文件名</el-button>
              </div>
              <div class="template-preview"><div class="preview-heading"><strong>文件名示例</strong><span>演示数据</span></div><pre data-testid="filename-preview">{{ examples.fileError ? '请修正模板后查看示例。' : examples.file }}</pre></div>
            </div>
            <dl class="template-variables" aria-label="文件名变量"><div v-for="[key, description] in filenameVariables" :key="key"><dt><code>{{ '{' + key + '}' }}</code></dt><dd>{{ description }}</dd></div></dl>
          </div>
          <details class="settings-details"><summary>命名示例与模板规则</summary><div class="details-content"><ul><li><code v-text="'{name}'" />：原文件名，缺失时用消息标题。</li><li><code v-text="'{date}_{name}'" />：收件日期与文件名称。</li><li><code v-text="'{date}_{type}_{index}'" />：日期、类型与序号。</li></ul><p>日期和时间采用请求创建时间与归档时区，并非拍摄时间。文件名优先使用原名，缺失时使用消息标题，再无标题则自动命名。同组共用消息标题；用标题命名的组内文件会加序号，模板已有序号变量时不重复添加。文件名会清理不支持的字符，过长时缩短；重名不会覆盖。</p><p>使用双花括号显示文字花括号，如 <code v-text="'{{name}}'" /> 显示为 <code v-text="'{name}'" />。恢复默认只修改表单，仍需保存设置。</p></div></details>
        </section>

        <section v-show="section === 'maintenance'" class="panel settings-section">
          <div class="section-heading"><div><h2>维护与备份</h2><p class="muted">管理日志，备份与恢复平台配置。</p></div></div>
          <div class="form-grid"><el-form-item label="日志保留天数" label-for="log-retention-days">
            <template #label><span class="setting-label">日志保留天数<SettingsSource :source="settings.sources.diagnostic_log_retention_days" :editable="editable('diagnostic_log_retention_days')" /></span></template>
            <el-input-number id="log-retention-days" aria-label="日志保留天数" v-model="form.diagnostic_log_retention_days" :min="0" :max="3650" :step="1" :disabled="!editable('diagnostic_log_retention_days')" />
            <p class="field-hint">默认 7 天，0 关闭按天清理。容量轮转始终有效，记录可能提前移除。</p>
          </el-form-item>
          <el-form-item label="每日自动清理时间" label-for="log-cleanup-time">
            <template #label><span class="setting-label">每日自动清理时间<SettingsSource :source="settings.sources.diagnostic_log_cleanup_time" :editable="editable('diagnostic_log_cleanup_time')" /></span></template>
            <el-input id="log-cleanup-time" aria-label="每日自动清理时间" type="time" v-model="form.diagnostic_log_cleanup_time" :disabled="!editable('diagnostic_log_cleanup_time')" />
            <p class="field-hint">默认 08:00，每日执行一次，使用归档时区 {{ form.timezone }}。保存后从下一个计划时间执行。</p>
          </el-form-item></div>
          <DiagnosticLogPanel :status="liveSystem?.diagnostic_log" :stale="!!statusError" :detailed="debug" @changed="diagnosticLogChanged" />
        </section>
      </el-form>
      <div v-show="section === 'maintenance'"><SettingsBackup v-if="settings && !loading" :dirty="dirty" :disabled="busy || syncing || conflict" @busy="backupBusy = $event" @restored="settingsRestored" /><section class="panel settings-section about-settings"><h2>开发者选项</h2><label class="debug-toggle"><span>显示调试详情</span><el-switch v-model="debug" aria-label="显示调试详情" /></label><p class="field-hint">仅在本次登录显示配置来源、服务实例与操作编号，便于排查问题；不会改变连接或日志设置。</p></section></div>
    </div>
    <div v-if="settings && !loading" class="save-bar" aria-label="设置操作">
      <div class="save-bar-content">
        <div class="save-actions"><span class="muted" aria-live="polite">{{ conflict ? '版本冲突 · 请重新读取' : dirty ? '有尚未保存的修改' : debug ? '配置版本 ' + settings.revision + ' · 已保存' : '所有修改已保存' }}</span><div class="save-buttons"><el-button :disabled="busy || syncing || backupBusy" @click="reload">重新读取</el-button><el-button :loading="busy" :disabled="syncing || conflict || backupBusy || !!validationBlocked" @click="validate">校验配置</el-button><el-button type="primary" native-type="submit" form="settings-form" :loading="busy" :disabled="syncing || !dirty || conflict || backupBusy">保存设置</el-button></div></div>
        <p v-if="validationBlocked" class="save-hint" role="status">{{ validationBlocked }}</p>
        <p class="save-hint">保存无需联网；校验检查当前输入的连接，不会保存。</p>
      </div>
    </div>
  </section>
</template>
