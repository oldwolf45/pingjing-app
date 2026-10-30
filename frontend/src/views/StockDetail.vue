<template>
  <div class="detail">
    <el-button link type="primary" @click="back" style="align-self: flex-start; padding: 0">
      ← 返回列表
    </el-button>

    <el-skeleton v-if="loading" :rows="6" animated />
    <el-alert v-else-if="error" :title="error" type="error" show-icon :closable="false" />

    <template v-else>
      <!-- 头部卡片 -->
      <el-card shadow="never" class="head-card">
        <div class="head-main">
          <div class="title-row">
            <h1>{{ s.name }}</h1>
            <span class="code num">{{ s.code }}</span>
            <el-tag :type="poolTagType(s.pool)" effect="plain">
              {{ s.pool_label }}
            </el-tag>
            <el-tag v-if="score.is_fallback" type="warning" effect="light" size="small">
              🟡 情报未录入
            </el-tag>
          </div>
          <div class="meta">
            <el-tag size="small" effect="plain" type="info">{{ s.sector || '未归类赛道' }}</el-tag>
            <span class="muted">跟踪 {{ s.weeks_in_pool }} 周</span>
            <span class="muted">连续达标 {{ s.consecutive_weeks_above }} 周</span>
            <span class="muted">评分日期 {{ score.as_of_date || '—' }}</span>
            <span v-if="s.total_mv_yi" class="muted">市值 {{ s.total_mv_yi.toFixed(0) }} 亿</span>
          </div>
        </div>
        <div class="head-score">
          <div class="total num">
            {{ score.has_score ? score.total.toFixed(2) : '—' }}
            <span class="denom">/ 9.0</span>
          </div>
          <div class="sub muted">六维总分</div>
        </div>
      </el-card>

      <el-alert
        v-if="!score.has_score"
        type="info"
        :closable="false"
        show-icon
        title="这只票还没有评分快照。评分数据只会由更新任务写入，不会在这里编一个分数。"
      />

      <template v-else>
        <!-- Tab 页签 -->
        <el-tabs v-model="activeTab" type="card" class="detail-tabs">
          <!-- 概览 Tab -->
          <el-tab-pane label="概览" name="overview">
            <el-row :gutter="16">
              <!-- 六维雷达 -->
              <el-col :lg="12" :span="24" style="margin-bottom: 16px">
                <el-card shadow="never">
                  <template #header>
                    <div class="card-header">
                      <span class="title">六维雷达</span>
                      <span class="subtitle">轴标签为「得分 / 满分」</span>
                    </div>
                  </template>
                  <Radar6 :dimensions="score.dimensions" :range="2.5" />
                  <el-alert
                    v-if="hasMissing"
                    type="warning"
                    :closable="false"
                    size="small"
                    style="margin-top: 10px"
                    title="有维度缺数据：图上是断开的，不是 0 分。"
                    show-icon
                  />
                </el-card>
              </el-col>

              <!-- 评分拆解 -->
              <el-col :lg="12" :span="24" style="margin-bottom: 16px">
                <el-card shadow="never">
                  <template #header>
                    <div class="card-header">
                      <span class="title">评分拆解</span>
                      <span class="subtitle">满分 9.0</span>
                    </div>
                  </template>
                  <div class="dim-list">
                    <div v-for="d in score.dimensions" :key="d.key" class="dim-item">
                      <div class="dim-top">
                        <span class="dim-label">{{ d.label }}</span>
                        <span v-if="d.missing" class="missing">缺数据</span>
                        <template v-else>
                          <span class="dim-val num">
                            {{ d.score.toFixed(2) }}
                            <span class="dim-full">/{{ d.full.toFixed(1) }}</span>
                          </span>
                        </template>
                      </div>
                      <el-progress
                        v-if="!d.missing"
                        :percentage="(d.score / d.full) * 100"
                        :stroke-width="8"
                        :show-text="false"
                        color="var(--primary)"
                      />
                    </div>
                  </div>
                </el-card>
              </el-col>
            </el-row>

            <el-row :gutter="16">
              <!-- 操作参数 -->
              <el-col :md="8" :span="24" style="margin-bottom: 16px">
                <el-card shadow="never">
                  <template #header>
                    <span class="title">操作参数</span>
                  </template>
                  <el-descriptions :column="1" border size="default">
                    <el-descriptions-item label="建议买点">
                      <template v-if="s.price_ready">{{ s.ideal_buy_text || '—' }}</template>
                      <el-tag v-else type="warning" effect="light" size="small">
                        待确认（进池当周不给价）
                      </el-tag>
                    </el-descriptions-item>
                    <el-descriptions-item label="止损位">
                      {{ s.stop_loss_text || '—' }}
                    </el-descriptions-item>
                    <el-descriptions-item label="目标价">
                      {{ s.target_text || '—' }}
                    </el-descriptions-item>
                    <el-descriptions-item label="建议仓位">
                      <template v-if="s.position_display !== null && s.position_display !== undefined">
                        {{ s.position_display }}%
                        <el-tag
                          v-if="s.position_capped"
                          type="warning"
                          effect="light"
                          size="small"
                          style="margin-left: 6px"
                        >
                          {{ s.position_note }}
                        </el-tag>
                      </template>
                      <span v-else class="muted">—</span>
                    </el-descriptions-item>
                  </el-descriptions>
                </el-card>
              </el-col>

              <!-- PLR 盈亏比 -->
              <el-col :md="8" :span="24" style="margin-bottom: 16px">
                <el-card shadow="never">
                  <template #header>
                    <div class="card-header">
                      <span class="title">盈亏比 PLR</span>
                      <el-tag size="small" effect="plain" type="info">技术信号参考</el-tag>
                      <el-tag
                        v-if="plr && plr.ref_score != null"
                        size="small"
                        effect="plain"
                        type="primary"
                        :title="'技术信号参考分 ' + plr.ref_score + ' / 1 分（不计入总分）'"
                      >
                        {{ plr.ref_score.toFixed(1) }}/1
                      </el-tag>
                    </div>
                  </template>
                  <div v-if="plrLoading" class="muted">计算中…</div>
                  <template v-else-if="plr">
                    <!-- 贴脸剔除 -->
                    <template v-if="plr.flag === '贴脸剔除'">
                      <div class="plr-main">
                        <span class="plr-value num">—</span>
                        <el-tag type="success" effect="light" size="small">贴脸剔除</el-tag>
                      </div>
                      <p class="muted small">
                        支撑 s1 距现价仅 {{ plr.s1_dist }}%（&lt;1.5%），这是<b>破位风险线</b>，不是高赔率，PLR 不给数（防虚高）。
                      </p>
                    </template>

                    <!-- 正常 / 贴线降仓 -->
                    <template v-else-if="plr.plr != null">
                      <div class="plr-main">
                        <span class="plr-value num">{{ plr.plr }}</span>
                        <el-tag
                          :type="plrFlagType(plr)"
                          size="small"
                          effect="plain"
                        >
                          {{ plr.flag === '贴线降仓' ? '贴线降仓' : (plr.plr > 3 ? '达标 >3' : (plr.plr >= 2 ? '临界 2-3' : '不达标 <2')) }}
                        </el-tag>
                      </div>
                      <el-descriptions :column="1" size="small" class="plr-kv">
                        <el-descriptions-item label="当前价" class-name="num">{{ plr.price != null ? plr.price.toFixed(2) : '—' }}</el-descriptions-item>
                        <el-descriptions-item label="支撑 s1" class-name="num">{{ plr.s1 != null ? plr.s1.toFixed(2) : '—' }}</el-descriptions-item>
                        <el-descriptions-item label="s1 距离" class-name="num">{{ plr.s1_dist }}%</el-descriptions-item>
                        <el-descriptions-item label="阻力 w2" class-name="num">{{ plr.w2 != null ? plr.w2.toFixed(2) : '—' }}</el-descriptions-item>
                        <el-descriptions-item label="日K样本" class-name="num">{{ plr.bars }} 根</el-descriptions-item>
                      </el-descriptions>
                      <el-alert
                        v-if="plr.flag === '贴线降仓'"
                        type="warning"
                        :closable="false"
                        size="small"
                        :title="`支撑距现价 ${plr.s1_dist}%（1.5%~2%），信号偏险，按 hermes 复核链应降仓。`"
                        show-icon
                      />
                    </template>

                    <p v-else class="muted small">
                      {{ plr.error || '技术信号算不出（日K不足 / 价格贴近支撑阻力），宁缺毋假。' }}
                    </p>
                    <p class="muted small plr-note">
                      PLR=(阻力w2−现价)/(现价−支撑s1)，近250日成交量加权分布峰值，s1 取量能最强支撑；支撑贴脸(&lt;1.5%)判为破位风险线不给数。与左侧基本面买点/止损是两套体系，仅作参考。
                    </p>
                  </template>
                </el-card>
              </el-col>

              <!-- 核心逻辑 -->
              <el-col :md="8" :span="24" style="margin-bottom: 16px">
                <el-card shadow="never">
                  <template #header>
                    <span class="title">核心逻辑与预警</span>
                  </template>
                  <div class="blk">
                    <div class="blk-label">核心逻辑</div>
                    <p class="blk-text">{{ s.core_logic || '未填写' }}</p>
                  </div>
                  <el-divider style="margin: 10px 0" />
                  <div class="blk">
                    <div class="blk-label warn">降级预警</div>
                    <p class="blk-text">{{ s.downgrade_warning || '未填写' }}</p>
                  </div>
                  <template v-if="s.pool_state && s.pool_state.tuna_hits && s.pool_state.tuna_hits.length">
                    <el-divider style="margin: 10px 0" />
                    <div class="blk">
                      <div class="blk-label">金枪鱼命中记录</div>
                      <p v-for="(h, i) in s.pool_state.tuna_hits" :key="i" class="blk-text small">{{ h }}</p>
                    </div>
                  </template>
                  <template v-if="s.pool_state && s.pool_state.remove_reason">
                    <el-divider style="margin: 10px 0" />
                    <div class="blk">
                      <div class="blk-label warn">移除原因</div>
                      <p class="blk-text">{{ s.pool_state.remove_reason }}</p>
                    </div>
                  </template>
                </el-card>
              </el-col>
            </el-row>
          </el-tab-pane>

          <!-- 壁垒 Tab -->
          <el-tab-pane label="壁垒明细" name="barrier">
            <el-row :gutter="16">
              <el-col :lg="12" :span="24" style="margin-bottom: 16px">
                <el-card shadow="never">
                  <template #header>
                    <div class="card-header">
                      <span class="title">壁垒明细（8 维）</span>
                      <span class="subtitle" v-if="score.barrier">
                        来源 {{ score.barrier.source || '—' }} ·
                        置信度 {{ score.barrier.overall_confidence || '—' }}%
                      </span>
                    </div>
                  </template>
                  <el-empty v-if="!barrierDims.length" description="没有壁垒明细数据" :image-size="80" />
                  <div v-else class="barrier-list">
                    <div v-for="b in barrierDims" :key="b.key" class="bar-item">
                      <div class="bar-top">
                        <span class="bar-name">{{ b.name }}</span>
                        <span v-if="b.missing" class="missing">缺数据</span>
                        <span v-else class="bar-score num small">
                          {{ fmt(b.score) }}/{{ b.max_score }}
                        </span>
                      </div>
                      <el-progress
                        v-if="!b.missing"
                        :percentage="((b.score || 0) / b.max_score) * 100"
                        :stroke-width="6"
                        :show-text="false"
                        color="#7c5cae"
                      />
                    </div>
                  </div>
                  <el-alert
                    v-if="score.barrier && score.barrier.source === 'fallback_manual'"
                    type="warning"
                    :closable="false"
                    size="small"
                    show-icon
                    style="margin-top: 12px"
                    title="这个赛道的壁垒情报还没录入，当前用的是兜底分 1.2。补上情报后分数会变。"
                  />
                </el-card>
              </el-col>

              <el-col :lg="12" :span="24" style="margin-bottom: 16px">
                <el-card shadow="never">
                  <template #header>
                    <div class="card-header">
                      <span class="title">财务快照</span>
                      <span class="subtitle">{{ score.as_of_date }} 评分时点</span>
                    </div>
                  </template>
                  <el-descriptions :column="2" border size="default">
                    <template v-for="f in finRows" :key="f.label">
                      <el-descriptions-item :label="f.label" :class-name="f.cls">
                        {{ f.value }}
                      </el-descriptions-item>
                    </template>
                  </el-descriptions>
                </el-card>
              </el-col>
            </el-row>
          </el-tab-pane>

          <!-- 历史 Tab -->
          <el-tab-pane label="评分历史" name="history">
            <el-card shadow="never">
              <template #header>
                <div class="card-header">
                  <span class="title">评分历史</span>
                  <span class="subtitle">
                    共 {{ score.history.length }} 条 · 覆盖 {{ score.trend.points }} 个日期
                  </span>
                </div>
              </template>

              <el-alert
                v-if="!score.trend.available"
                type="info"
                :closable="false"
                show-icon
                class="trend-alert"
              >
                <template #title>
                  还不能画趋势 · 当前 {{ score.trend.points }}/{{ score.trend.needed }}
                  <div class="small muted" style="margin-top: 4px">
                    少于 {{ score.trend.needed }} 个日期连起来会像噪音，不如不画。
                  </div>
                </template>
              </el-alert>

              <el-table :data="sortedHistory" stripe size="default">
                <el-table-column prop="as_of_date" label="日期" width="105">
                  <template #default="{ row }">
                    <span class="num">{{ row.as_of_date }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="总分" width="90" fixed>
                  <template #default="{ row }">
                    <span class="num strong">{{ fmt(row.total) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="壁垒" width="80">
                  <template #default="{ row }">
                    <span class="num">{{ fmt(row.track_barrier) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="增长" width="80">
                  <template #default="{ row }">
                    <span class="num">{{ fmt(row.earnings_growth) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="盈利" width="80">
                  <template #default="{ row }">
                    <span class="num">{{ fmt(row.profitability) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="景气" width="80">
                  <template #default="{ row }">
                    <span class="num">{{ fmt(row.prosperity_check) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="热度" width="80">
                  <template #default="{ row }">
                    <span class="num">{{ fmt(row.heat_position) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="市值" width="80">
                  <template #default="{ row }">
                    <span class="num">{{ fmt(row.market_cap_elasticity) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="来源" width="100">
                  <template #default="{ row }">
                    <span class="muted small">{{ sourceLabel(row.source) }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </el-tab-pane>

          <!-- 说明 Tab -->
          <el-tab-pane :label="`评分说明 (${score.notes.length})`" name="notes">
            <el-card shadow="never">
              <ul class="notes">
                <li v-for="(n, i) in score.notes" :key="i">{{ n }}</li>
              </ul>
            </el-card>
          </el-tab-pane>
        </el-tabs>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client.js'
import Radar6 from '../components/Radar6.vue'

const route = useRoute()
const router = useRouter()

const activeTab = ref('overview')
const s = ref(null)
const score = ref({ has_score: false, dimensions: [], history: [], trend: {}, notes: [] })
const plr = ref(null)
const plrLoading = ref(false)
const loading = ref(true)
const error = ref('')

const hasMissing = computed(() => score.value.dimensions.some((d) => d.missing))
const sortedHistory = computed(() => (score.value.history || []).slice().reverse())

const barrierDims = computed(() => {
  const b = score.value.barrier
  if (!b || !Array.isArray(b.dimensions)) return []
  return b.dimensions
})

const finRows = computed(() => {
  const f = score.value.financial
  if (!f) return [{ label: '财务数据', value: '缺数据', cls: 'muted' }]
  const pct = (v) => (v === null || v === undefined ? '缺数据' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
  const cls = (v) => (v === null || v === undefined ? 'muted' : v > 0 ? 'up' : v < 0 ? 'down' : '')
  return [
    { label: '营收同比', value: pct(f.revenue_yoy), cls: cls(f.revenue_yoy) },
    { label: '净利同比', value: pct(f.net_profit_yoy), cls: cls(f.net_profit_yoy) },
    { label: '扣非同比', value: pct(f.deduct_profit_yoy), cls: cls(f.deduct_profit_yoy) },
    { label: '毛利率', value: f.gross_margin === null || f.gross_margin === undefined ? '缺数据' : `${f.gross_margin.toFixed(1)}%`, cls: '' },
    { label: 'ROE', value: f.roe === null || f.roe === undefined ? '缺数据' : `${f.roe.toFixed(2)}%`, cls: '' },
    { label: 'OCF/净利', value: fmt(f.ocf_to_ni), cls: '' },
    { label: '总市值', value: f.total_mv ? `${f.total_mv.toFixed(0)} 亿` : '缺数据', cls: '' },
    { label: '毛利率趋势', value: { up: '上升', flat: '持平', down: '下降' }[f.gross_margin_trend] || '缺数据', cls: '' },
  ]
})

function fmt(v) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(2)
}

function poolTagType(p) {
  const m = { candidate: 'danger', observation: 'warning', reserve: 'primary', tuna: '', removed: 'info' }
  return m[p] || 'info'
}

function plrFlagType(plr) {
  if (plr.flag === '贴线降仓') return 'warning'
  if (plr.plr > 3) return 'success'
  if (plr.plr >= 2) return 'warning'
  return 'info'
}

function sourceLabel(src) {
  return {
    live: '实时计算',
    history: '历史快照',
    current: '当前状态',
    migration: '迁移导入',
    migration_cache: '迁移补分',
  }[src] || src || ''
}

function back() { router.push('/pools') }

async function load(code) {
  loading.value = true
  error.value = ''
  try {
    const r = await api.stock(code)
    s.value = r.stock
    score.value = r.score
  } catch (e) {
    error.value = `读取失败：${e.message}`
    s.value = null
  } finally {
    loading.value = false
  }
  loadPlr(code)
}

async function loadPlr(code) {
  plrLoading.value = true
  plr.value = null
  try {
    plr.value = await api.stockPlr(code)
  } catch (e) {
    plr.value = { plr: null, error: e.message, data_ok: false }
  } finally {
    plrLoading.value = false
  }
}

watch(() => route.params.code, (c) => c && load(c), { immediate: true })
</script>

<style scoped>
.detail { display: flex; flex-direction: column; gap: 14px; }

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.card-header .title { font-weight: 600; font-size: 14px; }
.card-header .subtitle { font-size: 12px; color: var(--text-tertiary); margin-left: auto; }

/* 头部 */
.head-card :deep(.el-card__body) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px;
}
.title-row { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.title-row h1 { font-size: 22px; margin: 0; font-weight: 700; }
.title-row .code { color: var(--text-tertiary); font-size: 13px; }
.meta {
  display: flex;
  gap: 10px 14px;
  margin-top: 8px;
  flex-wrap: wrap;
  align-items: center;
  font-size: 12px;
}
.head-score { text-align: right; padding-left: 20px; flex-shrink: 0; }
.total { font-size: 32px; font-weight: 700; line-height: 1.1; color: var(--primary); }
.total .denom { font-size: 14px; color: var(--text-tertiary); font-weight: 400; }
.head-score .sub { font-size: 12px; margin-top: 2px; }

.muted { color: var(--text-tertiary); }
.small { font-size: 11.5px; }
.num { font-variant-numeric: tabular-nums; }
.strong { font-weight: 600; color: var(--primary); }
.up { color: var(--danger); }
.down { color: var(--success); }

.detail-tabs { margin-top: 4px; }
.detail-tabs :deep(.el-tabs__item) {
  height: 38px;
  line-height: 36px;
  font-size: 13px;
}

/* 评分拆解 */
.dim-list { display: flex; flex-direction: column; gap: 10px; }
.dim-item { }
.dim-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
  font-size: 12.5px;
}
.dim-label { color: var(--text-secondary); }
.dim-val { font-weight: 600; color: var(--primary); }
.dim-full { color: var(--text-tertiary); font-weight: 400; font-size: 11px; }

.missing {
  display: inline-block;
  padding: 0 6px;
  border-radius: 3px;
  font-size: 11px;
  color: #8b95a5;
  background-image: repeating-linear-gradient(
    45deg, #e9ecf0, #e9ecf0 3px, #f6f7f9 3px, #f6f7f9 6px
  );
}

/* PLR */
.plr-main { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.plr-value { font-size: 28px; font-weight: 700; color: var(--primary); }
.plr-kv { margin-top: 4px; }
.plr-kv :deep(.el-descriptions__label) { width: 70px; }
.plr-note { margin: 10px 0 0; line-height: 1.6; }

/* 核心逻辑 */
.blk { }
.blk-label { font-size: 11.5px; color: var(--text-tertiary); margin-bottom: 4px; }
.blk-label.warn { color: var(--warning); }
.blk-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-primary);
}

/* 壁垒 */
.barrier-list { display: flex; flex-direction: column; gap: 10px; }
.bar-item { }
.bar-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
  font-size: 12.5px;
}
.bar-name { color: var(--text-secondary); }
.bar-score { color: var(--text-primary); font-weight: 500; }

/* 评分说明 */
.notes {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 2;
  color: var(--text-secondary);
}
.notes li { margin-bottom: 2px; }

.trend-alert { margin-bottom: 12px; }

:deep(.el-descriptions__label) {
  width: 80px;
  color: var(--text-tertiary) !important;
  font-weight: normal !important;
}
</style>
