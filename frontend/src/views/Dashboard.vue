<template>
  <div class="dash">
    <el-skeleton v-if="loading" :rows="8" animated />
    <el-alert v-else-if="error" :title="error" type="error" show-icon :closable="false" />

    <template v-else>
      <!-- 1 新鲜度提示条 -->
      <el-alert
        v-if="d.freshness.stale"
        type="warning"
        show-icon
        :closable="false"
        class="stale-alert"
      >
        <template #title>
          数据已 {{ d.freshness.days_since }} 天没更新，最近一次评分 {{ d.freshness.latest_score_date }}
          <el-link type="warning" style="margin-left: 12px" @click="goPools">去更新 →</el-link>
        </template>
      </el-alert>

      <el-row :gutter="16">
        <!-- 2 池子总览 -->
        <el-col :span="24" style="margin-bottom: 16px">
          <el-card shadow="never">
            <template #header>
              <div class="card-header">
                <span class="title">池子总览</span>
                <span class="subtitle">
                  名额 {{ d.pools.capacity }} · 在池 {{ d.pools.in_pool }} ·
                  {{ d.pools.gap > 0 ? `缺 ${d.pools.gap}` : '已满' }}
                </span>
              </div>
            </template>
            <el-row :gutter="12">
              <el-col
                v-for="p in poolOrder"
                :key="p"
                :xs="12" :sm="8" :md="4"
              >
                <div
                  class="pool-card"
                  :class="`pool-${p}`"
                  @click="goPoolsWith(p)"
                >
                  <div class="pool-label">{{ d.pools.labels[p] }}</div>
                  <div class="pool-count">{{ d.pools.counts[p] }}</div>
                  <div v-if="poolNote(p)" class="pool-note">{{ poolNote(p) }}</div>
                </div>
              </el-col>
            </el-row>
            <p class="footnote">
              在池 = 候选 + 观察 + 储备，金枪鱼池只跟踪不再新增投入，不占 28 个名额。
            </p>
          </el-card>
        </el-col>

        <!-- 3 待确认出池 -->
        <el-col :span="24" v-if="d.pending_tuna.length" style="margin-bottom: 16px">
          <el-card shadow="never">
            <template #header>
              <div class="card-header">
                <span class="title">待确认出池</span>
                <span class="subtitle">已命中出池线，下周再命中就移入金枪鱼池</span>
              </div>
            </template>
            <el-table :data="d.pending_tuna" size="default" @row-click="row => openStock(row.code)">
              <el-table-column prop="name" label="名称" min-width="120">
                <template #default="{ row }">
                  <span class="stock-name">{{ row.name }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="code" label="代码" width="90">
                <template #default="{ row }">
                  <span class="muted">{{ row.code }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="pool_label" label="所在池" width="100">
                <template #default="{ row }">
                  <el-tag :type="poolTagType(row.pool)" size="small" effect="plain">
                    {{ row.pool_label }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="tuna_reason" label="原因">
                <template #default="{ row }">
                  <span class="muted">{{ row.tuna_reason || '命中出池线' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="进度" width="100">
                <template #default="{ row }">
                  <el-progress
                    :percentage="row.tuna_weeks * 50"
                    :stroke-width="6"
                    :show-text="false"
                    status="warning"
                  />
                  <span class="small muted">{{ row.tuna_weeks }}/2 周</span>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="16">
        <!-- 4 评分排行 -->
        <el-col :lg="12" :span="24" style="margin-bottom: 16px">
          <el-card shadow="never">
            <template #header>
              <div class="card-header">
                <span class="title">评分排行</span>
                <span class="subtitle">前 7 名 · 满分 9.0</span>
              </div>
            </template>
            <el-empty v-if="!d.top_scores.length" description="还没有评分数据，点右上角更新" :image-size="80" />
            <el-table v-else :data="d.top_scores" size="default" @row-click="row => openStock(row.code)">
              <el-table-column type="index" label="#" width="45" align="center" />
              <el-table-column prop="name" label="名称" min-width="100">
                <template #default="{ row }">
                  <span class="stock-name">{{ row.name }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="code" label="代码" width="75">
                <template #default="{ row }">
                  <span class="muted small">{{ row.code }}</span>
                </template>
              </el-table-column>
              <el-table-column label="池" width="80">
                <template #default="{ row }">
                  <el-tag :type="poolTagType(row.pool)" size="small" effect="plain">
                    {{ labelOf(row.pool) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="评分" min-width="130">
                <template #default="{ row }">
                  <div class="score-cell">
                    <el-progress
                      :percentage="(row.total / 9) * 100"
                      :stroke-width="8"
                      :show-text="false"
                      color="var(--primary)"
                    />
                    <span class="score-num">{{ row.total?.toFixed(2) }}</span>
                    <el-tag v-if="row.is_fallback" type="warning" size="small" effect="light" style="margin-left:4px">
                      兜底
                    </el-tag>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>

        <!-- 5 最近池子变动 -->
        <el-col :lg="12" :span="24" style="margin-bottom: 16px">
          <el-card shadow="never">
            <template #header>
              <div class="card-header">
                <span class="title">最近池子变动</span>
                <span class="subtitle">最近 10 条</span>
              </div>
            </template>
            <el-empty v-if="!d.recent_events.length" description="本周暂无变动" :image-size="80" />
            <el-table v-else :data="d.recent_events" size="default" @row-click="row => openStock(row.code)">
              <el-table-column label="方向" width="50" align="center">
                <template #default="{ row }">
                  <span class="dir-icon" :class="`dir-${row.direction}`">{{ dirIcon(row.direction) }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="名称" min-width="90">
                <template #default="{ row }">
                  <span class="stock-name">{{ row.name || row.code }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="code" label="代码" width="70">
                <template #default="{ row }">
                  <span class="muted small">{{ row.code }}</span>
                </template>
              </el-table-column>
              <el-table-column label="变动" min-width="140">
                <template #default="{ row }">
                  <span class="muted small">{{ row.from_label }} → {{ row.to_label }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="change_date" label="日期" width="90" align="right">
                <template #default="{ row }">
                  <span class="muted small">{{ row.change_date }}</span>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="16">
        <!-- 6 名额缺口 -->
        <el-col :lg="12" :span="24" style="margin-bottom: 16px">
          <el-card shadow="never">
            <template #header>
              <div class="card-header">
                <span class="title">名额缺口</span>
              </div>
            </template>
            <div class="gap-box">
              <el-statistic v-if="d.pools.gap === 0" title="名额已满" :value="d.pools.in_pool" :suffix="`/ ${d.pools.capacity}`">
                <template #title>
                  <span class="gap-title">名额已满</span>
                </template>
              </el-statistic>
              <el-statistic v-else title="缺口" :value="d.pools.gap" value-style="color: #b42318">
                <template #title>
                  <span class="gap-title">缺口</span>
                </template>
              </el-statistic>
              <p class="gap-desc muted">
                <template v-if="d.pools.gap === 0">
                  在池 {{ d.pools.in_pool }} / {{ d.pools.capacity }}，补票会等有票出池后再补
                </template>
                <template v-else>
                  补票必须过四道关：情报已录入 · 财务拉全 · 分数 ≥4.5 · 市值 ≤250 亿。补不上就空着，不降标准。
                </template>
              </p>
              <el-button type="primary" size="small" @click="goPools">去股票池看看</el-button>
            </div>
          </el-card>
        </el-col>

        <!-- 7 接近升级 -->
        <el-col :lg="12" :span="24" style="margin-bottom: 16px">
          <el-card shadow="never">
            <template #header>
              <div class="card-header">
                <span class="title">接近升级</span>
                <span class="subtitle">距上一档 0.2 分以内</span>
              </div>
            </template>
            <el-empty v-if="!d.near_upgrade.length" description="暂时没有卡在档位边上的票" :image-size="80" />
            <el-table v-else :data="d.near_upgrade" size="default" @row-click="row => openStock(row.code)">
              <el-table-column prop="name" label="名称" min-width="100" />
              <el-table-column label="池" width="80">
                <template #default="{ row }">
                  <el-tag :type="poolTagType(row.pool)" size="small" effect="plain">
                    {{ row.pool_label }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="差距" min-width="120">
                <template #default="{ row }">
                  差 <b>{{ row.gap.toFixed(2) }}</b> 分到 {{ row.target }}
                </template>
              </el-table-column>
              <el-table-column label="达标周数" width="100" align="right">
                <template #default="{ row }">
                  <span class="muted small">连续 {{ row.consecutive_weeks_above }} 周</span>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, POOL_LABELS } from '../api/client.js'

const router = useRouter()
const d = ref(null)
const loading = ref(true)
const error = ref('')

const poolOrder = ['candidate', 'observation', 'reserve', 'tuna', 'removed']

function labelOf(p) { return POOL_LABELS[p] || p || '' }
function poolNote(p) {
  if (p === 'tuna') return '不占名额'
  if (p === 'removed') return '不计名额'
  return ''
}
function poolTagType(pool) {
  const m = { candidate: 'danger', observation: 'warning', reserve: 'primary', tuna: '', removed: 'info' }
  return m[pool] || 'info'
}

function openStock(code) { router.push(`/stocks/${code}`) }
function goPools() { router.push('/pools') }
function goPoolsWith(pool) { router.push({ path: '/pools', query: { pool } }) }

function dirIcon(dir) {
  return { up: '↑', down: '↓', tuna: '🐟', in: '📥', out: '×', back: '↩', move: '→' }[dir] || '·'
}

onMounted(async () => {
  try {
    d.value = await api.dashboard()
  } catch (e) {
    error.value = `读取仪表盘失败：${e.message}`
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.dash { display: flex; flex-direction: column; }

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-header .title {
  font-weight: 600;
  font-size: 14px;
}
.card-header .subtitle {
  font-size: 12px;
  color: var(--text-tertiary);
}

.stale-alert { margin-bottom: 16px; }

.pool-card {
  background: #fafbfc;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  cursor: pointer;
  transition: all 0.2s;
}
.pool-card:hover {
  border-color: var(--primary);
  background: var(--primary-light);
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}
.pool-card .pool-label {
  font-size: 12px;
  color: var(--text-secondary);
}
.pool-card .pool-count {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
  margin: 4px 0 2px;
}
.pool-card .pool-note {
  font-size: 11px;
  color: var(--text-tertiary);
}
.pool-card.pool-candidate:hover { border-color: #b42318; background: #fef2f2; }
.pool-card.pool-observation:hover { border-color: #96620b; background: #fffbeb; }
.pool-card.pool-reserve:hover { border-color: #2b4d9e; background: #eff6ff; }
.pool-card.pool-tuna:hover { border-color: #5b3a8f; background: #faf5ff; }
.pool-card.pool-removed:hover { border-color: #667085; background: #f3f4f6; }

.footnote {
  margin: 12px 0 0;
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.stock-name { font-weight: 500; }
.muted { color: var(--text-tertiary); }
.small { font-size: 11.5px; }

.score-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 100px;
}
.score-cell :deep(.el-progress) { flex: 1; min-width: 60px; }
.score-num {
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  font-weight: 600;
  color: var(--primary);
  white-space: nowrap;
}

.dir-icon {
  display: inline-block;
  width: 22px;
  height: 22px;
  line-height: 22px;
  text-align: center;
  border-radius: 50%;
  font-size: 12px;
}
.dir-up { background: #fee2e2; color: #b42318; }
.dir-down { background: #d1fae5; color: #065f46; }
.dir-tuna { background: #ede9fe; color: #5b21b6; }
.dir-in { background: #dbeafe; color: #1e40af; }
.dir-out { background: #f3f4f6; color: #4b5563; }
.dir-back { background: #fef3c7; color: #92400e; }
.dir-move { background: #e0e7ff; color: #3730a3; }

.gap-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
}
.gap-title {
  font-size: 12px;
  color: var(--text-secondary);
}
.gap-desc {
  font-size: 12px;
  margin: 4px 0 8px;
  max-width: 400px;
}

:deep(.el-table__row) { cursor: pointer; }
:deep(.el-empty) { padding: 20px 0; }
</style>
