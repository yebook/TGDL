<script setup lang="ts">
import { computed, ref } from 'vue'
import { useSystemStatus } from '../composables/useSystemStatus'
import { bytes } from '../api/format'
import { systemIssues, telegramSummary } from '../api/systemSummary'
import { proxySummary } from '../api/proxy'
import AppIcon from './AppIcon.vue'
const { data, error, refresh } = useSystemStatus()
const manualRefreshing = ref(false)
async function manualRefresh() {
  if (manualRefreshing.value) return
  manualRefreshing.value = true
  try { await refresh() }
  finally { manualRefreshing.value = false }
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
      <button v-if="telegram" type="button" class="telegram-api-summary" data-testid="top-api-status" title="当前实际 API 及切换目标。可响应表示 API 请求可完成；本地响应可能使用缓存，不代表 Telegram 实际消息收发正常。" @click="$emit('settings')"><span class="status-key">{{ telegram.historical ? '上次 API' : 'API' }}</span><span class="top-api-identity">{{ telegram.apiType }}</span><span class="status-indicator" :class="`status-tone--${telegram.apiTone}`">{{ telegram.apiState }}</span></button>
      <span v-else class="status-link">正在获取状态…</span>
      <span v-if="telegram" class="telegram-bot-summary" data-testid="top-bot-status" title="当前已识别的 Bot。已就绪表示应用已建立客户端，实际收发请向 Bot 发送消息确认。"><span class="status-key">{{ telegram.historical ? '上次 Bot' : 'Bot' }}</span><span class="top-bot-identity" :title="telegram.identity">{{ telegram.identity }}</span><span class="status-indicator" :class="`status-tone--${telegram.botTone}`">{{ telegram.botState }}</span></span>
      </div>
      <div class="top-status-metrics">
      <template v-if="data"><span>{{ error ? '上次队列：' : '' }}下载 {{ data.queue.active }} · 等待 {{ data.queue.pending }}</span><span v-if="root">可用 {{ error ? '待更新' : bytes(root.free_bytes) }}</span><span v-else>保存目录状态待确认</span></template>
      <span v-if="data" data-testid="top-proxy-status">{{ proxySummary(data.proxy.state, !!error) }}</span>
      </div>
    </div>
    <div class="top-status-tools">
      <button type="button" class="status-refresh" :aria-busy="manualRefreshing" :disabled="manualRefreshing" :title="manualRefreshing ? '正在刷新状态' : '刷新状态'" aria-label="刷新状态" @click="manualRefresh"><AppIcon name="refresh" :class="{ 'is-refreshing': manualRefreshing }" /></button>
    <slot name="account" />
    </div>
    </div>
    <p v-if="error" class="status-warning" role="alert">状态更新失败，{{ data ? '上次结果仅供参考；' : '' }}请检查应用连接后刷新。</p>
    <details v-if="issues.length" class="status-issues"><summary>{{ issues[0] }}<span v-if="issues.length > 1">（共 {{ issues.length }} 项需关注）</span></summary><ul v-if="issues.length > 1"><li v-for="issue in issues.slice(1)" :key="issue">{{ issue }}</li></ul><button class="status-link" @click="$emit('settings')">前往设置处理</button></details>
  </section>
</template>
