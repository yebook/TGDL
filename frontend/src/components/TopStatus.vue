<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { boundedRequest } from '../api/boundedRequest'
import { useSystemStatus } from '../composables/useSystemStatus'
import { bytes } from '../api/format'
import { systemIssues, telegramSummary } from '../api/systemSummary'
import { proxySummary } from '../api/proxy'
import type { ProtocolRefreshResult } from '../api/types'
import AppIcon from './AppIcon.vue'
const { data, error, refresh } = useSystemStatus()
const manualRefreshing = ref(false)
const protocolRefreshFeedback = ref('')
const controller = new AbortController()
onUnmounted(() => controller.abort())
async function manualRefresh() {
  if (manualRefreshing.value) return
  manualRefreshing.value = true
  protocolRefreshFeedback.value = ''
  try {
    const api = data.value?.local_api
    if (!api || error.value || (api.managed && api.desired_mode === 'local')) {
      try {
        const result = await boundedRequest<ProtocolRefreshResult | undefined>('/system/protocol-refresh', { method: 'POST', signal: controller.signal }, 10000)
        if (!controller.signal.aborted) {
          if (result?.outcome === 'updated') protocolRefreshFeedback.value = 'Telegram 协议复查已完成。'
          else if (result?.outcome === 'reused') protocolRefreshFeedback.value = 'Telegram 协议状态沿用仍有效的近期结果，未重复检测。'
          else if (result?.reason === 'not_applicable') protocolRefreshFeedback.value = '当前 API 状态不适用 Telegram 协议复查。'
          else if (result?.reason === 'another_round') protocolRefreshFeedback.value = '另一轮 Telegram 协议检测尚未结束，本次未更新协议结果。'
          else protocolRefreshFeedback.value = '本次未取得可用于当前 API 的新协议结果，请稍后重试。'
        }
      } catch {
        if (!controller.signal.aborted) protocolRefreshFeedback.value = 'Telegram 协议复查未完成，请稍后重试。'
      }
    }
    if (!controller.signal.aborted) await refresh()
  } finally { manualRefreshing.value = false }
}
defineEmits<{ settings: [] }>()
const issues = computed(() => data.value ? systemIssues(data.value) : [])
const root = computed(() => data.value?.storage.roots.find(item => item.id === data.value?.storage.selected_root_id))
const telegram = computed(() => data.value ? telegramSummary(data.value, !!error.value) : undefined)
</script>
<template>
  <section class="top-status" aria-label="运行概况" data-testid="top-status">
    <div class="top-status-main">
    <div class="top-status-summary">
      <div class="top-status-connections">
      <button v-if="telegram" type="button" class="telegram-api-summary" data-testid="top-api-status" title="当前实际 API 及切换目标。协议可达表示至少一个固定探测目标握手通过；不代表所有线路或 Bot 收发正常。" @click="$emit('settings')"><span class="status-key">{{ telegram.historical ? '上次 API' : 'API' }}</span><span class="top-api-identity">{{ telegram.apiType }}</span><span class="status-indicator" :class="`status-tone--${telegram.apiTone}`">{{ telegram.apiState }}</span></button>
      <span v-else class="status-link">正在获取状态…</span>
      <span v-if="telegram" class="telegram-bot-summary" data-testid="top-bot-status" :title="telegram.botHint ?? '当前已识别的 Bot。已就绪表示应用已建立客户端，协议检测也不能证明实际收发；请向 Bot 发送消息确认。'"><span class="status-key">{{ telegram.historical ? '上次 Bot' : 'Bot' }}</span><span class="top-bot-identity" :title="telegram.identity">{{ telegram.identity }}</span><span class="status-indicator" :class="`status-tone--${telegram.botTone}`">{{ telegram.botState }}</span></span>
      </div>
      <div class="top-status-metrics">
      <template v-if="data"><span>{{ error ? '上次队列：' : '' }}下载 {{ data.queue.active }} · 等待 {{ data.queue.pending }}</span><span v-if="root">可用 {{ error ? '待更新' : bytes(root.free_bytes) }}</span><span v-else>保存目录状态待确认</span></template>
      <span v-if="data" data-testid="top-proxy-status">{{ proxySummary(data.proxy.state, !!error) }}</span>
      <span v-if="data?.storage.cache.maintenance.state === 'running'">{{ error ? '上次状态：' : '' }}缓存清理中</span>
      </div>
    </div>
    <div class="top-status-tools">
      <button type="button" class="status-refresh" :aria-busy="manualRefreshing" :disabled="manualRefreshing" :title="manualRefreshing ? '正在刷新状态' : '刷新状态'" aria-label="刷新状态" @click="manualRefresh"><AppIcon name="refresh" :class="{ 'is-refreshing': manualRefreshing }" /></button>
    <slot name="account" />
    </div>
    </div>
    <p v-if="error" class="status-warning" role="alert">状态更新失败，{{ data ? '上次结果仅供参考；' : '' }}请检查应用连接后刷新。</p>
    <p v-else-if="protocolRefreshFeedback" class="status-refresh-feedback" role="status">{{ protocolRefreshFeedback }}</p>
    <details v-if="issues.length" class="status-issues"><summary>{{ issues[0] }}<span v-if="issues.length > 1">（共 {{ issues.length }} 项需关注）</span></summary><ul v-if="issues.length > 1"><li v-for="issue in issues.slice(1)" :key="issue">{{ issue }}</li></ul><button class="status-link" @click="$emit('settings')">前往设置处理</button></details>
  </section>
</template>
