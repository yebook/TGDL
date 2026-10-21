<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { request, errorMessage } from '../api/client'
import type { TaskDetail } from '../api/types'
import { bytes, dateTime } from '../api/format'
import { mediaState } from '../api/mediaProgress'
import { usePolling } from '../composables/usePolling'
import StatusBadge from './StatusBadge.vue'
import MediaProgress from './MediaProgress.vue'

const props = defineProps<{ id: string; deleting?: boolean }>()
const emit = defineEmits<{ changed: []; remove: [id: string] }>()
const busy = ref(false)
const { data, error, loading, refresh } = usePolling(signal => request<TaskDetail>(`/tasks/${encodeURIComponent(props.id)}`, { signal }))
watch(() => props.id, () => { void refresh() })
async function action(kind: 'cancel' | 'retry') {
  if (kind === 'cancel') {
    try { await ElMessageBox.confirm('取消尚未完成的文件下载？已保存的文件会保留。', '取消下载', { confirmButtonText: '确认取消', cancelButtonText: '继续下载', type: 'warning' }) }
    catch { return }
  }
  busy.value = true
  try {
    await request(`/tasks/${encodeURIComponent(props.id)}/${kind}`, { method: 'POST', body: '{}' })
    ElMessage.success(kind === 'cancel' ? '已提交取消请求' : '已提交失败文件重试')
    emit('changed'); await refresh()
  } catch (cause) { ElMessage.error(errorMessage(cause)) }
  finally { busy.value = false }
}
</script>
<template>
  <div class="detail-view">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-skeleton v-if="!data && loading" :rows="6" animated />
    <template v-if="data">
      <div class="detail-heading"><div><p class="eyebrow">{{ data.kind === 'group' ? '媒体组' : '单文件' }}</p><h2>{{ data.title || '未命名媒体' }}</h2></div><StatusBadge :status="data.status" /></div>
      <dl class="detail-meta">
        <div><dt>任务编号</dt><dd class="mono">{{ data.id }}</dd></div>
        <div><dt>创建时间</dt><dd>{{ dateTime(data.created_at) }}</dd></div>
        <div><dt>保存目录</dt><dd class="mono">{{ data.effective_destination || '确认后确定' }}</dd></div>
        <div><dt>结果反馈</dt><dd>{{ ({ sent: '已反馈到 Bot', pending: '等待反馈', failed: '反馈失败', none: '暂无反馈', delivered: '已反馈到 Bot' } as Record<string, string>)[data.notification_status] ?? data.notification_status }}</dd></div>
      </dl>
      <el-alert v-if="data.status === 'awaiting_confirmation'" :title="`请在 Telegram Bot 中确认下载。有效期至 ${dateTime(data.expires_at)}。`" type="info" :closable="false" show-icon />
      <div v-if="data.can_cancel || data.can_retry || data.can_delete" class="detail-actions"><el-button v-if="data.can_retry" type="primary" :loading="busy" :disabled="deleting" @click="action('retry')">重试失败文件</el-button><el-button v-if="data.can_cancel" :loading="busy" :disabled="deleting" @click="action('cancel')">取消下载</el-button><el-button v-if="data.can_delete" type="danger" plain :disabled="busy || deleting" @click="emit('remove', data.id)">删除任务</el-button></div>
      <h3>文件明细 <span class="muted">{{ data.completed_count }} / {{ data.item_count }}</span></h3>
      <article v-for="(item, index) in data.items" :key="item.id" class="media-card">
        <div class="media-heading"><div><span class="eyebrow">{{ item.media_type === 'video' ? '视频' : '图片' }} · {{ index + 1 }}</span><h4>{{ item.original_name || '自动命名文件' }}</h4></div><StatusBadge :status="mediaState(item)" /></div>
        <p class="muted">{{ bytes(item.size_bytes) }}<template v-if="item.duration_seconds != null"> · {{ item.duration_seconds }} 秒</template></p>
        <MediaProgress v-if="['saving', 'fetching', 'cancelling'].includes(item.status)" :item="item" />
        <p v-if="item.relative_path" class="path-line mono">{{ item.relative_path }}</p>
        <p v-if="item.linked_job_id" class="muted">关联已有文件任务：<span class="mono">{{ item.linked_job_id }}</span></p>
        <el-alert v-if="item.error_message || item.error_code" :title="item.error_message || item.error_code || ''" type="error" :closable="false" show-icon />
        <details v-if="item.attempts.length" class="attempts"><summary>执行记录（{{ item.attempts.length }}）</summary><div v-for="attempt in item.attempts" :key="attempt.sequence" class="attempt"><strong>第 {{ attempt.sequence }} 次</strong><span>{{ dateTime(attempt.started_at) }}</span><span>{{ attempt.error_code || (attempt.finished_at ? '已结束' : '执行中') }}</span></div></details>
      </article>
    </template>
  </div>
</template>
