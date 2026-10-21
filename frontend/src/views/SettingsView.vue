<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ApiError, errorMessage, request } from '../api/client'
import { parseUserIds } from '../api/format'
import { confirmationVariables, defaultConfirmationTemplate, defaultFilenameTemplate, filenameVariables, templateExamples } from '../api/templates'
import type { LocalApiStatus, Settings, SettingsValues, StorageRoot, SystemStatus } from '../api/types'
import { usePolling } from '../composables/usePolling'
import SettingsSource from '../components/SettingsSource.vue'
import LocalApiPanel from '../components/LocalApiPanel.vue'

const emit = defineEmits<{ dirty: [value: boolean] }>()
const settings = ref<Settings>()
const roots = ref<StorageRoot[]>([])
const form = reactive<SettingsValues>({
  bot_api_mode: 'cloud',
  telegram_api_id: '', telegram_api_hash: '',
  confirmation_template: defaultConfirmationTemplate, filename_template: defaultFilenameTemplate,
  bot_token: '', allowed_user_ids: [], confirmation_timeout_seconds: 1800, download_concurrency: 3,
  max_auto_retries: 3, download_root_id: 'default', timezone: 'Asia/Shanghai',
  diagnostic_log_retention_days: 7,
  proxy: { enabled: false, scheme: 'socks5', host: '', port: 1080, username: '', password: '' },
})
const userIds = ref('')
const clearBotToken = ref(false)
const clearProxyPassword = ref(false)
const clearApiHash = ref(false)
const busy = ref(false)
const loading = ref(true)
const error = ref('')
const conflict = ref(false)
type ValidationResult = { warnings: string[]; bot: { id: number; username: string } | null }
const snapshot = ref('')
const { data: liveSystem, error: statusError, refresh: refreshStatus } = usePolling(signal => request<SystemStatus>('/system/status', { signal }), 3000)
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
const proxyLabels: Record<string, string> = { disabled: '未启用', pending: '等待应用', applying: '正在应用', active: '已应用', failed: '应用失败', restart_required: '需要重启网络组件' }
const proxyMessages: Record<string, string> = { disabled: '当前未启用代理。', pending: '代理变更等待后台确认，尚未生效。', applying: '正在应用代理配置，请稍候。', active: '代理配置已应用。', failed: '代理配置应用失败，请检查连接与部署状态。', restart_required: '请按部署指南重启或重建网络组件。' }
const current = computed(() => JSON.stringify({ form, userIds: userIds.value, clearBotToken: clearBotToken.value, clearProxyPassword: clearProxyPassword.value, clearApiHash: clearApiHash.value }))
const dirty = computed(() => !!snapshot.value && snapshot.value !== current.value)
const examples = computed(() => templateExamples(form.confirmation_template, form.filename_template, form.confirmation_timeout_seconds))
// Displaying legacy seconds must not round the value or mark the form dirty.
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
    const [value, system] = await Promise.all([
      request<Settings>('/settings'), request<SystemStatus>('/system/status'),
    ])
    roots.value = system.storage.roots; assign(value)
  } catch (cause) { error.value = errorMessage(cause); ElMessage.error(error.value) }
  finally { loading.value = false }
}
function payload() {
  if (!Number.isInteger(form.diagnostic_log_retention_days) || form.diagnostic_log_retention_days < 0 || form.diagnostic_log_retention_days > 3650) throw new Error('诊断日志保留天数须为 0–3650 的整数。')
  if (examples.value.confirmationError || examples.value.fileError) throw new Error(examples.value.confirmationError || examples.value.fileError)
  if (form.telegram_api_id && !/^[1-9]\d*$/.test(form.telegram_api_id)) throw new Error('API ID 请填写正整数，或留空。')
  const values = { ...form, allowed_user_ids: parseUserIds(userIds.value), proxy: { ...form.proxy } }
  const changed: Record<string, unknown> = {}
  for (const key of Object.keys(values) as (keyof SettingsValues)[]) {
    if (editable(key) && JSON.stringify(values[key]) !== JSON.stringify(settings.value?.values[key])) changed[key] = values[key]
  }
  if (clearBotToken.value && editable('bot_token')) changed.clear_bot_token = true
  if (clearProxyPassword.value && editable('proxy')) changed.clear_proxy_password = true
  if (clearApiHash.value && editable('telegram_api_hash')) changed.clear_telegram_api_hash = true
  return changed
}
async function save() {
  if (!settings.value || busy.value || conflict.value) return
  busy.value = true; error.value = ''
  try {
    const saved = await request<Settings>('/settings', { method: 'PATCH', body: JSON.stringify({ revision: settings.value.revision, ...payload() }) })
    assign(saved)
    // Prefer this response until the diagnostic poll catches up to the saved revision.
    if (liveSystem.value) liveSystem.value.proxy = saved.proxy_status
    void refreshStatus()
    ElMessage.success('设置已保存，请查看各项生效状态。')
  } catch (cause) {
    error.value = errorMessage(cause)
    if (cause instanceof ApiError && cause.code === 'REVISION_CONFLICT') {
      conflict.value = true
      ElMessage({ type: 'warning', message: '配置已被其他会话修改。你的输入已保留，请先重新读取，再应用修改。', duration: 8000, showClose: true })
    } else ElMessage({ type: 'error', message: error.value, duration: 6000, showClose: true })
  } finally { busy.value = false }
}
async function validate() {
  if (busy.value || conflict.value) return
  busy.value = true; error.value = ''
  const checked = current.value
  try {
    const result = await request<ValidationResult>('/settings/validate', { method: 'POST', body: JSON.stringify(payload()) })
    if (checked === current.value) {
      const summary = result.bot ? '校验通过，Bot：@' + result.bot.username : '配置校验通过（未配置 Bot）'
      ElMessage({ type: result.warnings.length ? 'warning' : 'success', message: [summary, ...result.warnings, '校验不会保存配置。'].join('；'), duration: result.warnings.length ? 8000 : 4000, showClose: true })
    } else ElMessage.warning('校验期间配置已修改，请重新校验当前输入。')
  }
  catch (cause) { ElMessage({ type: 'error', message: checked === current.value ? errorMessage(cause) : '校验期间配置已修改，请重新校验当前输入。', duration: 6000, showClose: true }) }
  finally { busy.value = false }
}
async function reload() {
  if (dirty.value) {
    try { await ElMessageBox.confirm('重新读取将放弃尚未保存的修改。', '重新读取配置', { confirmButtonText: '放弃并读取', cancelButtonText: '保留修改' }) }
    catch { return }
  }
  await load()
}
onMounted(load)
</script>
<template>
  <section class="settings-view">
    <div class="settings-scroll">
      <div class="page-heading"><div><p class="eyebrow">PREFERENCES</p><h1>设置</h1><p class="muted">管理 Bot 连接、保存规则与下载偏好。</p></div><el-button :disabled="busy" @click="reload">重新读取</el-button></div>
      <el-skeleton v-if="loading" :rows="8" animated />
      <el-form v-else-if="settings" label-position="top" id="settings-form" class="settings-form" :disabled="busy" novalidate @submit.prevent="save">
        <section class="panel settings-section">
          <div class="section-heading"><span class="section-number">01</span><div><h2>Telegram 连接</h2><p class="muted">选择 API 服务，设置 Bot 与允许使用的人。</p></div></div>
          <el-form-item label="Bot API 服务" label-for="bot-api-mode">
            <template #label><span class="setting-label">Bot API 服务<SettingsSource :source="settings.sources.bot_api_mode" :editable="editable('bot_api_mode')" /></span></template>
            <el-select id="bot-api-mode" aria-label="Bot API 服务" v-model="form.bot_api_mode" :disabled="!editable('bot_api_mode')"><el-option label="Telegram 官方云端" value="cloud" /><el-option label="本地 Bot API" value="local" /></el-select>
            <p v-if="form.bot_api_mode === 'cloud'" class="field-hint mode-hint">只需 Bot Token，无需 API ID / Hash。单个文件最多下载 <strong>20 MB</strong>。</p>
            <p v-else class="field-hint mode-hint">支持超过 20 MB 的文件。{{ liveSystem?.local_api?.managed ? '填写下方 API ID / Hash 并保存，后台将协调启动和切换。' : '需由部署环境配置 API ID / Hash 并启动本地服务；从云端迁移须完成官方 logOut 流程。' }}</p>
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="Bot Token" label-for="bot-token">
              <template #label><span class="setting-label">Bot Token<SettingsSource :source="settings.sources.bot_token" :editable="editable('bot_token')" /></span></template>
              <el-input id="bot-token" aria-label="Bot Token" v-model="form.bot_token" type="password" show-password autocomplete="new-password" :disabled="!editable('bot_token') || clearBotToken" :placeholder="settings.configured.bot_token ? '已配置 · 留空保持不变' : '填写 @BotFather 提供的 Token'" />
              <p class="field-hint">从 @BotFather 获取。已保存的 Token 留空不变。</p>
              <el-checkbox v-if="settings.configured.bot_token && editable('bot_token')" v-model="clearBotToken">清除已保存的 Token</el-checkbox>
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
            <p v-if="settings.sources.telegram_api_id === 'deployment_fallback' || settings.sources.telegram_api_hash === 'deployment_fallback'" class="field-hint">当前使用部署凭据回退，可在此填写新值覆盖；已保存的 WebUI 配置优先。</p>
            <div class="form-grid">
              <el-form-item label="API ID" label-for="telegram-api-id"><template #label><span class="setting-label">API ID<SettingsSource :source="settings.sources.telegram_api_id" :editable="editable('telegram_api_id')" /></span></template><el-input id="telegram-api-id" aria-label="API ID" v-model="form.telegram_api_id" inputmode="numeric" autocomplete="off" :disabled="!editable('telegram_api_id')" placeholder="填写数字 API ID" /></el-form-item>
              <el-form-item label="API Hash" label-for="telegram-api-hash"><template #label><span class="setting-label">API Hash<SettingsSource :source="settings.sources.telegram_api_hash" :editable="editable('telegram_api_hash')" /></span></template><el-input id="telegram-api-hash" aria-label="API Hash" v-model="form.telegram_api_hash" type="password" show-password autocomplete="new-password" :disabled="!editable('telegram_api_hash') || clearApiHash" :placeholder="settings.configured.telegram_api_hash ? '已配置 · 留空保持不变' : '填写 API Hash'" /><p class="field-hint">已保存的 Hash 不回显，留空保持不变。</p><el-checkbox v-if="settings.configured.telegram_api_hash && editable('telegram_api_hash')" v-model="clearApiHash">清除已保存的 API Hash</el-checkbox></el-form-item>
            </div>
            <p v-if="liveSystem?.local_api && !liveSystem.local_api.managed" class="field-hint">当前 API 由外部管理，保存这些凭据不会修改外部进程；需先升级为配套管理组件。</p>
          </div>
          <LocalApiPanel :status="liveSystem?.local_api" :bot="liveSystem?.bot" :disabled="busy || dirty || conflict" :stale="!!statusError" @changed="localApiChanged" />
          <p v-if="dirty" class="field-hint">请先保存或放弃修改，再进行服务操作。</p>
          <details class="settings-details"><summary>连接与切换须知</summary><div class="details-content"><p>保存与连接校验分开；已保存选择与实际使用可能暂时不同，请查看服务状态。切换期间 Bot 会暂停接收，已排队任务会保留。</p><p v-if="liveSystem?.local_api?.managed">后台等待下载安全结束后协调切换；云端退出后回切需等待至少 10 分钟。应用与 WebUI 持续运行；其他程序须停止使用同一个 Bot。</p><p v-else>当前为外部管理模式，需按部署指南启动本地服务、执行云端退出。WebUI 不创建或重启容器。</p><p>更换 Bot 身份前，请结束原 Bot 的活动和待确认请求。本地代理仍需部署配套 TUN 网络组件。</p></div></details>
        </section>
        <section class="panel settings-section">
          <div class="section-heading"><span class="section-number">02</span><div><h2>下载与保存</h2><p class="muted">后续任务使用新设置，已有任务保留保存位置。</p></div></div>
          <div class="form-grid">
            <el-form-item label="下载根目录" label-for="download-root">
              <template #label><span class="setting-label">下载根目录<SettingsSource :source="settings.sources.download_root_id" :editable="editable('download_root_id')" /></span></template>
              <el-select id="download-root" aria-label="下载根目录" v-model="form.download_root_id" :disabled="!editable('download_root_id')"><el-option v-for="root in roots" :key="root.id" :value="root.id" :label="root.label + (root.available ? '' : '（不可用）')" /><el-option v-if="!roots.some(root => root.id === form.download_root_id)" :value="form.download_root_id" :label="form.download_root_id" /></el-select>
              <p class="field-hint">可选目录由部署挂载决定。修改后，旧待确认卡片需重新确认。</p>
            </el-form-item>
            <el-form-item label="归档时区" label-for="timezone">
              <template #label><span class="setting-label">归档时区<SettingsSource :source="settings.sources.timezone" :editable="editable('timezone')" /></span></template>
              <el-input id="timezone" aria-label="归档时区" v-model="form.timezone" :disabled="!editable('timezone')" placeholder="Asia/Shanghai" />
              <p class="field-hint">用于归档日期与文件名时间，例如 Asia/Shanghai。</p>
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
          <details class="settings-details"><summary>文件归档规则</summary><div class="details-content"><p>单文件直接保存在日期目录（年-月-日）；同一媒体组另建文字摘要与短编号命名的目录，图片和视频放在一起。同名文件不会覆盖。</p></div></details>
        </section>
        <section class="panel settings-section">
          <div class="section-heading"><span class="section-number">03</span><div><h2>提示与文件命名</h2><p class="muted">仅新生成的确认卡生效，已有卡片、任务和重试保留原计划。</p></div></div>
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
            <details class="settings-details"><summary>旧模板兼容说明</summary><div class="details-content"><p><code v-text="'{expires_in}'" /> 保留为秒数，兼容已有自定义提示。新模板建议使用 <code v-text="'{expires_in_minutes}'" /> 并标注“分钟”。</p></div></details>
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
          <details class="settings-details"><summary>命名示例与模板规则</summary><div class="details-content"><ul><li><code v-text="'{name}'" />：保留原名。</li><li><code v-text="'{date}_{name}'" />：收件日期与原名。</li><li><code v-text="'{date}_{type}_{index}'" />：日期、类型与序号。</li></ul><p>日期和时间采用请求创建时间与归档时区，并非拍摄时间。文件名会清理不支持的字符，过长时缩短；重名不会覆盖。</p><p>使用双花括号显示文字花括号，如 <code v-text="'{{name}}'" /> 显示为 <code v-text="'{name}'" />。恢复默认只修改表单，仍需保存设置。</p></div></details>
        </section>
        <section class="panel settings-section">
          <div class="section-heading"><span class="section-number">04</span><div><h2>网络代理</h2><p class="muted">用于 Telegram 连接，WebUI 内网访问保持直连。</p></div></div>
          <el-form-item label="启用代理" label-for="proxy-enabled">
            <template #label><span class="setting-label">启用代理<SettingsSource :source="settings.sources.proxy" :editable="editable('proxy')" /></span></template>
            <el-switch id="proxy-enabled" aria-label="启用代理" v-model="form.proxy.enabled" :disabled="!editable('proxy')" /><span class="switch-label">{{ form.proxy.enabled ? '已开启（当前表单）' : '已关闭（当前表单）' }}</span>
          </el-form-item>
          <div v-if="form.proxy.enabled" class="form-grid"><el-form-item label="协议" label-for="proxy-scheme"><el-select id="proxy-scheme" v-model="form.proxy.scheme" :disabled="!editable('proxy')"><el-option label="SOCKS5" value="socks5" /><el-option label="HTTP CONNECT" value="http" /></el-select></el-form-item><el-form-item label="代理地址" label-for="proxy-host"><el-input id="proxy-host" v-model="form.proxy.host" :disabled="!editable('proxy')" placeholder="代理服务的 IP 或域名" /></el-form-item><el-form-item label="端口" label-for="proxy-port"><el-input-number id="proxy-port" v-model="form.proxy.port" :min="1" :max="65535" :disabled="!editable('proxy')" /></el-form-item><el-form-item label="用户名（可选）" label-for="proxy-username"><el-input id="proxy-username" v-model="form.proxy.username" :disabled="!editable('proxy')" autocomplete="off" /></el-form-item><el-form-item label="密码（可选）" label-for="proxy-password"><el-input id="proxy-password" v-model="form.proxy.password" type="password" show-password autocomplete="new-password" :disabled="!editable('proxy') || clearProxyPassword" :placeholder="settings.configured.proxy_password ? '已配置 · 留空保持不变' : '无认证可留空'" /><p class="field-hint">已保存的密码留空不变。</p><el-checkbox v-if="settings.configured.proxy_password && editable('proxy')" v-model="clearProxyPassword">清除已保存的代理密码</el-checkbox></el-form-item></div>
          <p class="field-hint proxy-mode-hint">{{ form.bot_api_mode === 'cloud' ? '启用后，云端由应用直接使用代理，无需 TUN。保存后请等待连接确认。' : '本地模式需配套 TUN 网络组件；仅填写代理不会改变 Docker 网络。' }}</p>
          <div class="proxy-saved-state"><div class="proxy-state-heading"><strong>已保存配置</strong><span class="proxy-state-label">{{ proxyLabels[proxyStatus?.state ?? ''] || proxyStatus?.state || '等待确认' }}</span></div><el-alert :title="proxyStatus?.message || proxyMessages[proxyStatus?.state ?? ''] || '代理状态等待后台确认。'" :type="proxyStatus?.state === 'active' ? 'success' : proxyStatus?.state === 'failed' ? 'error' : proxyStatus?.state === 'restart_required' ? 'warning' : 'info'" :closable="false" show-icon /><details class="settings-details"><summary>查看应用版本与状态说明</summary><div class="details-content"><p>期望版本 {{ proxyStatus?.desired_revision ?? '—' }} / 已应用版本 {{ proxyStatus?.applied_revision ?? '—' }}</p><p>以上为已保存配置的后台状态，不包含尚未保存的修改；已应用也不代表 Bot 已连接。</p></div></details></div>
        </section>
        <section class="panel settings-section">
          <div class="section-heading"><span class="section-number">05</span><div><h2>诊断日志</h2><p class="muted">自动清理过期记录，保留空间上限。</p></div></div>
          <el-form-item label="日志保留天数" label-for="log-retention-days">
            <template #label><span class="setting-label">日志保留天数<SettingsSource :source="settings.sources.diagnostic_log_retention_days" :editable="editable('diagnostic_log_retention_days')" /></span></template>
            <el-input-number id="log-retention-days" aria-label="日志保留天数" v-model="form.diagnostic_log_retention_days" :min="0" :max="3650" :step="1" :disabled="!editable('diagnostic_log_retention_days')" />
            <p class="field-hint">默认 7 天，0 关闭按天清理。容量轮转始终有效，记录可能提前移除；保存后最迟一小时内清理过期记录。</p>
          </el-form-item>
          <p class="field-hint">在系统状态页可导出或手动清理诊断日志，不影响任务、媒体文件或连接配置。</p>
        </section>
      </el-form>
    </div>
    <div v-if="settings && !loading" class="save-bar" aria-label="设置操作">
      <div class="save-bar-content">
        <div class="save-actions"><span class="muted" aria-live="polite">{{ conflict ? '版本冲突 · 请重新读取' : dirty ? '有尚未保存的修改' : '配置版本 ' + settings.revision + ' · 已保存' }}</span><div class="save-buttons"><el-button :loading="busy" :disabled="conflict" @click="validate">校验配置</el-button><el-button type="primary" native-type="submit" form="settings-form" :loading="busy" :disabled="!dirty || conflict">保存设置</el-button></div></div>
        <p class="save-hint">保存无需联网；校验检查当前输入的连接，不会保存。</p>
      </div>
    </div>
  </section>
</template>
