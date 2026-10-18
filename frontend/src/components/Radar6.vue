<template>
  <div ref="el" class="radar"></div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  // [{key,label,score,full,missing}]
  dimensions: { type: Array, required: true },
  range: { type: Number, default: 2.5 },
})

const el = ref(null)
let chart = null

function indicators() {
  return props.dimensions.map((d) => ({
    // 轴标签直接写「得分/满分」，缺数据写「缺数据」，不写 0
    name: d.missing ? `${d.label}\n缺数据` : `${d.label}\n${fmt(d.score)}/${fmt(d.full)}`,
    max: props.range,
  }))
}

function fmt(v) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(2).replace(/0$/, '').replace(/\.$/, '')
}

function values() {
  // 缺数据给 null：ECharts 会断开而不是画到圆心（画到圆心等于说它是 0 分）
  return props.dimensions.map((d) => (d.missing ? null : d.score))
}

function render() {
  if (!chart) return
  chart.setOption(
    {
      tooltip: {
        trigger: 'item',
        formatter: () => {
          const rows = props.dimensions
            .map((d) =>
              d.missing
                ? `${d.label}：缺数据`
                : `${d.label}：${fmt(d.score)} / ${fmt(d.full)}`
            )
            .join('<br/>')
          return rows
        },
      },
      radar: {
        center: ['50%', '52%'],
        radius: '62%',
        splitNumber: 5,
        axisName: { color: '#5a6473', fontSize: 11, lineHeight: 15 },
        splitLine: { lineStyle: { color: '#e4e7ec' } },
        splitArea: { areaStyle: { color: ['#fbfbfd', '#ffffff'] } },
        axisLine: { lineStyle: { color: '#e4e7ec' } },
        indicator: indicators(),
      },
      series: [
        {
          type: 'radar',
          symbolSize: 4,
          lineStyle: { color: '#5b3a8f', width: 1.6 },
          itemStyle: { color: '#5b3a8f' },
          areaStyle: { color: 'rgba(91,58,143,0.14)' },
          data: [{ value: values() }],
        },
      ],
    },
    true
  )
}

function resize() {
  if (chart) chart.resize()
}

onMounted(() => {
  chart = echarts.init(el.value)
  render()
  window.addEventListener('resize', resize)
})

onUnmounted(() => {
  window.removeEventListener('resize', resize)
  if (chart) chart.dispose()
  chart = null
})

watch(() => [props.dimensions, props.range], render, { deep: true })
</script>

<style scoped>
.radar { width: 100%; height: 268px; }
</style>
