<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errorMessage } from '../api/client'
import { boundedRequest } from '../api/boundedRequest'
import type { TaskDetail } from '../api/types'
import { attemptResult, bytes, dateTime, duration, mediaTypeLabel, taskIssueTone } from '../api/format'
import { mediaState } from '../api/mediaProgress'
import { localFileLabel } from '../api/localFiles'
import { usePolling } from '../composables/usePolling'
import StatusBadge from './StatusBadge.vue'
import MediaProgress from './MediaProgress.vue'
import ItemCachePanel from './ItemCachePanel.vue'
import StorageFailureDetails from './StorageFailureDetails.vue'

const props = defineProps<{ id: string; deleting?: boolean }>()
const emit = defineEmits<{ changed: []; remove: [id: string] }>()
const busy = ref(false)
let controller = new AbortController()
let confirming = false
let generation = 0
const { data, error, loading, refresh } = usePolling(signal => boundedRequest<TaskDetail>(`/tasks/${encodeURIComponent(props.id)}`, { signal }))
function dispose() { generation++; controller.abort(); if (confirming) ElMessageBox.close(); confirming = false }
onUnmounted(dispose)
watch(() => props.id, () => { dispose(); controller = new AbortController(); busy.value = false; data.value = undefined; void refresh() })
async function cacheChanged() { emit('changed'); await refresh() }
async function action(kind: 'cancel' | 'retry') {
  if (busy.value || props.deleting) return
  const current = generation
  const id = props.id
  const signal = controller.signal
  busy.value = true
  try {
    if (kind === 'cancel') {
      confirming = true
      try { await ElMessageBox.confirm(data.value?.cancel_cleanup?.can_retry
        ? '重新核对并清理此任务未能删除的文件？会保留其他任务仍引用或身份无法确认的文件。删除结果未知的操作会先核对，不会重复执行。'
        : '取消任务并删除关联缓存、目标临时文件和已保存文件（包括组内已保存项）？文件删除后无法恢复，任务记录保留。正在下载或保存时需先等待安全停止；共享或无法核实的文件会保留并说明原因。',
      data.value?.cancel_cleanup?.can_retry ? '重试清理' : '取消并删除文件',
      { confirmButtonText: data.value?.cancel_cleanup?.can_retry ? '确认重试清理' : '确认取消并删除', cancelButtonText: '暂不操作', type: 'warning' }) }
      catch { return }
      finally { confirming = false }
    }
    if (current !== generation || signal.aborted || props.deleting || !(kind === 'cancel' ? data.value?.can_cancel : data.value?.can_retry)) return
    await boundedRequest(`/tasks/${encodeURIComponent(id)}/${kind}`, { method: 'POST', body: '{}', signal })
    if (current !== generation || signal.aborted) return
    ElMessage.success(kind === 'cancel' ? '已提交取消请求' : '已提交失败文件重试')
    emit('changed'); await refresh()
  } catch (cause) { if (current === generation && !signal.aborted) { ElMessage.error(errorMessage(cause)); void refresh() } }
  finally { if (current === generation) busy.value = false }
}
</script>
<template>
  <div class="detail-view">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-skeleton v-if="!data && loading" :rows="6" animated />
    <template v-if="data">
      <div class="detail-heading"><div><p class="eyebrow">{{ data.kind === 'group' ? '文件组' : '单文件' }}</p><h2>{{ data.title || '未命名文件' }}</h2></div><StatusBadge :status="data.status" /></div>
      <dl class="detail-meta">
        <div><dt>任务编号</dt><dd class="mono">{{ data.id }}</dd></div>
        <div><dt>创建时间</dt><dd>{{ dateTime(data.created_at) }}</dd></div>
        <div><dt>保存目录</dt><dd class="mono">{{ data.effective_destination || '确认后确定' }}</dd></div>
        <div><dt>结果反馈</dt><dd>{{ ({ sent: '已反馈到 Bot', pending: '等待反馈', failed: '反馈失败', none: '暂无反馈', delivered: '已反馈到 Bot' } as Record<string, string>)[data.notification_status] ?? data.notification_status }}</dd></div>
      </dl>
      <el-alert v-if="data.status === 'awaiting_confirmation'" :title="`请在 Telegram Bot 中确认下载。有效期至 ${dateTime(data.expires_at)}。`" type="info" :closable="false" show-icon />
      <section v-if="data.cancel_cleanup" aria-label="取消后的文件清理" class="cancel-cleanup">
        <el-alert :title="data.cancel_cleanup.message" :type="data.cancel_cleanup.state === 'complete' ? 'success' : 'warning'" :closable="false" show-icon />
        <ul v-if="data.cancel_cleanup.entries.length"><li v-for="(entry, index) in data.cancel_cleanup.entries" :key="index"><span>{{ entry.kind === 'cache' ? '缓存' : entry.kind === 'temporary' ? '目标临时文件' : '已保存文件' }}：</span><span class="mono">{{ entry.path || '位置待确认' }}</span><p>{{ entry.message }}</p></li></ul>
      </section>
      <div v-if="data.can_cancel || data.can_retry || data.can_delete" class="detail-actions"><el-button v-if="data.can_retry" type="primary" :loading="busy" :disabled="deleting || busy" @click="action('retry')">重试失败文件</el-button><el-button v-if="data.can_cancel" :loading="busy" :disabled="deleting || busy" @click="action('cancel')">{{ data.cancel_cleanup?.can_retry ? '重试清理' : '取消并删除文件' }}</el-button><el-button v-if="data.can_delete" type="danger" plain :disabled="busy || deleting" @click="emit('remove', data.id)">删除任务</el-button></div>
      <h3>文件明细 <span class="muted">{{ data.completed_count }} / {{ data.item_count }}</span></h3>
      <article v-for="(item, index) in data.items" :key="item.id" class="media-card">
        <div class="media-heading"><div><span class="eyebrow">{{ mediaTypeLabel(item.media_type) }} · {{ index + 1 }}</span><h4>{{ item.original_name || '自动命名文件' }}</h4></div><StatusBadge :status="mediaState(item)" /></div>
        <p class="muted">{{ bytes(item.size_bytes) }}<template v-if="item.duration_seconds != null"> · 时长 {{ duration(item.duration_seconds) }}</template><template v-if="item.status === 'succeeded'"> · 下载耗时 {{ duration(item.elapsed_seconds) }}</template></p>
        <MediaProgress v-if="['saving', 'fetching', 'cancelling'].includes(item.status)" :item="item" />
        <p v-if="item.relative_path" class="path-line mono">{{ item.relative_path }}</p>
        <p v-if="localFileLabel(item.local_file_state)" class="local-file-status" :class="item.local_file_state === 'present' ? 'muted' : 'warning-text'">{{ localFileLabel(item.local_file_state) }}</p>
        <p v-if="item.linked_job_id" class="muted">关联已有文件任务：<span class="mono">{{ item.linked_job_id }}</span></p>
        <el-alert v-if="item.error_message || item.error_code" :title="item.error_message || attemptResult(item.error_code, null)" :type="taskIssueTone(item.error_code)" :closable="false" show-icon />
        <StorageFailureDetails v-if="item.storage_failure" :failure="item.storage_failure" />
        <ItemCachePanel v-for="cache in item.caches ?? (item.cache ? [item.cache] : [])" :key="cache.id" :task-id="data.id" :item-id="item.id" :cache="cache" :saved="['succeeded', 'already_exists'].includes(item.status)" :disabled="busy || deleting" @changed="cacheChanged" @sync="refresh" />
        <details v-if="item.attempts.length" class="attempts"><summary>执行记录（{{ item.attempts.length }}）</summary><div v-for="attempt in item.attempts" :key="attempt.sequence" class="attempt"><strong>第 {{ attempt.sequence }} 次</strong><span>{{ dateTime(attempt.started_at) }}</span><span>{{ attemptResult(attempt.error_code, attempt.finished_at) }}</span></div></details>
      </article>
    </template>
  </div>
</template>
<style scoped>
.detail-actions { flex-wrap: wrap; }
.detail-actions .el-button { min-height: 44px; }
.cancel-cleanup ul { padding-left: 20px; overflow-wrap: anywhere; }
.cancel-cleanup li p { margin-top: 4px; }
</style>
