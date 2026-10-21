<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errorMessage, request } from '../api/client'
import { bytes } from '../api/format'
import { downloadDiagnosticLog } from '../api/diagnosticLog'
import type { DiagnosticLogExport, DiagnosticLogStatus } from '../api/types'

const props = defineProps<{ status?: DiagnosticLogStatus; stale?: boolean }>()
function cleanupDateTime(value: string | null) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { timeZone: props.status?.timezone, hour12: false })
}
const emit = defineEmits<{ changed: [status: DiagnosticLogStatus] }>()
const busy = ref<'export' | 'clear' | null>(null)
let confirming = false
const controller = new AbortController()
onUnmounted(() => { controller.abort(); if (confirming) ElMessageBox.close() })
async function exportLogs() {
  if (busy.value || props.stale || !props.status || controller.signal.aborted) return
  busy.value = 'export'
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
  } finally { busy.value = null }
}
async function clearLogs() {
  if (busy.value || props.stale || !props.status || controller.signal.aborted) return
  busy.value = 'clear'
  confirming = true
  try {
    try {
      await ElMessageBox.confirm('将永久删除已有诊断日志，无法恢复。任务、媒体文件、连接配置和请求保护不受影响；清理后会继续记录新事件。', '清理诊断日志', { type: 'warning', confirmButtonText: '确认清理', cancelButtonText: '取消' })
    } catch { return }
    finally { confirming = false }
    if (controller.signal.aborted || props.stale) return
    const status = await request<DiagnosticLogStatus>('/system/logs', { method: 'DELETE', signal: controller.signal })
    if (controller.signal.aborted || props.stale) return
    emit('changed', status)
    ElMessage.success('已有诊断日志已清理，继续记录新事件。')
  } catch (cause) {
    if (!controller.signal.aborted && !props.stale) ElMessage({ type: 'error', message: errorMessage(cause), duration: 6000, showClose: true })
  } finally { busy.value = null }
}
</script>
<template>
  <div v-if="status" data-testid="diagnostic-log-panel">
    <div class="diagnostic-title"><h3>诊断日志</h3><el-tag :type="status.state === 'ready' ? 'success' : 'danger'" round>{{ status.state === 'ready' ? '记录中' : '记录异常' }}</el-tag></div>
    <p :class="status.state === 'error' ? 'warning-text' : 'field-hint'">{{ status.message }}</p>
    <p class="field-hint">单文件上限 {{ bytes(status.max_file_bytes) }}，保留 {{ status.backup_count }} 个归档。导出为 JSONL，不包含凭据和媒体正文。</p>
    <p class="field-hint">{{ status.retention_days === 0 ? '已关闭按天清理' : `每天 ${status.cleanup_time}（${status.timezone}）清理超过 ${status.retention_days} 天的记录` }}；容量轮转始终有效。可在设置页修改。</p>
    <dl class="small-meta"><div><dt>当前日志占用</dt><dd>{{ bytes(status.total_bytes) }} · {{ status.file_count }} 个文件</dd></div><div><dt>上次清理</dt><dd>{{ cleanupDateTime(status.last_cleanup_at) }}</dd></div><div><dt>下次自动清理</dt><dd>{{ status.retention_days === 0 ? '已关闭' : cleanupDateTime(status.next_cleanup_at) }}</dd></div></dl>
    <div class="diagnostic-log-actions"><el-button :loading="busy === 'export'" :disabled="stale || !!busy" @click="exportLogs">导出诊断日志</el-button><el-button type="danger" plain :loading="busy === 'clear'" :disabled="stale || !!busy" @click="clearLogs">清理诊断日志</el-button></div>
  </div>
</template>
<style scoped>
.small-meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 20px; }
.diagnostic-log-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.diagnostic-log-actions .el-button { margin-left: 0; }
</style>
