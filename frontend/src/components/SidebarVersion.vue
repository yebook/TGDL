<script setup lang="ts">
import { useSystemStatus } from '../composables/useSystemStatus'
const { data } = useSystemStatus()
</script>
<template>
  <div class="sidebar-version" aria-label="应用版本"><span>{{ data?.version || '正在读取' }}</span><a href="https://yebook.github.io/TGDL/changelog.html" target="_blank" rel="noopener noreferrer">更新日志</a></div>
</template>
