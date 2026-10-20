<script setup lang="ts">
import { stateLabel, states } from '../api/format'
defineProps<{ status: string }>()
</script>
<template><el-tag :type="states[status]?.tone ?? 'info'" effect="light" round>{{ stateLabel(status) }}</el-tag></template>
