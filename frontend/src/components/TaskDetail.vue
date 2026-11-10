<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errorMessage } from '../api/client'
import { boundedRequest } from '../api/boundedRequest'
import type { ItemCache, TaskDetail } from '../api/types'
import { attemptResult, bytes, dateTime, duration, mediaTypeLabel, taskIssueTone } from '../api/format'
import { mediaState } from '../api/mediaProgress'
import { localFileLabel } from '../api/localFiles'
import { usePolling } from '../composables/usePolling'
import StatusBadge from './StatusBadge.vue'
import MediaProgress from './MediaProgress.vue'
import StorageFailureDetails from './StorageFailureDetails.vue'

const props = defineProps<{ id: string; deleting?: boolean }>()
const emit = defineEmits<{ changed: []; remove: [id: string]; close: [] }>()
const busy = ref(false)
let controller = new AbortController()
let confirming = false
let generation = 0
const { data, error, loading, refresh } = usePolling(signal => boundedRequest<TaskDetail>(`/tasks/${encodeURIComponent(props.id)}`, { signal }))
const terminal = computed(() => !!data.value?.terminal)
const hasTaskActions = computed(() => !!(data.value?.can_cancel || data.value?.can_retry || data.value?.can_delete))
const caches = computed(() => {
  if (!terminal.value || !data.value) return []
  const unique = new Map<string, ItemCache>()
  for (const item of data.value.items) {
    for (const cache of item.caches ?? (item.cache ? [item.cache] : [])) {
      if (cache.state !== 'retained' || (cache.size_bytes == null && cache.allocated_bytes == null && !cache.path)) continue
      const key = `${cache.path ?? cache.id}|${cache.size_bytes ?? ''}|${cache.allocated_bytes ?? ''}`
      if (!unique.has(key)) unique.set(key, cache)
    }
  }
  return [...unique.values()]
})
const cleanupSummary = computed(() => {
  const cleanup = data.value?.cancel_cleanup
  if (!cleanup || cleanup.state === 'complete') return ''
  const unresolved = cleanup.entries.filter(entry => ['pending', 'failed', 'uncertain'].includes(entry.outcome)).length
  return unresolved ? `${cleanup.message}（还有 ${unresolved} 项待确认）` : cleanup.message
})
function dispose() { generation++; controller.abort(); if (confirming) ElMessageBox.close(); confirming = false }
onUnmounted(dispose)
watch(() => props.id, () => { dispose(); controller = new AbortController(); busy.value = false; data.value = undefined; void refresh() })
async function action(kind: 'cancel' | 'retry') {
  if (busy.value || props.deleting || !data.value) return
  const current = generation
  const id = props.id
  const signal = controller.signal
  if (!(kind === 'cancel' ? data.value.can_cancel : data.value.can_retry)) return
  busy.value = true
  try {
    if (kind === 'cancel') {
      confirming = true
      try {
        await ElMessageBox.confirm(
          '任务将安全停止，并删除该任务关联的缓存、临时文件和已保存文件；任务记录会保留。共享或无法确认归属的文件会保留并说明原因。',
          '取消任务',
          { confirmButtonText: '确认取消', cancelButtonText: '暂不操作', type: 'warning' },
        )
      } catch { return } finally { confirming = false }
    }
    if (current !== generation || signal.aborted || props.deleting || !(kind === 'cancel' ? data.value?.can_cancel : data.value?.can_retry)) return
    await boundedRequest(`/tasks/${encodeURIComponent(id)}/${kind}`, { method: 'POST', body: '{}', signal })
    if (current !== generation || signal.aborted) return
    ElMessage.success(kind === 'cancel' ? '已提交取消请求' : '已提交恢复请求')
    emit('changed')
    await refresh()
  } catch (cause) {
    if (current === generation && !signal.aborted) { ElMessage.error(errorMessage(cause)); void refresh() }
  } finally {
    if (current === generation) busy.value = false
  }
}
function handleActionCommand(kind: 'cancel' | 'retry' | 'delete') {
  if (kind === 'delete') {
    if (!props.deleting && !busy.value && data.value?.can_delete) emit('remove', data.value.id)
    return
  }
  void action(kind)
}
</script>
<template>
  <div class="detail-view">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-skeleton v-if="!data && loading" :rows="6" animated />
    <template v-if="data">
      <header class="detail-heading">
        <button class="detail-close" type="button" aria-label="关闭任务详情" title="收起详情" @click="emit('close')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14.5 5-7 7 7 7" /></svg>
        </button>
        <div class="detail-heading-copy">
          <p class="eyebrow">{{ data.kind === 'group' ? '文件组' : '单文件' }}</p>
          <h2>{{ data.title || '未命名文件' }}</h2>
          <div class="detail-heading-meta">
            <StatusBadge :status="data.status" />
            <span>{{ data.item_count }} 个文件</span>
          </div>
        </div>
        <div class="detail-heading-tools">
          <el-dropdown v-if="hasTaskActions" trigger="click" placement="bottom-end" popper-class="task-detail-actions-menu" @command="handleActionCommand">
            <button class="detail-action-menu-trigger" type="button" aria-label="任务操作" aria-haspopup="menu" title="任务操作" :disabled="deleting || busy" :aria-busy="busy">
              <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" /></svg>
            </button>
            <template #dropdown>
              <el-dropdown-menu aria-label="可用任务操作">
                <el-dropdown-item v-if="data.can_cancel" command="cancel" :disabled="deleting || busy">取消</el-dropdown-item>
                <el-dropdown-item v-if="data.can_retry" command="retry" :disabled="deleting || busy" class="task-menu-retry">恢复</el-dropdown-item>
                <el-dropdown-item v-if="data.can_delete" command="delete" :disabled="deleting || busy" class="task-menu-danger">删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <section class="detail-summary" aria-label="任务摘要">
        <div class="detail-stat">
          <span class="eyebrow">文件进度</span>
          <strong>{{ data.completed_count }} / {{ data.item_count }}</strong>
        </div>
        <div class="detail-stat">
          <span class="eyebrow">总大小</span>
          <strong>{{ bytes(data.total_bytes) }}</strong>
        </div>
        <div class="detail-stat detail-destination">
          <span class="eyebrow">保存目录</span>
          <span class="mono path-value">{{ data.effective_destination || '确认后确定' }}</span>
        </div>
        <dl class="detail-meta">
          <div><dt>任务编号</dt><dd class="mono">{{ data.id }}</dd></div>
          <div><dt>创建时间</dt><dd>{{ dateTime(data.created_at) }}</dd></div>
          <div><dt>通知状态</dt><dd>{{ ({ sent: '已反馈到 Bot', pending: '等待反馈', failed: '反馈失败' } as Record<string, string>)[data.notification_status] ?? data.notification_status }}</dd></div>
        </dl>
      </section>
      <div v-if="data.status === 'awaiting_confirmation' || cleanupSummary" class="detail-notices">
        <el-alert v-if="data.status === 'awaiting_confirmation'" :title="`请在 Telegram Bot 中确认下载，有效期至 ${dateTime(data.expires_at)}`" type="info" :closable="false" show-icon />
        <el-alert v-if="cleanupSummary" :title="cleanupSummary" type="warning" :closable="false" show-icon />
      </div>

      <section v-if="caches.length" class="cache-summary" aria-label="任务缓存">
        <div class="cache-heading"><h3>缓存</h3></div>
        <ul>
          <li v-for="cache in caches" :key="cache.id"><span>{{ bytes(cache.size_bytes ?? cache.allocated_bytes) }}</span><span v-if="cache.path" class="mono cache-path">{{ cache.path }}</span></li>
        </ul>
      </section>

      <div class="detail-section-heading"><h3>文件明细</h3><span class="muted">{{ data.completed_count }} / {{ data.item_count }} 已完成</span></div>
      <article v-for="(item, index) in data.items" :key="item.id" class="media-card">
        <div class="media-heading"><div><span class="eyebrow">{{ mediaTypeLabel(item.media_type) }} · {{ index + 1 }}</span><h4>{{ item.original_name || '自动命名文件' }}</h4></div><StatusBadge :status="mediaState(item)" /></div>
        <p class="muted">{{ bytes(item.size_bytes) }}<template v-if="item.duration_seconds != null"> · 时长 {{ duration(item.duration_seconds) }}</template><template v-if="item.status === 'succeeded'"> · 下载耗时 {{ duration(item.elapsed_seconds) }}</template></p>
        <MediaProgress v-if="['saving', 'fetching', 'cancelling'].includes(item.status)" :item="item" />
        <p v-if="item.relative_path" class="path-line mono">{{ item.relative_path }}</p>
        <p v-if="localFileLabel(item.local_file_state)" class="local-file-status" :class="item.local_file_state === 'present' ? 'muted' : 'warning-text'">{{ localFileLabel(item.local_file_state) }}</p>
        <p v-if="item.linked_job_id" class="muted">关联已有文件任务：<span class="mono">{{ item.linked_job_id }}</span></p>
        <el-alert v-if="item.error_message || item.error_code" :title="item.error_message || attemptResult(item.error_code, null)" :type="taskIssueTone(item.error_code)" :closable="false" show-icon />
        <StorageFailureDetails v-if="item.storage_failure" :failure="item.storage_failure" />
      </article>
    </template>
  </div>
</template>
<style scoped>
.detail-heading { position: sticky; top: 0; z-index: 10; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 8px; margin: 0 -26px 12px; padding: 9px 14px 10px; background: var(--el-bg-color); border-bottom: 1px solid var(--el-border-color-lighter); box-shadow: 0 2px 8px rgb(15 23 42 / 4%); isolation: isolate; }
.detail-heading-copy { min-width: 0; }
.detail-heading .eyebrow { margin-bottom: 2px; font-size: 10px; letter-spacing: 1.4px; line-height: 1.35; }
.detail-heading h2 { margin: 0 0 4px; font-size: 18px; line-height: 1.25; }
.detail-heading-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; color: var(--muted); font-size: 12px; }
.detail-heading-tools { display: flex; flex: 0 0 auto; align-items: center; gap: 4px; }
.detail-close { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 44px; width: 44px; height: 44px; padding: 0; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--el-text-color-secondary); cursor: pointer; }
.detail-close:hover { border-color: var(--el-border-color-light); background: var(--el-fill-color-light); color: var(--el-text-color-primary); }
.detail-close:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.detail-close svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-linecap: round; stroke-width: 1.8; }
.detail-action-menu-trigger { display: inline-flex; align-items: center; justify-content: center; width: 44px; height: 44px; padding: 0; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--el-text-color-secondary); cursor: pointer; }
.detail-action-menu-trigger:hover:not(:disabled) { border-color: var(--el-border-color-light); background: var(--el-fill-color-light); color: var(--el-text-color-primary); }
.detail-action-menu-trigger:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.detail-action-menu-trigger:disabled { cursor: not-allowed; opacity: .55; }
.detail-action-menu-trigger svg { width: 20px; height: 20px; fill: currentColor; }
:deep(.task-detail-actions-menu .el-dropdown-menu__item) { min-height: 44px; padding: 11px 16px; font-size: 13px; }
:deep(.task-detail-actions-menu .el-dropdown-menu__item.task-menu-retry:not(.is-disabled)) { color: var(--el-color-primary); }
:deep(.task-detail-actions-menu .el-dropdown-menu__item.task-menu-danger:not(.is-disabled)) { color: var(--el-color-danger); }
.detail-summary { display: grid; gap: 14px 16px; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); padding: 14px 16px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; background: var(--el-fill-color-blank); }
.detail-stat { min-width: 0; }
.detail-stat strong { display: block; margin-top: 3px; font-size: 1.15rem; line-height: 1.35; }
.detail-destination { grid-column: 1 / -1; padding-top: 2px; }
.detail-destination .eyebrow { display: block; margin-bottom: 4px; letter-spacing: 1.4px; }
.path-value, .cache-path { overflow-wrap: anywhere; }
.detail-destination .path-value { display: block; max-width: 100%; padding: 5px 8px; border: 1px solid var(--el-border-color-lighter); border-radius: 5px; background: var(--el-fill-color-light); color: var(--el-text-color-regular); font-size: 11px; line-height: 1.45; }
.detail-meta { display: grid; grid-column: 1 / -1; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px 16px; padding: 11px 0 0; margin: 0; border-top: 1px solid var(--el-border-color-lighter); border-radius: 0; background: transparent; }
.detail-meta dt { margin-bottom: 3px; color: var(--muted); font-size: 11px; }
.detail-meta dd { margin: 0; font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
.detail-notices .el-alert { margin-bottom: 8px; }
.cache-summary { margin: 14px 0 18px; padding: 12px 14px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.cache-heading { display: flex; align-items: baseline; }
.cache-heading h3 { margin: 0; font-size: 14px; }
.cache-summary ul { display: grid; gap: 8px; margin: 9px 0 0; padding: 0; list-style: none; }
.cache-summary li { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 10px; align-items: baseline; font-size: 12px; }
.detail-section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin: 18px 0 9px; }
.detail-section-heading h3 { margin: 0; font-size: 15px; }
.detail-section-heading .muted { font-size: 11px; }
.media-card { padding: 14px; margin-bottom: 9px; }
.media-card > p { margin: 8px 0; }
.media-card .el-alert { margin: 8px 0; }
@media (max-width: 480px) {
  .detail-heading { align-items: flex-start; margin-inline: -16px; padding-inline: 8px; }
  .detail-heading-tools { align-self: flex-start; }
  .detail-summary { grid-template-columns: 1fr 1fr; padding: 12px; }
  .detail-meta { grid-template-columns: 1fr; gap: 8px; }
  .detail-destination { grid-column: 1 / -1; }
  .cache-summary li { grid-template-columns: 1fr; gap: 2px; }
}
</style>
