<script setup lang="ts">
import { useSystemStatus } from '../composables/useSystemStatus'
const { debug } = useSystemStatus()
defineProps<{ source?: string; editable: boolean }>()
</script>
<template>
  <span v-if="debug || !editable" class="settings-source" :class="{ 'settings-source--locked': !editable }">{{ !editable ? '由部署配置管理 · 只读' : source === 'database' ? '已保存' : source === 'environment' ? '部署配置' : '默认' }}</span>
</template>
