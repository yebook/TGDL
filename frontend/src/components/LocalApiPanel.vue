<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errorMessage, request } from '../api/client'
import { cooldownText, localApiPhases, localApiStates, localApiTone, modeLabel } from '../api/localApi'
import type { BotRequestSafety, LocalApiStatus } from '../api/types'
import { safetyLabels } from '../api/botSafety'

const props = defineProps<{ status?: LocalApiStatus; bot?: { state: string; message: string; request_safety?: BotRequestSafety }; detailed?: boolean; disabled?: boolean; stale?: boolean }>()
const emit = defineEmits<{ changed: [status: LocalApiStatus] }>()
const busy = ref(false)
let confirming = false
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => clearInterval(timer))
const cooldown = computed(() => cooldownText(props.status?.cloud_available_at ?? null, now.value))
const connection = computed(() => {
  const safety = props.bot?.request_safety
  if (safety && safety.state !== 'ready') return safetyLabels[safety.state]
  const state = props.bot?.state
  return state === 'connected' || state === 'online' ? 'Bot 已连接' : state === 'connecting' ? 'Bot 正在连接' : state === 'unconfigured' ? 'Bot 尚未配置' : 'Bot 未连接'
})
async function act(action: 'restart' | 'retry', acknowledge = false) {
  if (busy.value || confirming || props.disabled || props.stale || !props.status?.managed) return
  if (action === 'restart' || acknowledge) {
    confirming = true
    try {
      await ElMessageBox.confirm(acknowledge
        ? '上次云端退出的结果不明确。仅在你已核实云端已退出后继续；系统将尝试连接本地 API，不会再次执行云端退出。'
        : '将等待当前下载安全结束，然后重启本地 API 进程。期间 Bot 会短暂不可用，WebUI 保持运行。',
      acknowledge ? '确认云端退出状态' : '重启本地 API 进程', {
        confirmButtonText: acknowledge ? '已核实退出，继续本地连接' : '确认重启', cancelButtonText: '取消', type: 'warning',
      })
    } catch { return }
    finally { confirming = false }
  }
  if (busy.value || props.disabled || props.stale || !props.status?.managed) return
  busy.value = true
  try {
    const status = await request<LocalApiStatus>(`/system/local-api/${action}`, {
      method: 'POST', ...(action === 'retry' ? { body: JSON.stringify({ acknowledge_logout_uncertain: acknowledge }) } : {}),
    })
    emit('changed', status)
    ElMessage.success('操作已受理，请查看下方进度。')
  } catch (cause) { ElMessage({ type: 'error', message: errorMessage(cause), duration: 6000, showClose: true }) }
  finally { busy.value = false }
}
</script>
<template>
  <section class="local-api-panel" aria-label="本地 API 服务状态" data-testid="local-api-status">
    <div class="diagnostic-title"><h3>本地 API 服务</h3><el-tag v-if="status" :type="localApiTone(status)" round>{{ status.managed ? localApiStates[status.state] : '外部管理' }}</el-tag><span v-else class="muted">{{ stale ? '状态获取失败，正在重试' : '正在获取状态…' }}</span></div>
    <template v-if="status">
      <p v-if="stale" class="warning-text" role="status">状态更新失败，当前显示上次结果；恢复连接后可操作。</p>
      <dl class="local-api-modes"><div><dt>已保存选择</dt><dd>{{ modeLabel(status.desired_mode) }}</dd></div><div><dt>实际使用</dt><dd>{{ modeLabel(status.applied_mode) }}</dd></div><div v-if="bot"><dt>Bot 连接</dt><dd>{{ connection }}</dd></div></dl>
      <p v-if="status.phase !== 'idle'" class="local-api-phase" aria-live="polite">{{ localApiPhases[status.phase] }}</p>
      <p class="field-hint local-api-message" aria-live="polite">{{ status.message || (status.managed ? '保存配置后由后台协调应用。' : '当前服务由部署环境管理；升级管理组件后可在此控制 API 进程。') }}</p>
      <p v-if="cooldown" class="warning-text">{{ cooldown }}</p>
      <p v-if="status.managed && status.state === 'unavailable'" class="field-hint">管理组件未连接时，WebUI 无法重启 API 进程，请检查组件部署。</p>
      <div v-if="status.managed" class="local-api-actions">
        <el-button :loading="busy" :disabled="disabled || stale || !status.can_restart" @click="act('restart')">重启 API 进程</el-button>
        <el-button v-if="status.phase === 'logout_uncertain'" :loading="busy" :disabled="disabled || stale || !status.can_retry" @click="act('retry', true)">确认云端已退出并继续</el-button>
        <el-button v-else-if="status.can_retry" :loading="busy" :disabled="disabled || stale" @click="act('retry')">重试操作</el-button>
      </div>
      <details v-if="detailed" class="settings-details"><summary>服务运行详情</summary><dl class="local-api-details"><div><dt>操作阶段</dt><dd>{{ localApiPhases[status.phase] }}</dd></div><div><dt>最近心跳</dt><dd>{{ status.last_heartbeat_at ? new Date(status.last_heartbeat_at).toLocaleString('zh-CN', { hour12: false }) : '尚未收到' }}</dd></div><div><dt>进程实例</dt><dd>{{ status.generation || '—' }}</dd></div><div><dt>操作编号</dt><dd>{{ status.operation_id || '—' }}</dd></div></dl></details>
      <p v-if="detailed" class="field-hint">进程运行不代表 Bot 已连接。此处仅管理 API 进程；网络拓扑与容器由部署环境提供。</p>
    </template>
  </section>
</template>
