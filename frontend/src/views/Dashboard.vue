<template>
  <div class="dash">
    <div v-if="loading" class="loading">正在读取数据…</div>
    <div v-else-if="error" class="err">{{ error }}</div>

    <template v-else>
      <!-- 1 新鲜度提示条：只在真的过期时出现 -->
      <div v-if="d.freshness.stale" class="stale-bar" @click="goPools">
        <span class="warn-ico">⚠</span>
        数据已 {{ d.freshness.days_since }} 天没更新，最近一次评分
        {{ d.freshness.latest_score_date }}
        <span class="link">去更新</span>
      </div>

      <!-- 2 池子总览 -->
      <section class="card">
        <div class="card-head">
          <h2>池子总览</h2>
          <span class="hint">
            名额 {{ d.pools.capacity }} · 在池 {{ d.pools.in_pool }} ·
            {{ d.pools.gap > 0 ? `缺 ${d.pools.gap}` : '已满' }}
          </span>
        </div>
        <div class="card-body">
          <div class="pool-row">
            <PoolCard
              v-for="p in poolOrder"
              :key="p"
              :pool="p"
              :label="d.pools.labels[p]"
              :count="d.pools.counts[p]"
              :note="p === 'tuna' ? '不占名额' : p === 'removed' ? '不计名额' : ''"
              @pick="goPoolsWith"
            />
          </div>
          <p class="small muted footnote">
            在池 = 候选 + 观察 + 储备，金枪鱼池只跟踪不再新增投入，不占 28 个名额。
          </p>
        </div>
      </section>

      <!-- 3 待确认出池 -->
      <section v-if="d.pending_tuna.length" class="card">
        <div class="card-head">
          <h2>待确认出池</h2>
          <span class="hint">已命中出池线，下周再命中就移入金枪鱼池</span>
        </div>
        <div class="card-body tight">
          <button v-for="t in d.pending_tuna" :key="t.code" class="row-btn" @click="openStock(t.code)">
            <span class="nm">{{ t.name }}</span>
            <span class="code muted small">{{ t.code }}</span>
            <span class="pill" :class="poolClass(t.pool)">{{ t.pool_label }}</span>
            <span class="reason small">{{ t.tuna_reason || '命中出池线' }}</span>
            <span class="small muted">{{ t.tuna_weeks }}/2 周</span>
          </button>
        </div>
      </section>

      <div class="two-col">
        <!-- 4 评分排行 -->
        <section class="card">
          <div class="card-head">
            <h2>评分排行</h2>
            <span class="hint">前 7 名 · 满分 9.0</span>
          </div>
          <div class="card-body tight">
            <div v-if="!d.top_scores.length" class="empty">还没有评分数据，点右上角更新</div>
            <button v-for="(s, i) in d.top_scores" :key="s.code" class="row-btn" @click="openStock(s.code)">
              <span class="rank num">{{ i + 1 }}</span>
              <span class="nm">{{ s.name }}</span>
              <span class="code muted small">{{ s.code }}</span>
              <span class="pill" :class="poolClass(s.pool)">{{ labelOf(s.pool) }}</span>
              <span v-if="s.is_fallback" class="badge-fallback">兜底分</span>
              <span class="grow"></span>
              <ScoreBar :value="s.total" :full="9" />
            </button>
          </div>
        </section>

        <!-- 5 最近池子变动 -->
        <section class="card">
          <div class="card-head">
            <h2>最近池子变动</h2>
            <span class="hint">最近 10 条</span>
          </div>
          <div class="card-body tight">
            <div v-if="!d.recent_events.length" class="empty">本周暂无变动</div>
            <button v-for="e in d.recent_events" :key="e.id" class="row-btn" @click="openStock(e.code)">
              <span class="dotdir" :class="e.direction">{{ dirIcon(e.direction) }}</span>
              <span class="nm">{{ e.name || e.code }}</span>
              <span class="code muted small">{{ e.code }}</span>
              <span class="small muted">{{ e.from_label }} → {{ e.to_label }}</span>
              <span class="grow"></span>
              <span class="small muted">{{ e.change_date }}</span>
            </button>
          </div>
        </section>
      </div>

      <div class="two-col">
        <!-- 6 名额缺口 -->
        <section class="card">
          <div class="card-head"><h2>名额缺口</h2></div>
          <div class="card-body">
            <div v-if="d.pools.gap === 0" class="gap-ok">
              <span class="big">名额已满</span>
              <span class="small muted">在池 {{ d.pools.in_pool }} / {{ d.pools.capacity }}，补票会等有票出池后再补</span>
            </div>
            <div v-else class="gap-miss">
              <span class="big">缺口 {{ d.pools.gap }} 个</span>
              <span class="small muted">补票必须过四道关：情报已录入 · 财务拉全 · 分数 ≥4.5 · 市值 ≤250 亿。补不上就空着，不降标准。</span>
            </div>
            <button class="btn sm" style="margin-top: 10px" @click="goPools">去股票池看看</button>
          </div>
        </section>

        <!-- 7 接近升级 -->
        <section class="card">
          <div class="card-head">
            <h2>接近升级</h2>
            <span class="hint">距上一档 0.2 分以内</span>
          </div>
          <div class="card-body tight">
            <div v-if="!d.near_upgrade.length" class="empty">暂时没有卡在档位边上的票</div>
            <button v-for="n in d.near_upgrade" :key="n.code" class="row-btn" @click="openStock(n.code)">
              <span class="nm">{{ n.name }}</span>
              <span class="pill" :class="poolClass(n.pool)">{{ n.pool_label }}</span>
              <span class="small">差 {{ n.gap.toFixed(2) }} 分到 {{ n.target }}</span>
              <span class="grow"></span>
              <span class="small muted">连续达标 {{ n.consecutive_weeks_above }} 周</span>
            </button>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, POOL_LABELS, poolClass } from '../api/client.js'
import PoolCard from '../components/PoolCard.vue'
import ScoreBar from '../components/ScoreBar.vue'

const router = useRouter()
const d = ref(null)
const loading = ref(true)
const error = ref('')

const poolOrder = ['candidate', 'observation', 'reserve', 'tuna', 'removed']

function labelOf(p) {
  return POOL_LABELS[p] || p || ''
}

function openStock(code) {
  router.push(`/stocks/${code}`)
}

function goPools() {
  router.push('/pools')
}

function goPoolsWith(pool) {
  router.push({ path: '/pools', query: { pool } })
}

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
.dash { display: flex; flex-direction: column; gap: 14px; }
.stale-bar {
  padding: 9px 13px;
  border-radius: 8px;
  background: var(--amber-soft);
  color: var(--amber);
  font-size: 12.5px;
  cursor: pointer;
  display: flex;
  gap: 8px;
  align-items: center;
}
.stale-bar .link { margin-left: auto; text-decoration: underline; }
.warn-ico { font-size: 13px; }

.pool-row { display: flex; gap: 10px; }
.footnote { margin: 10px 0 0; }

.card-body.tight { padding: 4px 8px; }
.row-btn {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 8px 8px;
  border: none;
  background: none;
  cursor: pointer;
  text-align: left;
  border-bottom: 1px solid #f3f4f7;
  border-radius: 5px;
}
.row-btn:last-child { border-bottom: none; }
.row-btn:hover { background: #faf9fd; }
.row-btn .nm { font-weight: 500; }
.row-btn .code { font-variant-numeric: tabular-nums; }
.row-btn .rank { width: 16px; color: var(--fg-3); font-size: 11px; }
.row-btn .reason { color: var(--fg-2); }
.grow { flex: 1; }
.dotdir { width: 14px; text-align: center; font-size: 11px; }
.dotdir.up { color: var(--red); }
.dotdir.down { color: var(--green); }
.dotdir.tuna { color: var(--purple); }
.dotdir.in { color: var(--purple); }
.dotdir.out { color: #98a2b3; }

.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

.gap-ok, .gap-miss { display: flex; flex-direction: column; gap: 3px; }
.big { font-size: 16px; font-weight: 600; }
.gap-miss .big { color: #b42318; }

@media (max-width: 1080px) {
  .two-col { grid-template-columns: 1fr; }
  .pool-row { flex-wrap: wrap; }
}
</style>
