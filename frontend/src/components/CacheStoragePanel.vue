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
const outcomeUnknown = ref(false)
const awaitingOperation = ref<string | null>(null)
let previousOperation: string | null = null
let opened = false
let confirming = false
const controller = new AbortController()
onUnmounted(() => { controller.abort(); if (confirming) ElMessageBox.close() })
const running = computed(() => props.status?.maintenance.state === 'running' || !!awaitingOperation.value)
const cleanDisabled = computed(() => !!busy.value || props.stale || props.disabled || outcomeUnknown.value || running.value || !props.status?.maintenance.can_clean)
const usageLabel = computed(() => ({ ready: '已统计', refreshing: '统计中', stale: '待更新', unavailable: '暂不可用', unsupported: '服务不支持' })[props.status?.usage_state ?? 'unavailable'])
const measuredAt = computed(() => props.status?.measured_at ? new Date(props.status.measured_at).toLocaleString('zh-CN', { hour12: false }) : '尚未统计')
watch(() => props.status, status => {
  if (status?.maintenance.operation_id === awaitingOperation.value) awaitingOperation.value = null
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
  try {
    const status = await boundedRequest<CacheStatus>('/system/cache/refresh', { method: 'POST', signal: controller.signal })
    if (controller.signal.aborted) return
    // An old idle snapshot cannot acknowledge a clear whose response was lost.
    if (outcomeUnknown.value && status.maintenance.operation_id && status.maintenance.operation_id !== previousOperation) outcomeUnknown.value = false
    emit('changed', status)
  } catch (cause) {
    if (!controller.signal.aborted && !automatic) ElMessage({ type: 'error', message: errorMessage(cause), duration: 6000, showClose: true })
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
    if (status.maintenance.state === 'running') awaitingOperation.value = status.maintenance.operation_id
    emit('changed', status)
    ElMessage.info('缓存清理已提交，可在此查看结果。')
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
    <div class="cache-title"><h3>Telegram 媒体缓存</h3><el-tag type="info" round>{{ stale ? '状态待更新' : usageLabel }}</el-tag></div>
    <p class="field-hint">当前 Bot 的媒体缓存占用，包含未完成下载。缓存盘剩余 {{ bytes(status?.free_bytes ?? null) }} / {{ bytes(status?.total_bytes ?? null) }}。</p>
    <dl class="cache-meta"><div><dt>缓存占用</dt><dd>{{ bytes(status?.used_bytes ?? null) }}<span v-if="status?.file_count != null"> · {{ status.file_count }} 个文件</span></dd></div><div><dt>统计时间</dt><dd>{{ measuredAt }}</dd></div></dl>
    <p v-if="status?.usage_state === 'stale' || stale" class="field-hint">以上为上次统计，请刷新占用确认当前情况。</p>
    <p role="status" aria-live="polite" :class="outcomeUnknown || status?.maintenance.state === 'uncertain' ? 'warning-text' : 'field-hint'">{{ outcomeUnknown ? '清理结果尚未确认，请刷新占用查询。系统不会自动重复清理；仍无法确认时请在连接设置中重启本地 API。' : status?.maintenance.message || '正在获取缓存状态。' }}</p>
    <p v-if="status?.maintenance.last_result?.released_bytes != null" class="field-hint">上次清理实际释放 {{ bytes(status.maintenance.last_result.released_bytes) }}。</p>
    <div class="cache-actions"><el-button :loading="busy === 'refresh'" :disabled="!!busy || disabled" @click="refresh()">刷新占用</el-button><el-button type="danger" plain :loading="busy === 'clean' || running" :disabled="cleanDisabled" @click="clean">{{ running ? '正在清理' : '清理媒体缓存' }}</el-button></div>
    <p class="field-hint">下载与恢复处理结束后可统计和清理。缓存和保存目录均需足够空间。</p>
  </div>
</template>
<style scoped>
.cache-panel { margin-top: 24px; border-top: 1px solid var(--el-border-color-lighter); padding-top: 12px; }
.cache-title { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.cache-meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }
.cache-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.cache-actions .el-button { margin-left: 0; min-height: 44px; }
</style>
