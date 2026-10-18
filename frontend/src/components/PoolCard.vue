<template>
  <button class="pool-card" :class="`pc-${pool}`" @click="$emit('pick', pool)">
    <span class="pc-label">{{ label }}</span>
    <span class="pc-count num">{{ count }}</span>
    <span v-if="note" class="pc-note">{{ note }}</span>
  </button>
</template>

<script setup>
defineProps({
  pool: { type: String, required: true },
  label: { type: String, required: true },
  count: { type: Number, default: 0 },
  note: { type: String, default: '' },
})
defineEmits(['pick'])
</script>

<style scoped>
.pool-card {
  flex: 1;
  min-width: 0;
  text-align: left;
  padding: 12px 13px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: #fff;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
  transition: border-color 0.12s, box-shadow 0.12s;
}
.pool-card:hover { border-color: var(--purple); box-shadow: 0 2px 8px rgba(91, 58, 143, 0.1); }
.pc-label { font-size: 11.5px; color: var(--fg-3); }
.pc-count { font-size: 22px; font-weight: 600; line-height: 1.25; }
.pc-note { font-size: 11px; color: var(--fg-3); }
.pc-candidate .pc-count { color: #b42318; }
.pc-observation .pc-count { color: #96620b; }
.pc-reserve .pc-count { color: #2b4d9e; }
.pc-tuna .pc-count { color: #5b3a8f; }
.pc-removed .pc-count { color: #667085; }
</style>
