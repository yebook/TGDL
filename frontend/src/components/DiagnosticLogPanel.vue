<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { errorMessage, request } from '../api/client'
import { bytes } from '../api/format'
import { downloadDiagnosticLog } from '../api/diagnosticLog'
import type { DiagnosticLogExport, DiagnosticLogStatus } from '../api/types'

const props = defineProps<{ status?: DiagnosticLogStatus; stale?: boolean }>()
const busy = ref(false)
const controller = new AbortController()
onUnmounted(() => controller.abort())
async function exportLogs() {
  if (busy.value || props.stale || !props.status) return
  busy.value = true
  try {
    const result = await request<DiagnosticLogExport>('/system/logs', { signal: controller.signal })
    if (controller.signal.aborted || props.stale) return
    if (!result.events.length) {
      ElMessage.info('暂无可导出的诊断日志。')
      return
    }
    downloadDiagnosticLog(result.events)
    if (result.truncated) ElMessage.warning('诊断日志已导出，部分记录因导出上限或格式异常未包含。')
    else ElMessage.success('诊断日志已导出。')
  } catch (cause) {
    if (!controller.signal.aborted && !props.stale) ElMessage({ type: 'error', message: errorMessage(cause), duration: 6000, showClose: true })
  } finally { busy.value = false }
}
</script>
<template>
  <div v-if="status" data-testid="diagnostic-log-panel">
    <div class="diagnostic-title"><h3>诊断日志</h3><el-tag :type="status.state === 'ready' ? 'success' : 'danger'" round>{{ status.state === 'ready' ? '记录中' : '记录异常' }}</el-tag></div>
    <p :class="status.state === 'error' ? 'warning-text' : 'field-hint'">{{ status.message }}</p>
    <p class="field-hint">单文件上限 {{ bytes(status.max_file_bytes) }}，保留 {{ status.backup_count }} 个归档。导出为 JSONL，不包含凭据和媒体正文。</p>
    <el-button :loading="busy" :disabled="stale" @click="exportLogs">导出诊断日志</el-button>
  </div>
</template>
