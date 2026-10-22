<script setup lang="ts">
defineProps<{ name: 'download' | 'tasks' | 'settings' | 'system' | 'arrow' | 'refresh' }>()
const paths = {
  download: 'M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4',
  tasks: 'M8 5h12M8 12h12M8 19h12M3 5h.01M3 12h.01M3 19h.01',
  settings: 'M4 6h16M4 18h16M8 3v6M16 15v6M4 12h16M14 9v6',
  system: 'M4 4h16v12H4zM8 20h8m-4-4v4M7 8l3 2-3 2m6 0h4',
  arrow: 'm9 5 7 7-7 7',
  refresh: 'M20 7v5h-5m5 0a8 8 0 1 0-2.3 5.7',
}
</script>
<template><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name]" /></svg></template>
