<script setup lang="ts">
import { computed } from 'vue'
import type { MediaItem } from '../api/types'
import { bytes } from '../api/format'
import { mediaProgress } from '../api/mediaProgress'

const props = defineProps<{ item: MediaItem }>()
const progress = computed(() => mediaProgress(props.item))
</script>

<template>
  <div class="media-progress">
    <p class="progress-caption">{{ progress.label }}<span v-if="item.elapsed_seconds != null"> · {{ Math.floor(item.elapsed_seconds) }} 秒</span></p>
    <el-progress v-if="progress.percentage != null" :percentage="progress.percentage" :stroke-width="6" />
    <div v-else class="indeterminate-progress" role="progressbar" :aria-label="progress.label" aria-valuemin="0" aria-valuemax="100"><span /></div>
    <p v-if="progress.measured" class="muted">{{ progress.verb }} {{ bytes(item.completed_bytes) }}<template v-if="item.total_bytes != null"> / {{ bytes(item.total_bytes) }}</template><template v-if="item.speed_bps != null"> · {{ bytes(item.speed_bps) }}/s</template></p>
    <p v-else class="muted">{{ progress.explanation }}</p>
  </div>
</template>

<style scoped>
.indeterminate-progress { overflow: hidden; height: 6px; border-radius: 3px; background: var(--el-fill-color-light); }
.indeterminate-progress span { display: block; width: 30%; height: 100%; border-radius: inherit; background: var(--el-color-primary); animation: download-pending 1.8s ease-in-out infinite alternate; }
@keyframes download-pending { from { transform: translateX(0); } to { transform: translateX(233%); } }
@media (prefers-reduced-motion: reduce) { .indeterminate-progress span { animation: none; } }
</style>
