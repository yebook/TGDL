<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { networkAdvice, networkProbeLabel, networkResultHistorical, useNetworkTest } from '../api/networkTest'
import { localApiStates } from '../api/localApi'
import { proxyLabels } from '../api/proxy'

const props = defineProps<{ revision: number; active: boolean; draft: boolean; disabled?: boolean; outcomeUnknown?: boolean }>()
const { result, busy, error, nextAllowedAt, run, cancel } = useNetworkTest()
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => { clearInterval(timer); cancel() })
watch(() => props.active, active => { if (!active) cancel() })
const historical = computed(() => result.value && (props.outcomeUnknown || networkResultHistorical(result.value, props.revision, now.value)))
const waitSeconds = computed(() => Math.max(0, Math.ceil((nextAllowedAt.value - now.value) / 1000)))
const probes = computed(() => result.value ? [
  { name: 'Telegram HTTPS', result: result.value.telegram },
  { name: 'Google HTTPS', result: result.value.google },
  { name: '应用 → 本地 API TCP', result: result.value.local_api_tcp },
] : [])
const checkedTime = computed(() => result.value ? new Date(result.value.checked_at).toLocaleString('zh-CN', { hour12: false }) : '')
const advice = computed(() => result.value ? networkAdvice(result.value) : [])
function testNetwork() { if (props.active && !props.disabled && !props.outcomeUnknown) void run() }
</script>
<template>
  <section class="network-test-panel" aria-label="网络连通测试" :aria-busy="busy" data-testid="network-test">
    <div class="diagnostic-title"><h3>网络连通测试</h3><el-button :loading="busy" :disabled="disabled || outcomeUnknown || busy || waitSeconds > 0" @click="testNetwork">{{ busy ? '正在检测…' : waitSeconds > 0 ? `${waitSeconds} 秒后可重测` : '测试 Telegram 与 Google' }}</el-button></div>
    <p class="field-hint">从应用服务所在网络发起，使用已保存的直连或代理配置，不需要 Bot Token。不会重启 API 或改变下载与请求保护。</p>
    <p v-if="draft" class="field-hint">当前有未保存输入：本次不测试草稿，输入会保留。</p>
    <p v-if="outcomeUnknown" class="warning-text" role="status">上次保存结果尚未确认，请先重新同步状态再检测，以确认使用的路线。</p>
    <p v-if="busy" class="field-hint" role="status">正在独立检测两个 HTTPS 入口；网络轮次最多 8 秒，页面最多等待 15 秒。</p>
    <p v-if="error" class="warning-text" role="alert">{{ error }}</p>
    <div v-if="result" class="network-results" aria-live="polite">
      <p v-if="historical" class="warning-text" role="status">历史结果：保存版本已变化、结果超过 5 分钟或保存结果未确认，请重新检测。</p>
      <p class="field-hint">{{ checkedTime }} · 应用服务端 · 保存版本 {{ result.settings_revision }} · {{ result.route === 'direct' ? '直连' : result.proxy_scheme === 'http' ? 'HTTP CONNECT 代理' : 'SOCKS5 代理' }}</p>
      <dl class="network-probes">
        <div v-for="probe in probes" :key="probe.name"><dt>{{ probe.name }}</dt><dd><strong :class="probe.result.state === 'reachable' ? 'network-reachable' : probe.result.state === 'not_applicable' ? 'muted' : 'warning-text'">{{ networkProbeLabel(probe.result) }}</strong><span v-if="probe.result.latency_ms !== null"> · {{ probe.result.latency_ms }} ms</span><span v-if="probe.result.http_status !== null"> · HTTP {{ probe.result.http_status }}</span></dd></div>
      </dl>
      <template v-if="result.local_api">
        <p class="field-hint">检测时管理状态：{{ result.local_api.managed ? localApiStates[result.local_api.state] : '外部管理' }}；最近心跳：{{ result.local_api.last_heartbeat_at ? new Date(result.local_api.last_heartbeat_at).toLocaleString('zh-CN', { hour12: false }) : '未收到' }}。</p>
      </template>
      <p class="field-hint">检测时代理生效状态：{{ proxyLabels[result.proxy_status.state] || '待确认' }}（保存版本 {{ result.proxy_status.desired_revision ?? '—' }} / 应用版本 {{ result.proxy_status.applied_revision ?? '—' }}）。</p>
      <ul class="network-advice"><li v-for="hint in advice" :key="hint">{{ hint }}</li></ul>
    </div>
  </section>
</template>
<style scoped>
.network-test-panel { border-top: 1px solid var(--line); padding-top: 20px; margin-top: 20px; }
.diagnostic-title { flex-wrap: wrap; gap: 12px; }
.diagnostic-title h3 { margin: 0; }
.diagnostic-title .el-button { min-height: 44px; }
.network-probes { margin: 14px 0; }
.network-probes > div { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 4px 16px; padding: 8px 0; }
.network-probes dd { margin: 0; overflow-wrap: anywhere; }
.network-reachable { color: var(--el-color-success); }
.network-advice { padding-left: 20px; line-height: 1.7; font-size: 13px; }
.network-advice li + li { margin-top: 6px; }
@media (max-width: 600px) { .network-probes > div { display: block; } .network-probes dd { margin-top: 4px; } }
</style>
