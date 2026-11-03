<script setup lang="ts">
import { bytes } from '../api/format'
import type { StorageFailure } from '../api/types'

defineProps<{ failure: StorageFailure }>()
const phases = { before_download: '下载前空间检查', before_save: '保存前空间检查', downloading: '下载中空间不足', saving: '保存中空间不足' }
const locations = { target: '保存盘', cache: '缓存盘', 'target+cache': '缓存与保存盘（同盘）' }
</script>
<template>
  <div class="storage-failure" aria-label="空间检查详情">
    <p class="storage-heading">{{ phases[failure.phase] }}</p>
    <div v-for="(volume, index) in failure.volumes" :key="index" class="volume-budget">
      <strong>{{ locations[volume.location] }}</strong>
      <p>可用 {{ bytes(volume.free_bytes) }} · 本次需要 {{ bytes(volume.required_bytes) }}</p>
      <p>并发预留 {{ bytes(volume.concurrent_bytes) }} · 安全余量 {{ bytes(volume.reserve_bytes) }}</p>
      <p v-if="volume.missing_bytes != null && volume.missing_bytes > 0" class="warning-text">尚缺 {{ bytes(volume.missing_bytes) }}</p>
      <p v-if="volume.free_bytes == null" class="warning-text">无法确认可用空间，请检查存储连接与权限后重试。</p>
    </div>
    <p class="muted">空间为本次检查时的记录，其他任务或程序仍可能占用磁盘。</p>
  </div>
</template>
<style scoped>
.storage-failure { margin: 12px 0; font-size: 12px; overflow-wrap: anywhere; line-height: 1.7; }
.storage-failure p { margin: 4px 0; }
.storage-heading { font-weight: 600; }
.volume-budget { margin: 8px 0; padding-left: 10px; border-left: 2px solid var(--el-border-color); }
</style>
