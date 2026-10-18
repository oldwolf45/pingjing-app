<template>
  <div class="detail">
    <button class="back" @click="back">← 返回列表</button>

    <div v-if="loading" class="loading">正在读取…</div>
    <div v-else-if="error" class="err">{{ error }}</div>

    <template v-else>
      <!-- 头部 -->
      <section class="card head-card">
        <div class="head-main">
          <div class="title-row">
            <h1>{{ s.name }}</h1>
            <span class="code num">{{ s.code }}</span>
            <span class="pill" :class="poolClass(s.pool)">{{ s.pool_label }}</span>
            <span v-if="score.is_fallback" class="badge-fallback">🟡 情报未录入</span>
          </div>
          <div class="meta small muted">
            <span>{{ s.sector || '未归类赛道' }}</span>
            <span>·</span>
            <span>跟踪 {{ s.weeks_in_pool }} 周</span>
            <span>·</span>
            <span>连续达标 {{ s.consecutive_weeks_above }} 周</span>
            <span>·</span>
            <span>评分日期 {{ score.as_of_date || '—' }}</span>
            <span v-if="s.total_mv_yi">·</span>
            <span v-if="s.total_mv_yi">市值 {{ s.total_mv_yi.toFixed(0) }} 亿</span>
          </div>
        </div>
        <div class="head-score">
          <div class="total num">
            {{ score.has_score ? score.total.toFixed(2) : '—' }}
            <span class="denom">/ 9.0</span>
          </div>
          <div class="small muted">六维总分</div>
        </div>
      </section>

      <div v-if="!score.has_score" class="card">
        <div class="empty">
          这只票还没有评分快照。评分数据只会由更新任务写入，不会在这里编一个分数。
        </div>
      </div>

      <template v-else>
        <div class="cols">
          <!-- 雷达 -->
          <section class="card">
            <div class="card-head">
              <h2>六维雷达</h2>
              <span class="hint">轴标签为「得分 / 满分」</span>
            </div>
            <div class="card-body">
              <Radar6 :dimensions="score.dimensions" :range="2.5" />
              <p v-if="hasMissing" class="small muted miss-note">
                有维度缺数据：图上是断开的，不是 0 分。
              </p>
            </div>
          </section>

          <!-- 拆解条 -->
          <section class="card">
            <div class="card-head">
              <h2>评分拆解</h2>
              <span class="hint">满分 9.0</span>
            </div>
            <div class="card-body">
              <div v-for="d in score.dimensions" :key="d.key" class="dim">
                <span class="dim-label">{{ d.label }}</span>
                <span v-if="d.missing" class="missing">缺数据</span>
                <template v-else>
                  <span class="dim-bar">
                    <i :style="{ width: (d.score / d.full) * 100 + '%' }"></i>
                  </span>
                  <span class="dim-val num">{{ d.score.toFixed(2) }}</span>
                  <span class="dim-full num muted">/{{ d.full.toFixed(1) }}</span>
                </template>
              </div>
            </div>
          </section>
        </div>

        <div class="cols">
          <!-- 操作参数 -->
          <section class="card">
            <div class="card-head"><h2>操作参数</h2></div>
            <div class="card-body">
              <dl class="kv">
                <dt>建议买点</dt>
                <dd>
                  <template v-if="s.price_ready">{{ s.ideal_buy_text || '—' }}</template>
                  <span v-else class="badge-fallback">待确认（进池当周不给价）</span>
                </dd>
                <dt>止损位</dt>
                <dd>{{ s.stop_loss_text || '—' }}</dd>
                <dt>目标价</dt>
                <dd>{{ s.target_text || '—' }}</dd>
                <dt>建议仓位</dt>
                <dd>
                  <template v-if="s.position_display !== null && s.position_display !== undefined">
                    {{ s.position_display }}%
                    <span v-if="s.position_capped" class="cap-note small">
                      （{{ s.position_note }}）
                    </span>
                  </template>
                  <span v-else class="muted">—</span>
                </dd>
              </dl>
            </div>
          </section>

          <!-- PLR 盈亏比（V5.0 技术信号参考，hermes 口径，独立于基本面点位） -->
          <section class="card">
            <div class="card-head"><h2>盈亏比 PLR <span class="muted small">技术信号参考</span></h2></div>
            <div class="card-body">
              <p v-if="plrLoading" class="muted small">计算中…</p>
              <template v-else-if="plr">
                <!-- 贴脸剔除：支撑贴脸=破位风险线，不是高赔率，不给数值 -->
                <template v-if="plr.flag === '贴脸剔除'">
                  <div class="plr-main">
                    <span class="plr-value num">—</span>
                    <span class="plr-flag down small">贴脸剔除</span>
                  </div>
                  <p class="muted small">
                    支撑 s1 距现价仅 {{ plr.s1_dist }}%（&lt;1.5%），这是<b>破位风险线</b>，不是高赔率，PLR 不给数（防虚高）。
                  </p>
                </template>

                <!-- 正常 / 贴线降仓 -->
                <template v-else-if="plr.plr != null">
                  <div class="plr-main">
                    <span class="plr-value num">{{ plr.plr }}</span>
                    <span class="plr-flag small"
                          :class="plr.flag === '贴线降仓' ? 'warn' : (plr.plr > 3 ? 'up' : (plr.plr >= 2 ? 'warn' : 'down'))">
                      {{ plr.flag === '贴线降仓' ? '贴线降仓' : (plr.plr > 3 ? '达标 >3' : (plr.plr >= 2 ? '临界 2-3' : '不达标 <2')) }}
                    </span>
                  </div>
                  <dl class="kv small">
                    <dt>当前价</dt><dd class="num">{{ plr.price != null ? plr.price.toFixed(2) : '—' }}</dd>
                    <dt>支撑 s1</dt><dd class="num">{{ plr.s1 != null ? plr.s1.toFixed(2) : '—' }}</dd>
                    <dt>s1 距离</dt><dd class="num">{{ plr.s1_dist }}%</dd>
                    <dt>阻力 w2</dt><dd class="num">{{ plr.w2 != null ? plr.w2.toFixed(2) : '—' }}</dd>
                    <dt>日K样本</dt><dd class="num">{{ plr.bars }} 根</dd>
                  </dl>
                  <p v-if="plr.flag === '贴线降仓'" class="muted small">支撑距现价 {{ plr.s1_dist }}%（1.5%~2%），信号偏险，按 hermes 复核链应降仓。</p>
                </template>

                <!-- 数据不足 -->
                <p v-else class="muted small">
                  {{ plr.error || '技术信号算不出（日K不足 / 价格贴近支撑阻力），宁缺毋假。' }}
                </p>
                <p class="muted small plr-note">PLR=(阻力w2−现价)/(现价−支撑s1)，近250日成交量加权分布峰值，s1 取量能最强支撑；支撑贴脸(&lt;1.5%)判为破位风险线不给数。与左侧基本面买点/止损是两套体系，仅作参考。</p>
              </template>
            </div>
          </section>

          <!-- 核心逻辑与预警 -->
          <section class="card">
            <div class="card-head"><h2>核心逻辑与预警</h2></div>
            <div class="card-body">
              <div class="blk">
                <div class="blk-label">核心逻辑</div>
                <p class="blk-text">{{ s.core_logic || '未填写' }}</p>
              </div>
              <div class="blk">
                <div class="blk-label warn">降级预警</div>
                <p class="blk-text">{{ s.downgrade_warning || '未填写' }}</p>
              </div>
              <div v-if="s.pool_state && s.pool_state.tuna_hits && s.pool_state.tuna_hits.length" class="blk">
                <div class="blk-label">金枪鱼命中记录</div>
                <p v-for="(h, i) in s.pool_state.tuna_hits" :key="i" class="blk-text small">{{ h }}</p>
              </div>
              <div v-if="s.pool_state && s.pool_state.remove_reason" class="blk">
                <div class="blk-label warn">移除原因</div>
                <p class="blk-text">{{ s.pool_state.remove_reason }}</p>
              </div>
            </div>
          </section>
        </div>

        <div class="cols">
          <!-- 壁垒 8 维 -->
          <section class="card">
            <div class="card-head">
              <h2>壁垒明细（8 维）</h2>
              <span v-if="score.barrier" class="hint">
                来源 {{ score.barrier.source || '—' }} ·
                置信度 {{ score.barrier.overall_confidence || '—' }}%
              </span>
            </div>
            <div class="card-body">
              <div v-if="!barrierDims.length" class="empty">没有壁垒明细数据</div>
              <div v-for="b in barrierDims" :key="b.key" class="bar-dim">
                <span class="bar-name">{{ b.name }}</span>
                <span class="bar-meter">
                  <i :style="{ width: ((b.score || 0) / b.max_score) * 100 + '%' }"></i>
                </span>
                <span class="num small">{{ fmt(b.score) }}/{{ b.max_score }}</span>
                <span v-if="b.missing" class="missing">缺数据</span>
              </div>
              <p v-if="score.barrier && score.barrier.source === 'fallback_manual'" class="small warn-note">
                这个赛道的壁垒情报还没录入，当前用的是兜底分 1.2。补上情报后分数会变。
              </p>
            </div>
          </section>

          <!-- 财务快照 -->
          <section class="card">
            <div class="card-head">
              <h2>财务快照</h2>
              <span class="hint">{{ score.as_of_date }} 评分时点</span>
            </div>
            <div class="card-body">
              <dl class="kv two">
                <template v-for="f in finRows" :key="f.label">
                  <dt>{{ f.label }}</dt>
                  <dd :class="f.cls">{{ f.value }}</dd>
                </template>
              </dl>
            </div>
          </section>
        </div>

        <!-- 评分说明 -->
        <section class="card">
          <div class="card-head">
            <h2>评分说明</h2>
            <span class="hint">{{ score.notes.length }} 条</span>
          </div>
          <div class="card-body">
            <ul class="notes">
              <li v-for="(n, i) in score.notes" :key="i">{{ n }}</li>
            </ul>
          </div>
        </section>

        <!-- 评分历史 -->
        <section class="card">
          <div class="card-head">
            <h2>评分历史</h2>
            <span class="hint">共 {{ score.history.length }} 条 · 覆盖 {{ score.trend.points }} 个日期</span>
          </div>
          <div class="card-body">
            <div v-if="!score.trend.available" class="trend-blocked">
              还不能画趋势 · 当前 {{ score.trend.points }}/{{ score.trend.needed }}
              <div class="small muted" style="margin-top: 4px">
                少于 {{ score.trend.needed }} 个日期连起来会像噪音，不如不画。
              </div>
            </div>
            <table class="tbl">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>总分</th>
                  <th>壁垒</th>
                  <th>增长</th>
                  <th>盈利</th>
                  <th>景气</th>
                  <th>热度</th>
                  <th>市值</th>
                  <th>来源</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(h, i) in score.history.slice().reverse()" :key="i">
                  <td class="num">{{ h.as_of_date }}</td>
                  <td class="num strong">{{ fmt(h.total) }}</td>
                  <td class="num">{{ fmt(h.track_barrier) }}</td>
                  <td class="num">{{ fmt(h.earnings_growth) }}</td>
                  <td class="num">{{ fmt(h.profitability) }}</td>
                  <td class="num">{{ fmt(h.prosperity_check) }}</td>
                  <td class="num">{{ fmt(h.heat_position) }}</td>
                  <td class="num">{{ fmt(h.market_cap_elasticity) }}</td>
                  <td class="small muted">{{ sourceLabel(h.source) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, poolClass } from '../api/client.js'
import Radar6 from '../components/Radar6.vue'

const route = useRoute()
const router = useRouter()

const s = ref(null)
const score = ref({ has_score: false, dimensions: [], history: [], trend: {}, notes: [] })
const plr = ref(null)
const plrLoading = ref(false)
const loading = ref(true)
const error = ref('')

const hasMissing = computed(() => score.value.dimensions.some((d) => d.missing))

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

function sourceLabel(src) {
  return {
    live: '实时计算',
    history: '历史快照',
    current: '当前状态',
    migration: '迁移导入',
    migration_cache: '迁移补分',
  }[src] || src || ''
}

function back() {
  router.push('/pools')
}

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

// PLR 是技术信号参考，独立于评分体系；单独拉取，失败不影响主信息
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

.plr-main { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.plr-value { font-size: 26px; font-weight: 600; }
.plr-flag { padding: 2px 8px; border-radius: 4px; }
.plr-flag.up { color: var(--up, #c0392b); background: rgba(192, 57, 43, .08); }
.plr-flag.warn { color: #b8860b; background: rgba(184, 134, 11, .1); }
.plr-flag.down { color: var(--down, #27ae60); background: rgba(39, 174, 96, .08); }
.plr-note { margin-top: 10px; line-height: 1.5; }
.back {
  align-self: flex-start;
  border: none;
  background: none;
  color: var(--purple);
  cursor: pointer;
  padding: 0;
  font-size: 12.5px;
}
.back:hover { text-decoration: underline; }

.head-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 18px;
}
.title-row { display: flex; align-items: baseline; gap: 9px; flex-wrap: wrap; }
.title-row h1 { font-size: 20px; margin: 0; font-weight: 600; }
.title-row .code { color: var(--fg-3); font-size: 13px; }
.meta { display: flex; gap: 6px; margin-top: 5px; flex-wrap: wrap; }
.head-score { text-align: right; padding-left: 20px; }
.total { font-size: 28px; font-weight: 600; line-height: 1.1; color: var(--purple); }
.total .denom { font-size: 13px; color: var(--fg-3); font-weight: 400; }

.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

.dim { display: flex; align-items: center; gap: 9px; padding: 6px 0; }
.dim-label { width: 68px; font-size: 12.5px; color: var(--fg-2); }
.dim-bar {
  flex: 1;
  height: 6px;
  background: #eceef2;
  border-radius: 3px;
  overflow: hidden;
}
.dim-bar i { display: block; height: 100%; background: var(--purple); }
.dim-val { width: 34px; text-align: right; font-size: 12.5px; }
.dim-full { font-size: 11px; width: 26px; }

.kv { display: grid; grid-template-columns: 78px 1fr; gap: 8px 10px; margin: 0; }
.kv.two { grid-template-columns: 78px 1fr 78px 1fr; }
.kv dt { color: var(--fg-3); font-size: 12px; }
.kv dd { margin: 0; font-size: 13px; }
.cap-note { color: var(--amber); }

.blk { margin-bottom: 10px; }
.blk:last-child { margin-bottom: 0; }
.blk-label { font-size: 11.5px; color: var(--fg-3); margin-bottom: 2px; }
.blk-label.warn { color: var(--amber); }
.blk-text { margin: 0; font-size: 12.5px; line-height: 1.65; }

.bar-dim { display: flex; align-items: center; gap: 8px; padding: 4px 0; }
.bar-name { width: 82px; font-size: 12px; color: var(--fg-2); }
.bar-meter { flex: 1; height: 5px; background: #eceef2; border-radius: 3px; overflow: hidden; }
.bar-meter i { display: block; height: 100%; background: #7c5cae; }
.miss-note, .warn-note { margin: 8px 0 0; }
.warn-note { color: var(--amber); }

.notes { margin: 0; padding-left: 18px; font-size: 12.5px; line-height: 1.9; }
.strong { font-weight: 600; }

.trend-blocked {
  padding: 10px 12px;
  border-radius: 6px;
  background: #f7f8fa;
  color: var(--fg-2);
  font-size: 12.5px;
  margin-bottom: 10px;
}

@media (max-width: 1100px) {
  .cols { grid-template-columns: 1fr; }
}
</style>
