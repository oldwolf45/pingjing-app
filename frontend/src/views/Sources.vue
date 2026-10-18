<template>
  <div class="stack">
    <section class="card">
      <div class="card-head">
        <h2>数据源</h2>
        <span class="hint">看最近 {{ data.window || 5 }} 次调用</span>
        <div class="spacer"></div>
        <span class="tag ok">正常 {{ counts.ok || 0 }}</span>
        <span class="tag warn">降级 {{ counts.degraded || 0 }}</span>
        <span class="tag bad">不可用 {{ counts.down || 0 }}</span>
        <span class="tag">未调用 {{ counts.unknown || 0 }}</span>
        <button class="btn sm" :disabled="busy" @click="load">刷新</button>
      </div>
      <div class="card-body">
        <p v-if="error" class="err">{{ error }}</p>
        <div v-if="!data.items || !data.items.length" class="empty">还没有任何调用记录</div>
        <table v-else class="tbl">
          <thead>
            <tr>
              <th style="width: 42px">状态</th>
              <th>源</th>
              <th>用途</th>
              <th class="r">最近 5 次</th>
              <th class="r">成功率</th>
              <th class="r">平均延迟</th>
              <th>上次成功/错误</th>
              <th style="width: 120px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in data.items" :key="s.name">
              <td><span class="lamp" :class="s.status"></span></td>
              <td>{{ s.name }}</td>
              <td class="muted">{{ s.capabilityLabel || s.capability || '—' }}</td>
              <td class="r num">{{ s.okCount }}/{{ s.calls }}</td>
              <td class="r num">{{ s.ratePct === null ? '—' : s.ratePct + '%' }}</td>
              <td class="r num">{{ s.avgLatencyMs === null ? '—' : s.avgLatencyMs + ' ms' }}</td>
              <td class="small">
                <span v-if="s.lastError" class="errtext" :title="s.lastError">{{ s.lastError }}</span>
                <span v-else class="muted">{{ s.lastCalledAt || '—' }}</span>
                <div v-if="s.breakerOpen" class="errtext">熔断中</div>
              </td>
              <td>
                <button class="btn sm" :disabled="testing === s.name" @click="probe(s.name)">
                  {{ testing === s.name ? '测试中…' : '测试' }}
                </button>
                <button class="btn sm" @click="toggle(s)">
                  {{ s.enabled ? '关闭' : '启用' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="probeResult" class="probe">
          测试 {{ probeResult.source }}：{{ probeResult.ok ? '通过' : '失败' }}
          <template v-if="probeResult.supported === false">（{{ probeResult.error }}）</template>
          <template v-else-if="probeResult.error">（{{ probeResult.error }}）</template>
          <template v-else>（{{ probeResult.latencyMs }} ms）</template>
        </p>
      </div>
    </section>

    <section class="card">
      <div class="card-head"><h2>降级规则</h2></div>
      <div class="card-body">
        <table class="tbl rules">
          <thead>
            <tr><th style="width: 130px">状态</th><th>判定</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in data.rules || []" :key="r.level">
              <td><span class="lamp" :class="r.level"></span> {{ r.label }}</td>
              <td class="muted">{{ r.rule }}</td>
            </tr>
          </tbody>
        </table>
        <p class="lead">
          单个源挂掉不会中断整轮更新：其它票照常算完，失败的那只记「数据缺失」，
          不编数、不当 0 分。限流规则：单域名并发
          {{ data.limits?.concurrencyPerDomain }}，间隔
          {{ data.limits?.minIntervalMs }}–{{ data.limits?.maxIntervalMs }} ms，
          连续 {{ data.limits?.failThreshold }} 次失败熔断
          {{ Math.round((data.limits?.openSeconds || 600) / 60) }} 分钟。
        </p>
      </div>
    </section>
  </div>
</template>

<script setup>
// T5.7 数据源健康：四色状态灯 + 测试按钮 + 降级规则说明。
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client.js'

const data = ref({ items: [], rules: [], counts: {}, limits: {} })
const error = ref('')
const busy = ref(false)
const testing = ref('')
const probeResult = ref(null)

const counts = computed(() => data.value.counts || {})

async function load() {
  busy.value = true
  try {
    data.value = await api.sources()
    error.value = ''
  } catch (e) {
    error.value = `读取失败：${e.message}`
  } finally {
    busy.value = false
  }
}

async function probe(name) {
  testing.value = name
  probeResult.value = null
  try {
    probeResult.value = await api.testSource({ name })
    await load()
  } catch (e) {
    error.value = `测试失败：${e.message}`
  } finally {
    testing.value = ''
  }
}

async function toggle(s) {
  try {
    await api.toggleSource({ name: s.name, enabled: !s.enabled })
    await load()
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<style scoped>
.stack { display: flex; flex-direction: column; gap: 14px; }
.r { text-align: right; }
.err { color: var(--red); font-size: 12px; }
.errtext { color: var(--red); font-size: 11px; }
.probe { font-size: 12px; color: var(--fg-2); margin: 8px 0 0; }
.lead { font-size: 12px; color: var(--fg-2); line-height: 1.8; margin: 10px 0 0; }

.lamp {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--fg-3);
  vertical-align: middle;
}
.lamp.ok { background: var(--green); }
.lamp.degraded { background: #e8a33d; }
.lamp.down { background: var(--red); }
.lamp.disabled { background: #c6cbd4; }
.lamp.unknown { background: #dfe3e9; border: 1px solid var(--line-strong); }

.tag.ok { color: var(--green); }
.tag.warn { color: var(--amber); }
.tag.bad { color: var(--red); }
.rules td { font-size: 12px; }
</style>
