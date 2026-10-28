<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errorMessage, request } from '../api/client'
import type { TaskPage } from '../api/types'
import { bytes, dateTime, stateLabel } from '../api/format'
import { usePolling } from '../composables/usePolling'
import TaskDetail from '../components/TaskDetail.vue'
import StatusBadge from '../components/StatusBadge.vue'
import AppIcon from '../components/AppIcon.vue'

const status = ref('')
const query = ref('')
const search = ref('')
const page = ref(1)
const selected = ref('')
const selectedIds = ref<string[]>([])
const deleting = ref(false)
const { data, error, loading, refresh } = usePolling(signal => {
  const params = new URLSearchParams({ page: String(page.value), page_size: '20' })
  if (status.value) params.set('status', status.value)
  if (search.value) params.set('q', search.value)
  return request<TaskPage>(`/tasks?${params}`, { signal })
})
const filters = ['', 'collecting', 'awaiting_confirmation', 'queued', 'fetching', 'saving', 'retry_wait', 'cancelling', 'recovery_blocked', 'succeeded', 'already_exists', 'partial_success', 'failed', 'cancelled', 'expired']
const selectableIds = computed(() => data.value?.items.filter(task => task.can_delete).map(task => task.id) ?? [])
const allSelected = computed(() => selectableIds.value.length > 0 && selectableIds.value.every(id => selectedIds.value.includes(id)))
watch(data, value => {
  const eligible = new Set(value?.items.filter(task => task.can_delete).map(task => task.id))
  selectedIds.value = selectedIds.value.filter(id => eligible.has(id))
  if (value && page.value > 1 && !value.items.length) page.value = Math.max(1, Math.ceil(value.total / 20))
})
watch(page, () => { selectedIds.value = []; void refresh() })
watch(status, () => { selectedIds.value = []; if (page.value !== 1) page.value = 1; else void refresh() })
function submitSearch() { search.value = query.value.trim(); selectedIds.value = []; if (page.value !== 1) page.value = 1; else void refresh() }
function toggle(id: string, checked: unknown) {
  selectedIds.value = checked ? [...new Set([...selectedIds.value, id])] : selectedIds.value.filter(value => value !== id)
}
async function remove(ids: string[]) {
  if (deleting.value || !ids.length) return
  deleting.value = true
  try {
    try { await ElMessageBox.confirm(`将删除 ${ids.length} 条任务记录。仅删除任务记录，保留已下载文件。`, '删除任务', { confirmButtonText: '确认删除', cancelButtonText: '保留任务', type: 'warning' }) }
    catch { return }
    const result = await request<{ deleted_ids: string[] }>(ids.length === 1 ? `/tasks/${encodeURIComponent(ids[0]!)}` : '/tasks/delete', {
      method: ids.length === 1 ? 'DELETE' : 'POST', ...(ids.length > 1 ? { body: JSON.stringify({ ids }) } : {}),
    })
    const removed = new Set(result.deleted_ids)
    selectedIds.value = selectedIds.value.filter(id => !removed.has(id))
    if (removed.has(selected.value)) selected.value = ''
    ElMessage.success(`已删除 ${result.deleted_ids.length} 条任务记录，文件已保留`)
    await refresh()
  } catch (cause) { ElMessage({ type: 'error', message: errorMessage(cause), duration: 6000, showClose: true }) }
  finally { deleting.value = false }
}
</script>
<template>
  <section class="tasks-workspace">
    <div class="panel task-panel">
      <div class="task-toolbar"><el-select v-model="status" aria-label="筛选任务状态" class="status-filter" :disabled="deleting"><el-option v-for="value in filters" :key="value" :label="value ? stateLabel(value) : '全部状态'" :value="value" /></el-select><form class="search-form" @submit.prevent="submitSearch"><el-input v-model="query" placeholder="搜索名称或任务编号" aria-label="搜索名称或任务编号" :disabled="deleting" clearable @clear="submitSearch" /><el-button native-type="submit" :disabled="deleting">搜索</el-button></form></div>
      <div v-if="data?.items.length" class="task-selection"><el-checkbox :model-value="allSelected" :indeterminate="selectedIds.length > 0 && !allSelected" :disabled="deleting || !selectableIds.length" @change="selectedIds = $event ? [...selectableIds] : []">全选本页</el-checkbox><span class="muted" aria-live="polite">已选 {{ selectedIds.length }} 条</span><el-button type="danger" plain :disabled="!selectedIds.length || deleting" :loading="deleting" @click="remove([...selectedIds])">删除所选</el-button></div>
      <div class="task-scroll" role="region" aria-label="下载任务列表" tabindex="0">
      <el-alert v-if="error && !data" :title="error" type="error" :closable="false" show-icon />
      <el-skeleton v-if="!data && loading" :rows="5" animated class="list-skeleton" />
      <div v-else-if="data?.items.length" class="task-list">
        <div class="task-list-head"><span></span><span>文件 / 媒体组</span><span>状态</span><span>文件进度</span><span class="task-date">创建时间</span><span>操作</span></div>
        <div v-for="task in data.items" :key="task.id" class="task-row" :class="{ 'task-row-selected': selectedIds.includes(task.id) }">
          <el-checkbox :model-value="selectedIds.includes(task.id)" :aria-label="'选择任务 ' + task.title" :disabled="!task.can_delete || deleting" :title="task.can_delete ? '选择任务' : '活动任务请先在详情中取消'" @change="toggle(task.id, $event)" />
          <button class="task-name" :disabled="deleting" @click="selected = task.id"><span class="file-symbol"><AppIcon name="download" /></span><span><strong>{{ task.title || '未命名媒体' }}</strong><small class="task-id">编号：{{ task.id }}</small><small>{{ task.kind === 'group' ? '媒体组' : '单文件' }} · {{ bytes(task.total_bytes) }}</small></span></button>
          <span class="task-status"><StatusBadge :status="task.status" /></span><span class="file-progress">{{ task.completed_count }} / {{ task.item_count }}</span><span class="task-date">{{ dateTime(task.created_at) }}</span><el-button class="task-delete" type="danger" link :disabled="!task.can_delete || deleting" :aria-label="'删除任务 ' + task.title" :title="task.can_delete ? '删除任务记录' : '活动任务请先在详情中取消'" @click="remove([task.id])">删除</el-button>
        </div>
      </div>
      <el-empty v-else-if="data" :description="search || status ? '没有符合条件的任务' : '还没有下载任务'" :image-size="90" />
      </div>
      <div v-if="data" class="list-footer"><span class="muted task-total">共 {{ data.total }} 条</span><el-pagination v-model:current-page="page" :disabled="deleting" :total="data.total" :page-size="20" :pager-count="5" layout="prev, pager, next" /><span class="warning-text task-refresh-status" :title="error || undefined" role="status">{{ error ? '更新失败，保留上次结果' : '' }}</span></div>
    </div>
    <el-drawer :model-value="!!selected" title="任务详情" size="min(680px, 100vw)" destroy-on-close @close="selected = ''"><TaskDetail v-if="selected" :id="selected" :deleting="deleting" @changed="refresh" @remove="remove([$event])" /></el-drawer>
  </section>
</template>
