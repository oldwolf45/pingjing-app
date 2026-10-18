<template>
  <div class="stack">
    <section class="card">
      <div class="card-head">
        <h2>推荐追踪</h2>
        <span class="hint">快照只追加 · 三档分开算</span>
      </div>
      <div class="card-body">
        <div class="cards">
          <div class="stat">
            <div class="k">累计推荐</div>
            <div class="v">{{ stats.total ?? 0 }}</div>
            <div class="n">每一条都留了当时的评分和价格</div>
          </div>
          <div v-for="t in tierList" :key="t.key" class="stat" :class="t.key">
            <div class="k">{{ t.label }}</div>
            <div class="v">
              <template v-if="t.sampleEnough">{{ t.rate }}<span class="pct">%</span></template>
              <template v-else>—</template>
            </div>
            <div class="n">
              <template v-if="t.sampleEnough">{{ t.hit }} / {{ t.evaluated }} 条命中</template>
              <template v-else>{{ t.sampleNote }}</template>
            </div>
            <div class="hintline">{{ t.hint }}</div>
          </div>
        </div>

        <p class="lead">
          三档统计永远分开：看错方向（硬失误）、方向对但没赚（到期不赚）、
          赚了但没跑赢大盘（跑输大盘），要改的东西完全不同，合成一个"成功率"等于什么都没说。
        </p>
        <p v-if="error" class="err">{{ error }}</p>
      </div>
    </section>

    <section class="card">
      <div class="card-head">
        <h2>推荐记录</h2>
        <span class="hint">{{ items.length }} 条</span>
      </div>
      <div class="card-body">
        <div v-if="!items.length" class="empty">
          还没有推荐记录。在个股详情页点「记一条推荐」，就会留一张当时的快照。
        </div>
        <table v-else class="tbl">
          <thead>
            <tr>
              <th style="width: 96px">日期</th>
              <th style="width: 82px">代码</th>
              <th>名称</th>
              <th class="r">推荐时分数</th>
              <th class="r">推荐价</th>
              <th class="r">最新价</th>
              <th class="r">涨跌</th>
              <th>三档判定</th>
              <th style="width: 60px"></th>
            </tr>
          </thead>
          <tbody>
            <template v-for="r in items" :key="r.id">
              <tr>
                <td class="num">{{ r.as_of_date }}</td>
                <td class="num">{{ r.code }}</td>
                <td>{{ r.stockName || r.name }}</td>
                <td class="r num">{{ r.total_score === null ? '—' : r.total_score.toFixed(2) }}</td>
                <td class="r num">{{ r.price_cents ? (r.price_cents / 100).toFixed(2) : '—' }}</td>
                <td class="r num">{{ lastPrice(r) }}</td>
                <td class="r num" :class="pnlClass(lastPct(r))">{{ lastPct(r) === null ? '—' : lastPct(r) + '%' }}</td>
                <td>
                  <span v-for="t in tierList" :key="t.key" class="vd" :class="verdictClass(r, t.key)">
                    {{ t.label }}·{{ verdictText(r, t.key) }}
                  </span>
                </td>
                <td>
                  <button class="btn sm" @click="toggle(r.id)">
                    {{ opened.has(r.id) ? '收起' : '展开' }}
                  </button>
                </td>
              </tr>
              <tr v-if="opened.has(r.id)" class="detail-row">
                <td colspan="9">
                  <div class="detail">
                    <div>
                      <div class="dt">当时快照</div>
                      <pre>{{ pretty(r.snapshotDecoded) }}</pre>
                    </div>
                    <div>
                      <div class="dt">跟踪曲线（{{ (r.tracks || []).length }} 个点）</div>
                      <div v-if="!(r.tracks || []).length" class="small muted">还没有跟踪数据，跑一次更新会补</div>
                      <ul v-else class="trackline">
                        <li v-for="t in r.tracks" :key="t.id">
                          <span class="num">{{ t.as_of_date }}</span>
                          <span class="num">{{ t.price_cents ? (t.price_cents / 100).toFixed(2) : '—' }}</span>
                          <span class="num" :class="pnlClass(t.pct_change)">{{ t.pct_change === null ? '—' : t.pct_change + '%' }}</span>
                        </li>
                      </ul>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-head"><h2>还不能画的图</h2></div>
      <div class="card-body">
        <div class="cards">
          <div class="stat muted-card">
            <div class="k">命中率按评分档位</div>
            <div class="v">—</div>
            <div class="n">{{ stats.byScoreBand?.note || '样本够了才能画' }}</div>
          </div>
          <div class="stat muted-card">
            <div class="k">维度归因</div>
            <div class="v">—</div>
            <div class="n">{{ stats.byDimension?.note || '样本够了才能画' }}</div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
// T5.5 / T5.6。硬规则：三档分开统计，样本 <10 一律显示「—」，不推算。
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client.js'

const stats = ref({ total: 0, tiers: {}, byScoreBand: {}, byDimension: {} })
const items = ref([])
const opened = ref(new Set())
const error = ref('')

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
function verdictClass(r, tier) {
  const v = r.evaluations?.[tier]?.verdict
  return { hit: 'hit', miss: 'miss', pending: 'pending' }[v] || 'pending'
}
function toggle(id) {
  const s = new Set(opened.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  opened.value = s
}

async function load() {
  try {
    const d = await api.recommendations()
    items.value = d.items || []
    stats.value = d.stats || stats.value
  } catch (e) {
    error.value = `读取失败：${e.message}`
  }
}

onMounted(load)
</script>

<style scoped>
.stack { display: flex; flex-direction: column; gap: 14px; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.stat {
  flex: 1 1 160px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 12px;
  background: #fcfcfd;
}
.stat .k { font-size: 11px; color: var(--fg-3); }
.stat .v { font-size: 22px; font-weight: 600; line-height: 1.3; }
.stat .pct { font-size: 12px; font-weight: 400; margin-left: 1px; }
.stat .n { font-size: 11px; color: var(--fg-2); }
.stat .hintline { font-size: 10px; color: var(--fg-3); margin-top: 3px; }
.stat.hard_miss .v { color: var(--red); }
.stat.flat .v { color: var(--amber); }
.stat.lag_benchmark .v { color: var(--purple); }
.muted-card { opacity: 0.75; background: repeating-linear-gradient(
  45deg, #fafafa, #fafafa 6px, #f2f3f5 6px, #f2f3f5 12px); }

.lead { font-size: 12px; color: var(--fg-2); margin: 4px 0 0; line-height: 1.7; }
.err { color: var(--red); font-size: 12px; }
.r { text-align: right; }
.up { color: var(--red); }
.down { color: var(--green); }

.vd {
  display: inline-block;
  font-size: 10px;
  border-radius: 3px;
  padding: 1px 4px;
  margin-right: 4px;
  border: 1px solid transparent;
}
.vd.hit { background: #fdecea; color: var(--red); }
.vd.miss { background: #eaf6ee; color: var(--green); }
.vd.pending { background: #f2f3f5; color: var(--fg-3); }

.detail-row td { background: #fafbfc; }
.detail { display: flex; gap: 20px; }
.detail > div { flex: 1; min-width: 0; }
.dt { font-size: 11px; color: var(--fg-3); margin-bottom: 4px; }
pre {
  margin: 0;
  font-size: 11px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 8px;
  max-height: 190px;
  overflow: auto;
}
.trackline { list-style: none; margin: 0; padding: 0; font-size: 12px; }
.trackline li { display: flex; gap: 16px; padding: 2px 0; }
</style>
