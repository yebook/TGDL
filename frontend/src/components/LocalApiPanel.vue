<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ApiError, errorMessage } from '../api/client'
import { boundedRequest } from '../api/boundedRequest'
import { cooldownText, localApiPhaseLabel, localApiStates, localBotConnectionHint, modeLabel } from '../api/localApi'
import { connectionSummary } from '../api/systemSummary'
import type { BotRequestSafety, LocalApiStatus } from '../api/types'

const props = defineProps<{ status?: LocalApiStatus; bot?: { state: string; username?: string | null; message: string; request_safety?: BotRequestSafety }; detailed?: boolean; draft?: boolean; disabled?: boolean; stale?: boolean }>()
const emit = defineEmits<{ changed: [status: LocalApiStatus]; sync: [] }>()
const busy = ref(false)
const outcomeUnknown = ref(false)
watch(() => props.status, () => { outcomeUnknown.value = false })
let confirming = false
const controller = new AbortController()
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => {
  clearInterval(timer)
  controller.abort()
  if (confirming) ElMessageBox.close()
})
const cooldown = computed(() => cooldownText(props.status?.cloud_available_at ?? null, now.value))
const connection = computed(() => {
  if (!props.status) return undefined
  return connectionSummary(props.status, { state: 'unconfigured', message: '', ...props.bot, username: props.bot?.username || '' }, props.stale)
})
const connectionTone = computed(() => connection.value?.apiTone === 'danger' ? 'danger' : connection.value?.apiTone === 'warning' ? 'warning' : connection.value?.apiTone === 'success' ? 'success' : 'info')
const actionAllowed = (action: 'restart' | 'retry') => action === 'restart' ? props.status?.managed && props.status.can_restart : props.status?.can_retry
async function act(action: 'restart' | 'retry', acknowledge = false) {
  if (busy.value || outcomeUnknown.value || confirming || controller.signal.aborted || props.disabled || props.stale || !actionAllowed(action)) return
  if (action === 'restart' || acknowledge || props.draft) {
    confirming = true
    try {
      await ElMessageBox.confirm(acknowledge
        ? '上次云端退出的结果不明确。仅在你已核实云端已退出后继续；系统将尝试连接本地 API，不会再次执行云端退出。'
        : action === 'restart' ? '将等待当前下载安全结束，然后重启本地 API 进程。期间 Bot 会短暂不可用，WebUI 保持运行。' + (props.draft ? '本次仅使用已保存配置，未保存的输入会保留。' : '')
          : '本次仅重试已保存的连接配置；当前未保存的输入会保留，不会应用。',
      acknowledge ? '确认云端退出状态' : action === 'restart' ? '重启本地 API 进程' : '重试已保存的配置', {
        confirmButtonText: acknowledge ? '已核实退出，继续本地连接' : action === 'restart' ? '确认重启' : '使用已保存配置重试', cancelButtonText: '取消', type: 'warning',
      })
    } catch { return }
    finally { confirming = false }
  }
  if (busy.value || controller.signal.aborted || props.disabled || props.stale || !actionAllowed(action)) return
  busy.value = true
  try {
    const status = await boundedRequest<LocalApiStatus>(`/system/local-api/${action}`, {
      method: 'POST', signal: controller.signal,
      ...(action === 'retry' ? { body: JSON.stringify({ acknowledge_logout_uncertain: acknowledge }) } : {}),
    })
    if (controller.signal.aborted) return
    emit('changed', status)
    ElMessage.success('操作已受理，请查看下方进度。')
  } catch (cause) {
    if (!controller.signal.aborted) {
      outcomeUnknown.value = cause instanceof ApiError && cause.status === 0
      ElMessage({ type: 'error', message: errorMessage(cause) + (outcomeUnknown.value ? '操作可能已受理，正在重新获取状态，请勿重复提交。' : ''), duration: 6000, showClose: true })
      emit('sync')
    }
  }
  finally { busy.value = false }
}
</script>
<template>
  <section class="local-api-panel" aria-label="本地 API 服务状态" data-testid="local-api-status">
    <div class="diagnostic-title"><h3>API 与 Bot 连接</h3><el-tag v-if="connection" :type="connectionTone" round>{{ connection.apiState }}</el-tag><span v-else class="muted">{{ stale ? '状态获取失败，正在重试' : '正在获取状态…' }}</span></div>
    <template v-if="status">
      <p v-if="stale" class="warning-text" role="status">状态更新失败，当前显示上次结果；恢复连接后可操作。</p>
      <div v-if="outcomeUnknown" class="warning-text" role="status"><p>上次操作的结果尚未确认，请重新同步状态后再操作。</p><button type="button" class="connection-resync" @click="$emit('sync')">重新同步状态</button></div>
      <dl class="local-api-modes"><div><dt>已保存选择</dt><dd>{{ modeLabel(status.desired_mode) }}</dd></div><div><dt>实际使用</dt><dd>{{ modeLabel(status.applied_mode) }}</dd></div><div v-if="bot"><dt>Bot 连接</dt><dd>{{ connection?.botState }}</dd></div></dl>
      <p v-if="status.applied_mode === 'local' && connection?.botTone === 'success'" class="field-hint">{{ localBotConnectionHint }}</p>
      <p v-if="status.phase !== 'idle'" class="local-api-phase" aria-live="polite">{{ localApiPhaseLabel(status) }}</p>
      <p class="field-hint local-api-message" aria-live="polite">{{ status.message || (status.managed || status.desired_mode === 'cloud' ? '保存配置后由后台协调应用。' : '当前服务由部署环境管理；升级管理组件后可在此控制 API 进程。') }}</p>
      <p v-if="cooldown" class="warning-text">{{ cooldown }}</p>
      <p v-if="status.managed && status.desired_mode === 'local' && status.state === 'unavailable'" class="field-hint">管理组件未连接时，WebUI 无法重启 API 进程，请检查组件部署。</p>
      <div v-if="status.managed || status.can_retry" class="local-api-actions">
        <el-button v-if="status.managed" :loading="busy" :disabled="disabled || stale || outcomeUnknown || !status.can_restart" @click="act('restart')">重启 API 进程</el-button>
        <el-button v-if="status.phase === 'logout_uncertain'" :loading="busy" :disabled="disabled || stale || outcomeUnknown || !status.can_retry" @click="act('retry', true)">确认云端已退出并继续</el-button>
        <el-button v-else-if="status.can_retry" :loading="busy" :disabled="disabled || stale || outcomeUnknown" @click="act('retry')">{{ status.managed ? '重试操作' : '重试连接' }}</el-button>
      </div>
      <details v-if="detailed" class="settings-details"><summary>服务运行详情</summary><dl class="local-api-details"><div><dt>本地进程</dt><dd>{{ localApiStates[status.state] }}</dd></div><div><dt>操作阶段</dt><dd>{{ localApiPhaseLabel(status) }}</dd></div><div><dt>最近心跳</dt><dd>{{ status.last_heartbeat_at ? new Date(status.last_heartbeat_at).toLocaleString('zh-CN', { hour12: false }) : '尚未收到' }}</dd></div><div><dt>进程实例</dt><dd>{{ status.generation || '—' }}</dd></div><div><dt>操作编号</dt><dd>{{ status.operation_id || '—' }}</dd></div></dl></details>
      <p v-if="detailed" class="field-hint">进程运行不代表 Bot 已连接。此处仅管理 API 进程；网络拓扑与容器由部署环境提供。</p>
    </template>
  </section>
</template>
