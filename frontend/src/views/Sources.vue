<template>
  <div class="sources-page">
    <!-- 数据源列表 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="title">数据源</span>
          <span class="subtitle">看最近 {{ data.window || 5 }} 次调用</span>
          <div class="header-tags">
            <el-tag type="success" effect="light" size="small">正常 {{ counts.ok || 0 }}</el-tag>
            <el-tag type="warning" effect="light" size="small">降级 {{ counts.degraded || 0 }}</el-tag>
            <el-tag type="danger" effect="light" size="small">不可用 {{ counts.down || 0 }}</el-tag>
            <el-tag type="info" effect="light" size="small">未调用 {{ counts.unknown || 0 }}</el-tag>
          </div>
          <el-button size="small" :loading="busy" @click="load">
            <el-icon><Refresh /></el-icon>
            <span>刷新</span>
          </el-button>
        </div>
      </template>

      <el-table
        v-loading="busy"
        :data="data.items || []"
        stripe
        size="default"
        empty-text=""
      >
        <template #empty>
          <el-empty description="还没有任何调用记录" :image-size="80" />
        </template>

        <el-table-column label="状态" width="70" align="center">
          <template #default="{ row }">
            <el-tooltip :content="statusLabel(row.status)" placement="top">
              <span class="status-dot" :class="row.status"></span>
            </el-tooltip>
          </template>
        </el-table-column>

        <el-table-column prop="name" label="源" min-width="140">
          <template #default="{ row }">
            <span class="source-name">{{ row.name }}</span>
            <el-tag v-if="row.breakerOpen" type="danger" size="small" effect="dark" style="margin-left:6px">
              熔断中
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="用途" min-width="160">
          <template #default="{ row }">
            <span class="muted">{{ row.capabilityLabel || row.capability || '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column label="最近 5 次" width="100" align="right">
          <template #default="{ row }">
            <span class="num">{{ row.okCount }}/{{ row.calls }}</span>
          </template>
        </el-table-column>

        <el-table-column label="成功率" width="90" align="right">
          <template #default="{ row }">
            <span class="num">{{ row.ratePct === null ? '—' : row.ratePct + '%' }}</span>
          </template>
        </el-table-column>

        <el-table-column label="平均延迟" width="100" align="right">
          <template #default="{ row }">
            <span class="num">{{ row.avgLatencyMs === null ? '—' : row.avgLatencyMs + ' ms' }}</span>
          </template>
        </el-table-column>

        <el-table-column label="上次成功/错误" min-width="180">
          <template #default="{ row }">
            <el-tooltip v-if="row.lastError" :content="row.lastError" placement="top">
              <span class="err-text">{{ row.lastError }}</span>
            </el-tooltip>
            <span v-else class="muted small">{{ row.lastCalledAt || '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="150" align="center" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :loading="testing === row.name" @click="probe(row.name)">
              测试
            </el-button>
            <el-button
              size="small"
              :type="row.enabled ? 'info' : 'success'"
              @click="toggle(row)"
            >
              {{ row.enabled ? '关闭' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-alert
        v-if="probeResult"
        :type="probeResult.ok ? 'success' : 'error'"
        :closable="false"
        show-icon
        style="margin-top: 12px"
      >
        <template #title>
          测试 {{ probeResult.source }}：{{ probeResult.ok ? '通过' : '失败' }}
          <template v-if="probeResult.supported === false">（{{ probeResult.error }}）</template>
          <template v-else-if="probeResult.error">（{{ probeResult.error }}）</template>
          <template v-else>（{{ probeResult.latencyMs }} ms）</template>
        </template>
      </el-alert>
    </el-card>

    <!-- 降级规则 -->
    <el-card shadow="never">
      <template #header>
        <span class="title">降级规则</span>
      </template>
      <el-table :data="data.rules || []" size="default" :show-header="false">
        <el-table-column label="状态" width="160">
          <template #default="{ row }">
            <span class="status-dot" :class="row.level" style="margin-right: 8px"></span>
            {{ row.label }}
          </template>
        </el-table-column>
        <el-table-column prop="rule" label="判定">
          <template #default="{ row }">
            <span class="muted">{{ row.rule }}</span>
          </template>
        </el-table-column>
      </el-table>
      <p class="lead">
        单个源挂掉不会中断整轮更新：其它票照常算完，失败的那只记「数据缺失」，
        不编数、不当 0 分。限流规则：单域名并发
        <b>{{ data.limits?.concurrencyPerDomain }}</b>，间隔
        <b>{{ data.limits?.minIntervalMs }}–{{ data.limits?.maxIntervalMs }} ms</b>，
        连续 <b>{{ data.limits?.failThreshold }}</b> 次失败熔断
        <b>{{ Math.round((data.limits?.openSeconds || 600) / 60) }} 分钟</b>。
      </p>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { api } from '../api/client.js'

const data = ref({ items: [], rules: [], counts: {}, limits: {} })
const busy = ref(false)
const testing = ref('')
const probeResult = ref(null)

const counts = computed(() => data.value.counts || {})

function statusLabel(s) {
  return { ok: '正常', degraded: '降级', down: '不可用', disabled: '已关闭', unknown: '未知' }[s] || s
}

async function load() {
  busy.value = true
  try {
    data.value = await api.sources()
  } catch (e) {
    ElMessage.error(`读取失败：${e.message}`)
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
    ElMessage.error(`测试失败：${e.message}`)
  } finally {
    testing.value = ''
  }
}

async function toggle(s) {
  try {
    await api.toggleSource({ name: s.name, enabled: !s.enabled })
    ElMessage.success(s.enabled ? '已关闭' : '已启用')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

onMounted(load)
</script>

<style scoped>
.sources-page { display: flex; flex-direction: column; gap: 16px; }

.card-header {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.card-header .title { font-weight: 600; font-size: 14px; }
.card-header .subtitle { font-size: 12px; color: var(--text-tertiary); }
.header-tags {
  display: flex;
  gap: 6px;
  margin-left: auto;
}

.num { font-variant-numeric: tabular-nums; }
.muted { color: var(--text-tertiary); }
.small { font-size: 11.5px; }
.source-name { font-weight: 500; }

.status-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #dfe3e9;
  vertical-align: middle;
}
.status-dot.ok { background: var(--success); box-shadow: 0 0 0 3px rgba(15, 157, 88, 0.15); }
.status-dot.degraded { background: #e8a33d; box-shadow: 0 0 0 3px rgba(232, 163, 61, 0.15); }
.status-dot.down { background: var(--danger); box-shadow: 0 0 0 3px rgba(217, 48, 37, 0.15); }
.status-dot.disabled { background: #c6cbd4; }
.status-dot.unknown { background: #dfe3e9; border: 1px solid var(--border-strong); }

.err-text {
  color: var(--danger);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
  display: inline-block;
  vertical-align: middle;
}

.lead {
  margin: 14px 0 0;
  font-size: 12.5px;
  color: var(--text-secondary);
  line-height: 1.8;
}
</style>
