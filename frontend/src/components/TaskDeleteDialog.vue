<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { errorMessage, request } from '../api/client'
import { taskDeletionFeedback } from '../api/localFiles'
import { deletionOutcomeUncertain, pendingTaskDeletion, retainTaskDeletion, taskDeletionOperationId } from '../api/taskDeletion'
import type { TaskDeleteRequest, TaskDeleteResponse } from '../api/types'

const props = defineProps<{ ids: string[]; open: boolean }>()
const emit = defineEmits<{ close: []; defer: []; removed: [ids: string[]] }>()
const pending = pendingTaskDeletion()
let attempt: TaskDeleteRequest | undefined = pending?.ids.length === props.ids.length
  && pending.ids.every((id, index) => props.ids[index] === id) ? pending : undefined
const deleteFiles = ref(attempt?.delete_files ?? false)
const busy = ref(false)
const error = ref(attempt ? '上一次删除结果尚未确认，请重试确认同一次删除的结果。' : '')
const uncertain = ref(!!attempt)
let controller: AbortController | undefined
let disposed = false

onBeforeUnmount(() => { disposed = true; controller?.abort() })

async function confirm() {
  if (busy.value) return
  const previouslyUncertain = uncertain.value
  // One operation identity survives a lost reply. Never turn an explicit retry
  // into a new destructive operation or allow its selection to change.
  attempt ??= { ids: [...props.ids], delete_files: deleteFiles.value,
    operation_id: taskDeletionOperationId() }
  retainTaskDeletion(attempt)
  busy.value = true
  error.value = ''
  controller = new AbortController()
  const deadline = setTimeout(() => controller?.abort(), 30_000)
  try {
    const result = await request<TaskDeleteResponse>('/tasks/delete', {
      method: 'POST', body: JSON.stringify(attempt), signal: controller.signal,
    })
    if (disposed) return
    const feedback = taskDeletionFeedback(result, attempt.delete_files)
    retainTaskDeletion()
    ElMessage({ type: feedback.warning ? 'warning' : 'success', message: feedback.message,
      duration: feedback.warning ? 10_000 : 5000, showClose: true })
    emit('removed', result.deleted_ids)
  } catch (cause) {
    if (disposed) return
    // A structured client error before acceptance can be corrected. Transport,
    // body and server failures may follow filesystem changes, so retain scope.
    uncertain.value = deletionOutcomeUncertain(cause, previouslyUncertain)
    if (!uncertain.value) { attempt = undefined; retainTaskDeletion() }
    error.value = uncertain.value ? `${errorMessage(cause)} 删除结果尚未确认。请重试查询同一次删除的结果，不会重复删除文件。` : errorMessage(cause)
  } finally {
    clearTimeout(deadline)
    if (!disposed) busy.value = false
  }
}
</script>
<template>
  <el-dialog :model-value="open" title="删除任务" width="min(460px, calc(100vw - 32px))"
    class="task-delete-dialog" :close-on-click-modal="false" :close-on-press-escape="!busy && !uncertain"
    :show-close="!busy && !uncertain" append-to-body @close="!busy && !uncertain && emit('close')">
    <p>将删除 {{ ids.length }} 条任务记录。</p>
    <el-checkbox v-model="deleteFiles" :disabled="busy || uncertain" aria-describedby="delete-files-help">同时删除已下载文件</el-checkbox>
    <p id="delete-files-help" class="muted delete-files-help">{{ deleteFiles ? '文件删除后无法恢复。仍被其他保留任务引用的文件会保留；未通过安全检查的文件也会保留。' : '仅删除任务记录，保留已下载文件。' }}</p>
    <el-alert v-if="error" :title="error" :type="uncertain ? 'warning' : 'error'" :closable="false" show-icon role="alert" />
    <template #footer>
      <el-button v-if="uncertain" :disabled="busy" @click="emit('defer')">稍后确认</el-button>
      <el-button v-else :disabled="busy" @click="emit('close')">取消</el-button>
      <el-button type="danger" :loading="busy" @click="confirm">{{ uncertain ? '重试确认结果' : '确认删除' }}</el-button>
    </template>
  </el-dialog>
</template>
