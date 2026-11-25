<script setup lang="ts">
import { provide, ref } from 'vue'
import { boundedRequest } from '../api/boundedRequest'
import type { SystemStatus } from '../api/types'
import { usePolling } from '../composables/usePolling'
import { systemKey } from '../composables/useSystemStatus'

const status = usePolling((signal, reason) => boundedRequest<SystemStatus>(
  reason === 'automatic' ? '/system/status?sample_storage=true' : '/system/status', { signal }, 10000,
), 3000)
provide(systemKey, { ...status, debug: ref(false) })
</script>
<template><slot /></template>
