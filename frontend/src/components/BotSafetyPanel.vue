<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { errorMessage, request } from '../api/client'
import { retryTime, safetyLabels } from '../api/botSafety'
import type { BotRequestSafety } from '../api/types'

const props = defineProps<{ status?: BotRequestSafety; stale?: boolean; disabled?: boolean }>()
const emit = defineEmits<{ changed: [status: BotRequestSafety] }>()
const busy = ref(false)
const controller = new AbortController()
onUnmounted(() => controller.abort())
async function retry() {
  if (busy.value || props.stale || props.disabled || !props.status?.can_retry) return
  busy.value = true
  try {
    const status = await request<BotRequestSafety>('/system/bot/retry', { method: 'POST', signal: controller.signal })
    if (controller.signal.aborted) return
    emit('changed', status)
    ElMessage.success('恢复请求已受理，请查看连接状态。')
  } catch (cause) {
    if (!controller.signal.aborted) ElMessage({ type: 'error', message: errorMessage(cause), duration: 6000, showClose: true })
  } finally { busy.value = false }
}
</script>
<template>
  <div v-if="status && status.state !== 'ready'" data-testid="bot-request-safety" role="status" aria-live="polite">
    <p class="warning-text">{{ safetyLabels[status.state] }}</p>
    <p v-if="status.message" class="field-hint">{{ status.message }}</p>
    <p v-if="retryTime(status.retry_at)" class="field-hint">{{ retryTime(status.retry_at) }}</p>
    <el-button v-if="status.can_retry || status.state === 'auth_paused'" :loading="busy" :disabled="disabled || stale || !status.can_retry" @click="retry">重试 Bot 连接</el-button>
  </div>
</template>
