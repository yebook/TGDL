<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ApiError, errorMessage } from '../api/client'
import { boundedRequest } from '../api/boundedRequest'
import { bytes } from '../api/format'
import type { CacheStatus } from '../api/types'

const props = defineProps<{ status?: CacheStatus; active: boolean; available?: boolean; stale?: boolean; disabled?: boolean; draft?: boolean }>()
const emit = defineEmits<{ changed: [status: CacheStatus]; sync: [] }>()
const busy = ref<'refresh' | 'clean' | null>(null)
const refreshError = ref('')
const outcomeUnknown = ref(false)
const awaitingOperation = ref<string | null>(null)
let previousOperation: string | null = null
let pendingResult: string | null = null
let opened = false
let confirming = false
const controller = new AbortController()
onUnmounted(() => { controller.abort(); if (confirming) ElMessageBox.close() })
const running = computed(() => props.status?.maintenance.state === 'running' || !!awaitingOperation.value)
const cleanReason = computed(() => {
  if (outcomeUnknown.value) return '清理结果尚未确认，请刷新占用查询；确认服务空闲后可重新载入页面。持续无法确认时请在连接设置中重启本地 API。'
  if (running.value) return '正在清理媒体缓存，请等待完成。'
  if (busy.value) return busy.value === 'refresh' ? '正在刷新缓存占用，请稍候。' : '请先完成当前清理操作。'
  if (props.disabled) return '请先完成当前设置操作，再清理缓存。'
  if (props.stale) return '服务状态未更新，请刷新状态后再清理缓存。'
  if (!props.status?.maintenance.can_clean) return props.status?.maintenance.message || '请先刷新缓存占用。'
  return ''
})
const cleanDisabled = computed(() => !!cleanReason.value)
const usage = computed(() => props.status?.used_bytes == null ? '—' : bytes(props.status.used_bytes))
watch(() => props.status, status => {
  if (status?.maintenance.operation_id === awaitingOperation.value) awaitingOperation.value = null
  const maintenance = status?.maintenance
  if (maintenance?.state === 'running' && maintenance.operation_id) pendingResult = maintenance.operation_id
  if (maintenance?.operation_id && maintenance.operation_id === pendingResult
      && ['succeeded', 'failed'].includes(maintenance.state) && maintenance.blocked_reason !== 'maintenance_running') {
    pendingResult = null
    const succeeded = maintenance.state === 'succeeded'
    ElMessage({ type: succeeded ? 'success' : 'error',
      message: succeeded ? '缓存清理完成' : maintenance.blocked_reason ? '缓存清理未完成，请刷新占用后重试。' : maintenance.message,
      duration: 6000, showClose: true })
  }
})
watch(() => props.active, active => { if (!active && confirming) ElMessageBox.close() })
watch(() => [props.active, props.status, props.stale, props.available, props.disabled] as const, () => {
  if (!opened && props.active && props.available && props.status && !props.stale && !props.disabled && props.status.usage_state !== 'unsupported') {
    opened = true
    void refresh(true)
  }
}, { immediate: true })
async function refresh(automatic = false) {
  if (busy.value || props.disabled || controller.signal.aborted) return
  busy.value = 'refresh'
  refreshError.value = ''
  try {
    const status = await boundedRequest<CacheStatus>('/system/cache/refresh', { method: 'POST', signal: controller.signal })
    if (controller.signal.aborted) return
    // An old idle snapshot cannot acknowledge a clear whose response was lost.
    if (outcomeUnknown.value && status.maintenance.operation_id && status.maintenance.operation_id !== previousOperation) outcomeUnknown.value = false
    emit('changed', status)
  } catch (cause) {
    if (!controller.signal.aborted) {
      refreshError.value = errorMessage(cause)
      if (!automatic) ElMessage({ type: 'error', message: refreshError.value, duration: 6000, showClose: true })
    }
  } finally { busy.value = null }
}
async function clean() {
  if (cleanDisabled.value || controller.signal.aborted) return
  busy.value = 'clean'
  confirming = true
  try {
    try {
      await ElMessageBox.confirm(`将清理当前 Bot 的媒体缓存，包括已取消或失败下载的残留。已保存文件和账号数据会保留；缓存清理后无法恢复，再次下载需重新获取。${props.draft ? '本次操作使用已保存的连接，未保存设置会保留。' : ''}`, '清理媒体缓存', { type: 'warning', confirmButtonText: '确认清理', cancelButtonText: '取消' })
    } catch { return }
    finally { confirming = false }
    if (controller.signal.aborted || !props.active || props.stale || props.disabled || !props.status?.maintenance.can_clean) return
    previousOperation = props.status.maintenance.operation_id
    const status = await boundedRequest<CacheStatus>('/system/cache/clean', { method: 'POST', signal: controller.signal })
    if (controller.signal.aborted) return
    pendingResult = status.maintenance.operation_id
    if (status.maintenance.state === 'running') awaitingOperation.value = status.maintenance.operation_id
    emit('changed', status)
    ElMessage.info('缓存清理已提交。')
  } catch (cause) {
    if (controller.signal.aborted) return
    if (!(cause instanceof ApiError) || cause.status === 0 || cause.status >= 500) outcomeUnknown.value = true
    ElMessage({ type: 'error', message: errorMessage(cause), duration: 6000, showClose: true })
    emit('sync')
  } finally { busy.value = null }
}
</script>
<template>
  <div class="cache-panel" data-testid="cache-storage-panel" :aria-busy="!!busy || running">
    <h3>Telegram 媒体缓存</h3>
    <p class="cache-usage" aria-live="polite">缓存占用 {{ usage }}<span v-if="status?.file_count != null"> · {{ status.file_count }} 个文件</span></p>
    <p v-if="refreshError || status?.refresh_error" class="warning-text" role="alert">{{ refreshError || status?.refresh_error }}</p>
    <div class="cache-actions">
      <el-button :loading="busy === 'refresh'" :disabled="!!busy || disabled" @click="refresh()">刷新占用</el-button>
      <el-tooltip :content="cleanReason" :disabled="!cleanDisabled" :trigger="['hover', 'focus']" placement="top">
        <span class="cache-clean-control" :tabindex="cleanDisabled ? 0 : undefined" :aria-label="cleanDisabled ? `清理媒体缓存：${cleanReason}` : undefined" :title="cleanReason || undefined">
          <el-button type="danger" plain :loading="busy === 'clean' || running" :disabled="cleanDisabled" :aria-label="cleanReason ? `清理媒体缓存：${cleanReason}` : undefined" @click="clean">{{ running ? '正在清理' : '清理媒体缓存' }}</el-button>
        </span>
      </el-tooltip>
    </div>
  </div>
</template>
<style scoped>
.cache-panel { margin-top: 24px; border-top: 1px solid var(--el-border-color-lighter); padding-top: 12px; }
.cache-usage { margin: 12px 0; }
.cache-clean-control { display: inline-flex; }
.cache-clean-control:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 3px; border-radius: 4px; }
.cache-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.cache-actions .el-button { margin-left: 0; min-height: 44px; }
</style>
