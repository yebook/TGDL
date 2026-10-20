<script setup lang="ts">
import { request } from '../api/client'
import type { LocalApiStatus, SystemStatus } from '../api/types'
import { bytes } from '../api/format'
import { usePolling } from '../composables/usePolling'
import LocalApiPanel from '../components/LocalApiPanel.vue'
const { data, error, loading, updatedAt, refresh } = usePolling(signal => request<SystemStatus>('/system/status', { signal }), 3000)
function localApiChanged(status: LocalApiStatus) {
  if (data.value) data.value.local_api = status
  void refresh()
}
const labels: Record<string, string> = { connected: '已连接', online: '已连接', connecting: '正在连接', running: '运行中', ready: '已就绪', unconfigured: '尚未配置', disconnected: '未连接', error: '连接异常', stopped: '已停止', disabled: '未启用', applied: '已应用', active: '已应用', applying: '正在应用', pending: '等待应用', pending_restart: '需要重启', restart_required: '需要重启', failed: '应用失败', unavailable: '不可用', direct: '直连模式' }
const good = (state: string) => ['connected', 'online', 'running', 'ready', 'applied', 'active', 'direct', 'disabled'].includes(state)
</script>
<template>
  <section>
    <div class="page-heading"><div><p class="eyebrow">SYSTEM</p><h1>系统状态</h1><p class="muted">检查连接、存储与下载调度。</p></div><el-button :loading="loading" @click="refresh">刷新状态</el-button></div>
    <el-alert v-if="error" :title="`${error}${data ? ' 当前显示上次获取的状态。' : ''}`" type="error" :closable="false" show-icon />
    <el-skeleton v-if="!data && loading" :rows="8" animated />
    <template v-if="data">
      <div class="system-grid">
        <section class="panel diagnostic-card"><p class="eyebrow">TELEGRAM BOT</p><div class="diagnostic-title"><h2>{{ data.bot.username ? `@${data.bot.username}` : 'Bot 连接' }}</h2><el-tag :type="good(data.bot.state) ? 'success' : 'warning'" round>{{ labels[data.bot.state] || data.bot.state }}</el-tag></div><p class="muted">{{ data.bot.message || '在设置中配置 Bot Token 和用户白名单。' }}</p></section>
        <section class="panel diagnostic-card"><p class="eyebrow">DOWNLOAD QUEUE</p><h2>下载队列</h2><div class="queue-stats"><div><strong>{{ data.queue.active }}</strong><span>正在执行</span></div><div><strong>{{ data.queue.pending }}</strong><span>等待执行</span></div><div><strong>{{ data.queue.concurrency }}</strong><span>并发上限</span></div></div></section>
      </div>
      <section class="panel diagnostic-card"><LocalApiPanel :status="data.local_api" :bot="data.bot" :stale="!!error" detailed @changed="localApiChanged" /></section>
      <section class="panel diagnostic-card"><div class="diagnostic-title"><div><p class="eyebrow">STORAGE</p><h2>下载存储</h2></div><span class="muted">目录由部署环境提供</span></div><div v-for="root in data.storage.roots" :key="root.id" class="storage-row"><div><strong>{{ root.label }}</strong><p class="muted">{{ root.message || (root.available ? '存储检查正常' : '请检查挂载和权限') }}</p></div><div><el-tag :type="root.available ? 'success' : 'danger'" round>{{ root.available ? '可用' : '不可用' }}</el-tag><p class="muted">剩余 {{ bytes(root.free_bytes) }} / {{ bytes(root.total_bytes) }}</p></div></div><p class="field-hint">Telegram 缓存可用空间：{{ bytes(data.storage.cache.free_bytes) }} / {{ bytes(data.storage.cache.total_bytes) }}。下载存储与缓存都需要足够空间。</p></section>
      <div class="system-grid"><section class="panel diagnostic-card"><p class="eyebrow">NETWORK</p><div class="diagnostic-title"><h2>代理链路</h2><el-tag :type="good(data.proxy.state) ? 'success' : 'warning'" round>{{ labels[data.proxy.state] || data.proxy.state }}</el-tag></div><p class="muted">{{ data.proxy.message || '查看已保存与实际生效配置是否一致。' }}</p><dl class="small-meta"><div><dt>期望配置版本</dt><dd>{{ data.proxy.desired_revision ?? '—' }}</dd></div><div><dt>已应用版本</dt><dd>{{ data.proxy.applied_revision ?? '—' }}</dd></div></dl></section><section class="panel diagnostic-card"><p class="eyebrow">RECOVERY</p><h2>恢复与诊断</h2><p class="muted">{{ data.recovery.message || '没有待处理的恢复问题。' }}</p><p v-if="data.recovery.blocked_jobs" class="warning-text">{{ data.recovery.blocked_jobs }} 个任务需要处理</p><p class="field-hint">应用版本 {{ data.version }}</p></section></div>
      <p class="system-footer muted">{{ loading ? '正在检查…' : `最近更新 ${updatedAt?.toLocaleTimeString('zh-CN', { hour12: false }) || '—'} · 每 3 秒刷新` }}</p>
    </template>
  </section>
</template>
