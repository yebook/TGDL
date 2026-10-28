<script setup lang="ts">
import { computed } from 'vue'
import type { MediaItem } from '../api/types'
import { bytes, duration } from '../api/format'
import { mediaProgress } from '../api/mediaProgress'

const props = defineProps<{ item: MediaItem }>()
const progress = computed(() => mediaProgress(props.item))
</script>

<template>
  <div class="media-progress">
    <p class="progress-caption">{{ progress.label }}<span v-if="item.elapsed_seconds != null"> · 耗时 {{ duration(item.elapsed_seconds) }}</span></p>
    <el-progress v-if="progress.percentage != null" :percentage="progress.percentage" :stroke-width="6" :aria-label="progress.label" />
    <div v-else class="indeterminate-progress" role="progressbar" :aria-label="progress.label" aria-valuemin="0" aria-valuemax="100"><span /></div>
    <p v-if="progress.measured" class="muted">{{ progress.verb }} {{ bytes(progress.completedBytes) }}<template v-if="progress.totalBytes != null"> / {{ progress.estimated ? '约 ' : '' }}{{ bytes(progress.totalBytes) }}</template><template v-if="progress.speedBps != null"> · {{ bytes(progress.speedBps) }}/s</template></p>
    <p v-if="progress.explanation" class="muted">{{ progress.explanation }}</p>
  </div>
</template>

<style scoped>
.indeterminate-progress { overflow: hidden; height: 6px; border-radius: 3px; background: var(--el-fill-color-light); }
.indeterminate-progress span { display: block; width: 30%; height: 100%; border-radius: inherit; background: var(--el-color-primary); animation: download-pending 1.8s ease-in-out infinite alternate; }
@keyframes download-pending { from { transform: translateX(0); } to { transform: translateX(233%); } }
@media (prefers-reduced-motion: reduce) { .indeterminate-progress span { animation: none; } }
</style>
