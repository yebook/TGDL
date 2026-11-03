<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ApiError, errorMessage } from '../api/client'
import { boundedRequest } from '../api/boundedRequest'
import { operationId as createOperationId } from '../api/operationId'
import { bytes } from '../api/format'
import type { ItemCache, ItemCacheAction, TaskDetail } from '../api/types'

const props = defineProps<{ taskId: string; itemId: string; cache: ItemCache; disabled?: boolean; saved?: boolean }>()
const emit = defineEmits<{ changed: []; sync: [] }>()
const busy = ref<ItemCacheAction | null>(null)
const unknownOperation = ref<string | null>(null)
const awaitingOperation = ref<string | null>(null)
let confirming = false
let controller = new AbortController()
let generation = 0
let watchedOperation: string | null = null
let announcedOperation: string | null = null

function dispose() {
  generation++
  controller.abort()
  if (confirming) ElMessageBox.close()
  confirming = false
}
onUnmounted(dispose)
watch([() => props.taskId, () => props.itemId, () => props.cache.id], () => {
  dispose()
  controller = new AbortController()
  busy.value = null
  unknownOperation.value = null
  awaitingOperation.value = null
  watchedOperation = null
  announcedOperation = null
})
const labels: Record<ItemCacheAction, string> = { save: '继续保存', clean: '清理缓存', download: '继续下载', redownload: '重新下载（从头开始）' }
const active = computed(() => !!awaitingOperation.value || ['queued', 'running', 'uncertain'].includes(props.cache.operation?.state ?? ''))
const recoveryGuidance = computed(() => props.cache.recovery_guidance || (unknownOperation.value
  || props.cache.state === 'uncertain' || props.cache.operation?.state === 'uncertain'
  ? '无法确认操作结果。请先重新同步状态；持续无法确认时，前往连接设置恢复连接，并按服务指引重试或安全重启本地 API。' : ''))
const headline = computed(() => {
  if (props.cache.state === 'deleted' || props.cache.state === 'missing') return '缓存已不存在'
  if (props.cache.state === 'changed') return '缓存已改变，无法复用原文件'
  if (props.cache.state === 'uncertain') return '缓存状态待确认'
  if (props.cache.completeness === 'complete') return '完整缓存已保留'
  if (props.cache.completeness === 'partial') return '下载未完成，缓存已保留'
  return '缓存完整性待确认'
})
const commonReason = computed(() => {
  if (unknownOperation.value) return '操作结果尚未确认，请重新同步状态；确认结果前不能重复操作。'
  if (busy.value) return '请先完成当前缓存操作。'
  if (props.disabled) return '请先完成当前任务操作。'
  if (active.value) return props.cache.operation?.message || '缓存操作处理中，请等待安全结束；下载或保存可通过任务按钮取消。'
  return ''
})
function allowed(kind: ItemCacheAction) {
  return ({ save: props.cache.can_save, clean: props.cache.can_clean,
    download: props.cache.can_resume_download, redownload: props.cache.can_redownload })[kind]
}
function reason(kind: ItemCacheAction) {
  return commonReason.value || (allowed(kind) ? '' : kind === 'download'
    ? props.cache.resume_unavailable_reason || props.cache.unavailable_reason || '当前缓存无法继续下载。'
    : props.cache.unavailable_reason || '当前状态无法执行此操作。')
}
const actions = computed<ItemCacheAction[]>(() => props.cache.completeness === 'complete'
  ? props.cache.state === 'retained' ? ['save', 'clean'] : ['redownload', 'clean']
  : ['download', 'redownload', 'clean'])
const statusMessages = computed(() => [...new Set([
  props.cache.completeness !== 'complete' ? props.cache.resume_unavailable_reason : null,
  props.cache.unavailable_reason, props.cache.operation?.message, commonReason.value,
].filter((message): message is string => !!message))])

watch(() => props.cache.operation, operation => {
  if (!operation) return
  if (operation.id === unknownOperation.value) unknownOperation.value = null
  if (operation.id === awaitingOperation.value) awaitingOperation.value = null
  if (['queued', 'running'].includes(operation.state)) watchedOperation = operation.id
  announce(operation)
}, { immediate: true })
function announce(operation: ItemCache['operation']) {
  if (!operation || operation.id !== watchedOperation || operation.id === announcedOperation
      || !['succeeded', 'failed', 'cancelled'].includes(operation.state)) return
  announcedOperation = operation.id
  const success = operation.state === 'succeeded'
  ElMessage({ type: success ? 'success' : operation.state === 'cancelled' ? 'info' : 'error',
    message: operation.message || `${labels[operation.kind]}${success ? '完成' : operation.state === 'cancelled' ? '已取消，请查看任务清理结果' : '失败，请检查任务详情'}`,
    duration: 6000, showClose: true })
}
async function act(kind: ItemCacheAction) {
  if (reason(kind) || controller.signal.aborted) return
  const current = generation
  const taskId = props.taskId
  const itemId = props.itemId
  const cacheId = props.cache.id
  const signal = controller.signal
  const operationId = createOperationId()
  busy.value = kind
  try {
    if (kind === 'clean' || kind === 'redownload') {
      confirming = true
      try {
        await ElMessageBox.confirm(kind === 'clean'
          ? '仅清理此文件对应的缓存，已保存文件会保留。缓存删除后无法恢复，提交后不能通过取消任务撤回。'
          : '将重新下载，不依赖现有进度；旧缓存保留，可另外手动清理。开始前将重新检查完整下载所需空间。',
        labels[kind], { type: 'warning', confirmButtonText: kind === 'clean' ? '确认清理' : '确认重新下载', cancelButtonText: '暂不操作' })
      } catch { return }
      finally { confirming = false }
    }
    if (current !== generation || signal.aborted || props.taskId !== taskId || props.itemId !== itemId || props.cache.id !== cacheId
        || props.disabled || !allowed(kind) || active.value || unknownOperation.value) return
    watchedOperation = operationId
    const result = await boundedRequest<TaskDetail>(`/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(itemId)}/cache/${kind}`, {
      method: 'POST', signal, body: JSON.stringify({ operation_id: operationId, cache_id: cacheId, ...(kind === 'redownload' || kind === 'clean' ? { confirmed: true } : {}) }),
    })
    if (current !== generation || signal.aborted || props.taskId !== taskId || props.itemId !== itemId || props.cache.id !== cacheId) return
    const item = result.items.find(item => item.id === itemId)
    const operation = (item?.caches?.find(cache => cache.id === cacheId) ?? item?.cache)?.operation
    awaitingOperation.value = operationId
    if (operation?.id === operationId) {
      announce(operation)
      if (['queued', 'running'].includes(operation.state)) ElMessage.info(`${labels[kind]}已提交，可在任务详情查看进度。`)
    }
    emit('changed')
  } catch (cause) {
    if (current !== generation || signal.aborted) return
    if (!(cause instanceof ApiError) || cause.status === 0 || cause.status >= 500) unknownOperation.value = operationId
    ElMessage({ type: 'error', message: errorMessage(cause), duration: 6000, showClose: true })
    emit('sync')
  } finally { if (current === generation) busy.value = null }
}
</script>
<template>
  <section class="item-cache" aria-label="文件缓存">
    <p class="cache-heading">{{ headline }}</p>
    <p v-if="cache.completeness === 'complete' && cache.state === 'retained' && !saved" class="muted">缓存已下载完整，尚未完成目标保存；可释放空间后继续保存。</p>
    <p class="muted">缓存大小 {{ bytes(cache.size_bytes) }} · 实际占用 {{ bytes(cache.allocated_bytes) }}</p>
    <p v-if="cache.path" class="cache-path mono">{{ cache.path }}</p>
    <p v-else class="muted">暂无可靠的缓存路径记录。</p>
    <p v-if="cache.completeness === 'partial' && cache.can_resume_download" class="muted">继续下载将复用有效分片；Bot API 重启后可能无法恢复原进度。</p>
    <p v-for="message in statusMessages" :key="message" class="warning-text" role="status">{{ message }}</p>
    <p v-if="recoveryGuidance" class="warning-text">{{ recoveryGuidance }} <a href="#/settings">前往连接设置</a></p>
    <div class="item-cache-actions">
      <span v-for="kind in actions" :key="kind" :title="reason(kind) || undefined" :tabindex="reason(kind) ? 0 : undefined" :aria-label="reason(kind) ? `${labels[kind]}：${reason(kind)}` : undefined">
        <el-button :type="kind === 'save' || kind === 'download' ? 'primary' : kind === 'clean' ? 'danger' : 'default'" :plain="kind === 'clean'" :disabled="!!reason(kind)" :loading="busy === kind" @click="act(kind)">{{ labels[kind] }}</el-button>
      </span>
      <el-button v-if="unknownOperation || active || recoveryGuidance" @click="emit('sync')">重新同步状态</el-button>
    </div>
  </section>
</template>
<style scoped>
.item-cache { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--line); min-width: 0; }
.item-cache p { margin: 8px 0; font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.cache-heading { font-weight: 600; }
.cache-path { white-space: pre-wrap; word-break: break-word; }
.item-cache-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }
.item-cache-actions > span { display: inline-flex; max-width: 100%; }
.item-cache-actions .el-button { min-height: 44px; margin-left: 0; max-width: 100%; white-space: normal; height: auto; }
.item-cache-actions > span:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 3px; border-radius: 4px; }
</style>
