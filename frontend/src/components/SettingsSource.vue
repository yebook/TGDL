<script setup lang="ts">
defineProps<{ source?: string; editable: boolean }>()
</script>
<template>
  <span class="settings-source" :class="{ 'settings-source--locked': !editable }">{{ !editable ? '环境固定 · 只读' : source === 'database' ? '已保存' : source === 'deployment_fallback' ? '部署回退' : source === 'environment' ? '部署配置' : '默认' }}</span>
</template>
