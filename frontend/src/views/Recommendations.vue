<template>
  <div class="rec-page">
    <!-- 统计卡片 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="title">推荐追踪</span>
          <span class="subtitle">快照只追加 · 三档分开算</span>
        </div>
      </template>

      <el-row :gutter="12" class="stats-row">
        <el-col :sm="12" :md="6" :span="24">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-label">累计推荐</div>
            <div class="stat-value">{{ stats.total ?? 0 }}</div>
            <div class="stat-note">每一条都留了当时的评分和价格</div>
          </el-card>
        </el-col>
        <el-col v-for="t in tierList" :key="t.key" :sm="12" :md="6" :span="24">
          <el-card shadow="hover" class="stat-card" :class="`tier-${t.key}`">
            <div class="stat-label">{{ t.label }}</div>
            <div class="stat-value">
              <template v-if="t.sampleEnough">
                {{ t.rate }}<span class="pct">%</span>
              </template>
              <template v-else>—</template>
            </div>
            <div class="stat-note">
              <template v-if="t.sampleEnough">{{ t.hit }} / {{ t.evaluated }} 条命中</template>
              <template v-else>{{ t.sampleNote }}</template>
            </div>
            <div class="stat-hint">{{ t.hint }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="lead-alert"
      >
        <template #title>
          三档统计永远分开：看错方向（硬失误）、方向对但没赚（到期不赚）、赚了但没跑赢大盘（跑输大盘），要改的东西完全不同，合成一个"成功率"等于什么都没说。
        </template>
      </el-alert>
    </el-card>

    <!-- 推荐记录 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="title">推荐记录</span>
          <span class="subtitle">{{ items.length }} 条</span>
        </div>
      </template>

      <el-table
        :data="items"
        stripe
        size="default"
        empty-text=""
      >
        <template #empty>
          <el-empty
            description="还没有推荐记录。在个股详情页点「记一条推荐」，就会留一张当时的快照。"
            :image-size="80"
          />
        </template>

        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="expand-detail">
              <div class="detail-block">
                <div class="dt">当时快照</div>
                <pre>{{ pretty(row.snapshotDecoded) }}</pre>
              </div>
              <div class="detail-block">
                <div class="dt">跟踪曲线（{{ (row.tracks || []).length }} 个点）</div>
                <div v-if="!(row.tracks || []).length" class="muted small">还没有跟踪数据，跑一次更新会补</div>
                <el-table v-else :data="row.tracks" size="small" border style="max-width: 360px">
                  <el-table-column prop="as_of_date" label="日期" width="100">
                    <template #default="{ row: t }">
                      <span class="num">{{ t.as_of_date }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="价格" width="80" align="right">
                    <template #default="{ row: t }">
                      <span class="num">{{ t.price_cents ? (t.price_cents / 100).toFixed(2) : '—' }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="涨跌" width="80" align="right">
                    <template #default="{ row: t }">
                      <span class="num" :class="pnlClass(t.pct_change)">
                        {{ t.pct_change === null ? '—' : t.pct_change + '%' }}
                      </span>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="as_of_date" label="日期" width="100">
          <template #default="{ row }">
            <span class="num">{{ row.as_of_date }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="code" label="代码" width="75">
          <template #default="{ row }">
            <span class="num muted">{{ row.code }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="name" label="名称" min-width="100">
          <template #default="{ row }">{{ row.stockName || row.name }}</template>
        </el-table-column>

        <el-table-column label="推荐时分数" width="100" align="right">
          <template #default="{ row }">
            <span class="num">
              {{ row.total_score === null ? '—' : row.total_score.toFixed(2) }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="推荐价" width="90" align="right">
          <template #default="{ row }">
            <span class="num">{{ row.price_cents ? (row.price_cents / 100).toFixed(2) : '—' }}</span>
          </template>
        </el-table-column>

        <el-table-column label="最新价" width="90" align="right">
          <template #default="{ row }">
            <span class="num">{{ lastPrice(row) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="涨跌" width="80" align="right">
          <template #default="{ row }">
            <span class="num" :class="pnlClass(lastPct(row))">
              {{ lastPct(row) === null ? '—' : lastPct(row) + '%' }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="三档判定" min-width="240">
          <template #default="{ row }">
            <el-tag
              v-for="t in tierList"
              :key="t.key"
              size="small"
              effect="plain"
              :type="verdictTagType(row, t.key)"
              style="margin-right: 4px"
            >
              {{ t.label }}·{{ verdictText(row, t.key) }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 还不能画的图 -->
    <el-card shadow="never">
      <template #header>
        <span class="title">还不能画的图</span>
      </template>
      <el-row :gutter="12">
        <el-col :sm="12" :span="24" style="margin-bottom: 12px">
          <el-card class="stat-card muted-card" shadow="never">
            <div class="stat-label">命中率按评分档位</div>
            <div class="stat-value">—</div>
            <div class="stat-note">{{ stats.byScoreBand?.note || '样本够了才能画' }}</div>
          </el-card>
        </el-col>
        <el-col :sm="12" :span="24" style="margin-bottom: 12px">
          <el-card class="stat-card muted-card" shadow="never">
            <div class="stat-label">维度归因</div>
            <div class="stat-value">—</div>
            <div class="stat-note">{{ stats.byDimension?.note || '样本够了才能画' }}</div>
          </el-card>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client.js'

const stats = ref({ total: 0, tiers: {}, byScoreBand: {}, byDimension: {} })
const items = ref([])

const tierList = computed(() => ['hard_miss', 'flat', 'lag_benchmark'].map((k) => ({
  key: k,
  label: stats.value.tiers?.[k]?.label || k,
  hint: stats.value.tiers?.[k]?.hint || '',
  rate: stats.value.tiers?.[k]?.rate,
  hit: stats.value.tiers?.[k]?.hit ?? 0,
  evaluated: stats.value.tiers?.[k]?.evaluated ?? 0,
  sampleEnough: !!stats.value.tiers?.[k]?.sampleEnough,
  sampleNote: stats.value.tiers?.[k]?.sampleNote || '',
})))

function pretty(obj) {
  if (!obj) return '（快照缺失）'
  return JSON.stringify(obj, null, 1)
}

function lastTrack(r) {
  const ts = r.tracks || []
  return ts.length ? ts[ts.length - 1] : null
}
function lastPrice(r) {
  const t = lastTrack(r)
  return t && t.price_cents ? (t.price_cents / 100).toFixed(2) : '—'
}
function lastPct(r) {
  const t = lastTrack(r)
  return t && t.pct_change !== null ? t.pct_change : null
}
function pnlClass(v) {
  if (v === null || v === undefined) return ''
  return v > 0 ? 'up' : v < 0 ? 'down' : ''
}
function verdictText(r, tier) {
  const v = r.evaluations?.[tier]?.verdict
  return { hit: '命中', miss: '未命中', pending: '待定' }[v] || '待定'
}
function verdictTagType(r, tier) {
  const v = r.evaluations?.[tier]?.verdict
  return { hit: 'danger', miss: 'success', pending: 'info' }[v] || 'info'
}

async function load() {
  try {
    const d = await api.recommendations()
    items.value = d.items || []
    stats.value = d.stats || stats.value
  } catch (e) {
    // 错误由 toast 处理
  }
}

onMounted(load)
</script>

<style scoped>
.rec-page { display: flex; flex-direction: column; gap: 16px; }

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-header .title { font-weight: 600; font-size: 14px; }
.card-header .subtitle { font-size: 12px; color: var(--text-tertiary); }

.stats-row { margin-bottom: 12px; }

.stat-card {
  border-radius: 10px;
  transition: all 0.2s;
}
.stat-card :deep(.el-card__body) {
  padding: 16px;
}
.stat-label { font-size: 12px; color: var(--text-tertiary); }
.stat-value {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.3;
  margin: 4px 0;
  color: var(--text-primary);
}
.stat-value .pct {
  font-size: 13px;
  font-weight: 400;
  margin-left: 2px;
  color: var(--text-tertiary);
}
.stat-note { font-size: 11.5px; color: var(--text-secondary); }
.stat-hint { font-size: 10.5px; color: var(--text-tertiary); margin-top: 4px; }

.tier-hard_miss .stat-value { color: var(--danger); }
.tier-flat .stat-value { color: var(--warning); }
.tier-lag_benchmark .stat-value { color: var(--primary); }

.muted-card {
  opacity: 0.7;
  background: repeating-linear-gradient(
    45deg, #fafafa, #fafafa 6px, #f2f3f5 6px, #f2f3f5 12px
  ) !important;
}

.lead-alert { margin-top: 4px; }

.num { font-variant-numeric: tabular-nums; }
.muted { color: var(--text-tertiary); }
.small { font-size: 11.5px; }
.up { color: var(--danger); }
.down { color: var(--success); }

.expand-detail {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
.detail-block { flex: 1; min-width: 280px; }
.dt { font-size: 11.5px; color: var(--text-tertiary); margin-bottom: 6px; font-weight: 500; }
pre {
  margin: 0;
  font-size: 11px;
  background: #fafbfc;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px;
  max-height: 220px;
  overflow: auto;
  line-height: 1.6;
}
</style>
