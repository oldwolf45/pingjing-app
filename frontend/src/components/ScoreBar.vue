<template>
  <span class="sb-wrap">
    <span class="scorebar" :class="level">
      <i v-if="!missing" :style="{ width: pct + '%' }"></i>
    </span>
    <span class="sb-num num" :class="{ muted: missing }">{{ text }}</span>
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  value: { type: Number, default: null },
  full: { type: Number, default: 9.0 },
  digits: { type: Number, default: 2 },
})

// 缺数据不画 0 分条，显示「缺数据」
const missing = computed(() => props.value === null || props.value === undefined)

const pct = computed(() => {
  if (missing.value || !props.full) return 0
  return Math.max(0, Math.min(100, (props.value / props.full) * 100))
})

const text = computed(() => (missing.value ? '缺数据' : props.value.toFixed(props.digits)))

const level = computed(() => {
  if (missing.value) return 'low'
  const r = props.value / props.full
  if (r >= 0.7) return ''
  if (r >= 0.45) return 'warn'
  return 'low'
})
</script>

<style scoped>
.sb-wrap { display: inline-flex; align-items: center; gap: 8px; }
.sb-num { font-size: 12.5px; min-width: 40px; }
.sb-num.muted { color: #8b95a5; }
</style>
