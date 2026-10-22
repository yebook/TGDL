<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { errorMessage, isAbort, request } from '../api/client'
import type { Settings, SettingsBackupEnvelope, SettingsBackupPreview } from '../api/types'

const props = defineProps<{ dirty: boolean; disabled: boolean }>()
const emit = defineEmits<{ restored: [settings: Settings]; busy: [value: boolean] }>()
const dialog = ref(false)
const mode = ref<'export' | 'restore'>('export')
const password = ref('')
const repeatedPassword = ref('')
const backup = ref<SettingsBackupEnvelope>()
const preview = ref<SettingsBackupPreview>()
const fileName = ref('')
const busy = ref(false)
const fileInput = ref<HTMLInputElement>()
const validPassword = computed(() => Array.from(password.value).length >= 6 && Array.from(password.value).length <= 128)
let controller: AbortController | undefined
let mounted = true
let objectUrl: string | undefined
let fileGeneration = 0
const labels: Record<string, string> = {
  bot_api_mode: 'API 服务', bot_token: 'Bot Token', allowed_user_ids: '允许使用的用户',
  telegram_api_id: 'API ID', telegram_api_hash: 'API Hash', proxy: '网络代理',
  confirmation_template: '确认提示模板', filename_template: '文件名模板',
  confirmation_timeout_seconds: '确认有效期', download_concurrency: '下载并发',
  max_auto_retries: '自动重试', download_root_id: '下载根目录', timezone: '归档时区',
  diagnostic_log_retention_days: '日志保留天数', diagnostic_log_cleanup_time: '日志清理时间',
}
const fieldNames = (fields: string[]) => fields.map(field => labels[field] ?? field).join('、')
function reset() {
  fileGeneration++
  password.value = ''; repeatedPassword.value = ''; backup.value = undefined
  preview.value = undefined; fileName.value = ''
  if (fileInput.value) fileInput.value.value = ''
}
function close() {
  controller?.abort(); reset()
}
function open(value: 'export' | 'restore') {
  reset(); mode.value = value; dialog.value = true
}
async function selectFile(event: Event) {
  const generation = ++fileGeneration
  backup.value = undefined; preview.value = undefined; fileName.value = ''
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 128 * 1024) { ElMessage.error('备份文件不能超过 128 KiB。'); return }
  try {
    const value: unknown = JSON.parse(await file.text())
    if (!mounted || !dialog.value || generation !== fileGeneration) return
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('请选择有效的 TGDL 配置备份文件。')
    backup.value = value as SettingsBackupEnvelope; fileName.value = file.name
  } catch { if (mounted && generation === fileGeneration) ElMessage.error('无法读取备份，请选择有效的 JSON 备份文件。') }
}
async function run(action: 'export' | 'preview' | 'restore') {
  if (busy.value || props.disabled || !validPassword.value) return
  if (action === 'export' && password.value !== repeatedPassword.value) { ElMessage.warning('两次输入的备份密码不一致。'); return }
  if (action !== 'export' && (!backup.value || props.dirty)) return
  if (action === 'restore' && !preview.value) return
  busy.value = true; emit('busy', true); controller = new AbortController()
  try {
    const body = action === 'export' ? { password: password.value }
      : { password: password.value, backup: backup.value, ...(action === 'restore' ? { revision: preview.value!.revision } : {}) }
    if (action === 'export') {
      const result = await request<{ backup: SettingsBackupEnvelope; excluded_fields: string[] }>('/settings/backup/export', { method: 'POST', body: JSON.stringify(body), signal: controller.signal })
      if (!mounted) return
      objectUrl = URL.createObjectURL(new Blob([JSON.stringify(result.backup, null, 2)], { type: 'application/json' }))
      const link = document.createElement('a'); link.href = objectUrl
      link.download = `tgdl-settings-${new Date().toISOString().replace(/[:.]/g, '-')}.json`
      link.click(); URL.revokeObjectURL(objectUrl); objectUrl = undefined
      if (result.excluded_fields.length) ElMessage({ type: 'warning', message: `加密配置已导出。部署环境控制的配置未包含：${fieldNames(result.excluded_fields)}。请妥善保管备份和密码。`, duration: 8000, showClose: true })
      else ElMessage.success('加密配置已导出，请妥善保管备份文件和密码。')
      dialog.value = false; reset()
    } else if (action === 'preview') {
      const result = await request<SettingsBackupPreview>('/settings/backup/preview', { method: 'POST', body: JSON.stringify(body), signal: controller.signal })
      if (mounted) preview.value = result
    } else {
      const result = await request<Settings>('/settings/backup/restore', { method: 'POST', body: JSON.stringify(body), signal: controller.signal })
      if (!mounted) return
      emit('restored', result); dialog.value = false; reset()
      ElMessage.success('配置已恢复。连接变更由后台应用，请查看服务状态。')
    }
  } catch (cause) {
    if (mounted && !isAbort(cause)) {
      if (action === 'restore') preview.value = undefined
      ElMessage({ type: 'error', message: errorMessage(cause), duration: 7000, showClose: true })
    }
  } finally {
    if (mounted) { busy.value = false; emit('busy', false) }
  }
}
onUnmounted(() => { mounted = false; controller?.abort(); reset(); if (objectUrl) URL.revokeObjectURL(objectUrl) })
</script>

<template>
  <section class="panel settings-section backup-section" aria-label="配置备份与恢复">
    <div class="section-heading"><div><h2>配置备份与恢复</h2><p class="muted">迁移或保留一份平台设置，使用独立密码加密。</p></div></div>
    <p class="field-hint">包含已保存的 Bot Token、API Hash 和代理密码。不包含管理员账号、会话、任务记录、数据库、媒体文件、日志、缓存及服务运行状态。</p>
    <div class="backup-actions"><el-button :disabled="disabled || busy" @click="open('export')">导出配置备份</el-button><el-button :disabled="disabled || busy || dirty" @click="open('restore')">恢复配置备份</el-button></div>
    <p v-if="dirty" class="field-hint">导出仅包含已保存的配置。恢复前请先保存修改，或使用底部的“重新读取”放弃修改。</p>
    <el-dialog v-model="dialog" class="backup-dialog" :title="mode === 'export' ? '导出配置备份' : '恢复配置备份'" width="min(560px, calc(100vw - 32px))" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy" @closed="close">
      <p class="field-hint">{{ mode === 'export' ? '导出当前已保存的配置，包含密钥。请记住备份密码，遗失后无法恢复。' : '选择加密备份并输入密码，先预览再确认。预览不会更改配置或连接 Telegram。' }}</p>
      <el-form label-position="top" :disabled="busy" @submit.prevent="run(mode === 'export' ? 'export' : 'preview')">
        <el-form-item v-if="mode === 'restore'" label="配置备份文件" label-for="settings-backup-file"><input id="settings-backup-file" ref="fileInput" class="backup-file" type="file" accept=".json,application/json" @change="selectFile" /><p v-if="fileName" class="field-hint">{{ fileName }}</p></el-form-item>
        <el-form-item label="备份密码" label-for="settings-backup-password"><el-input id="settings-backup-password" v-model="password" type="password" show-password :maxlength="128" autocomplete="new-password" @input="preview = undefined" /><p class="field-hint">6–128 个字符，与管理员登录密码相互独立。</p></el-form-item>
        <el-form-item v-if="mode === 'export'" label="再次输入备份密码" label-for="settings-backup-password-repeat"><el-input id="settings-backup-password-repeat" v-model="repeatedPassword" type="password" show-password :maxlength="128" autocomplete="new-password" /></el-form-item>
        <div v-if="preview" class="backup-preview" aria-label="备份预览" aria-live="polite">
          <h3>恢复预览</h3>
          <p class="warning-text">将覆盖可编辑配置，备份中的空密钥也会清除当前密钥。不会恢复管理员、任务、数据库、媒体、日志、缓存或服务运行状态；API 与代理变更可能暂时中断 Bot 连接。</p>
          <p>备份时间：{{ new Date(preview.created_at).toLocaleString('zh-CN', { hour12: false }) }}</p>
          <p>API：{{ preview.summary.bot_api_mode === 'cloud' ? '官方云端' : '本地 Bot API' }} · 下载并发：{{ preview.summary.download_concurrency }} · 允许用户：{{ preview.summary.allowed_user_count }}</p>
          <p>下载根目录：{{ preview.summary.download_root_id }}</p>
          <p>将恢复：{{ fieldNames(preview.included_fields) || '无' }}</p>
          <p v-if="preview.skipped_fields.length">保留目标当前值，跳过：{{ fieldNames(preview.skipped_fields) }}</p>
          <p>恢复后密钥状态：Bot Token {{ preview.configured.bot_token ? '已配置' : '为空' }}；API Hash {{ preview.configured.telegram_api_hash ? '已配置' : '为空' }}；代理密码 {{ preview.configured.proxy_password ? '已配置' : '为空' }}。</p>
          <p v-for="warning in preview.warnings" :key="warning" class="warning-text">{{ warning }}</p>
        </div>
      </el-form>
      <template #footer><el-button :disabled="busy" @click="dialog = false">取消</el-button><el-button v-if="mode === 'export'" type="primary" :loading="busy" :disabled="!validPassword || !repeatedPassword" @click="run('export')">加密并下载</el-button><el-button v-else-if="!preview" type="primary" :loading="busy" :disabled="!validPassword || !backup || dirty" @click="run('preview')">预览备份</el-button><el-button v-else type="danger" :loading="busy" :disabled="dirty" @click="run('restore')">确认覆盖配置</el-button></template>
    </el-dialog>
  </section>
</template>

<style scoped>
.backup-section { max-width: 1060px; margin-left: auto; margin-right: auto; }
.backup-actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 16px; }
.backup-actions :deep(.el-button + .el-button) { margin-left: 0; }
.backup-file { max-width: 100%; font: inherit; }
.backup-preview { padding: 16px; border: 1px solid var(--el-border-color); border-radius: 10px; }
.backup-preview p { overflow-wrap: anywhere; line-height: 1.7; }
:deep(.backup-dialog) { display: flex; flex-direction: column; max-height: calc(100dvh - 48px); margin-top: 24px; margin-bottom: 24px; }
:deep(.backup-dialog .el-dialog__body) { overflow-y: auto; min-height: 0; }
:deep(.backup-dialog .el-dialog__header), :deep(.backup-dialog .el-dialog__footer) { flex-shrink: 0; }
</style>
