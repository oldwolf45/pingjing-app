<template>
  <div class="stack">
    <section class="card">
      <div class="card-head">
        <h2>持仓</h2>
        <span class="hint">{{ data.portfolio?.name || '主账户' }}</span>
        <div class="spacer"></div>
        <span class="tag">总资产 {{ yuan(data.totalAssetsCents) }}</span>
        <span class="tag">仓位 {{ data.cashWeightPct === null ? '—' : (100 - data.cashWeightPct).toFixed(1) + '%' }}</span>
      </div>
      <div class="card-body">
        <div class="summary">
          <div><span class="k">持仓市值</span><span class="v num">{{ yuan(data.marketValueCents) }}</span></div>
          <div><span class="k">现金</span><span class="v num">{{ yuan(data.cashCents) }}</span></div>
          <div>
            <span class="k">浮动盈亏</span>
            <span class="v num" :class="pnlClass(data.totalPnlCents)">
              {{ data.totalPnlCents === null ? '—' : signed(data.totalPnlCents) }}
              <span v-if="data.totalPnlPct !== null" class="dim">（{{ data.totalPnlPct }}%）</span>
            </span>
          </div>
          <div><span class="k">已实现</span><span class="v num">{{ signed(data.realizedCents) }}</span></div>
        </div>

        <p v-if="data.incomplete" class="warn">
          有 {{ data.missingPrice?.length }} 只票没有现价（{{ data.missingPrice?.join('、') }}），
          总资产和占比不完整。跑一次更新就能补上。
        </p>
        <p v-if="error" class="err">{{ error }}</p>

        <div v-if="!data.positions || !data.positions.length" class="empty">
          还没有持仓。下面录第一笔买入。
        </div>
        <table v-else class="tbl">
          <thead>
            <tr>
              <th style="width: 82px">代码</th>
              <th>名称</th>
              <th class="r">持股</th>
              <th class="r">成本价</th>
              <th class="r">现价</th>
              <th class="r">市值</th>
              <th class="r">浮动盈亏</th>
              <th class="r">仓位占比</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in data.positions" :key="p.code" :class="{ over: p.overLimit }">
              <td class="num">{{ p.code }}</td>
              <td><a href="#" class="link" @click.prevent="open(p.code)">{{ nameOf(p.code) }}</a></td>
              <td class="r num">{{ p.shares }}</td>
              <td class="r num">{{ p.costPrice.toFixed(3) }}</td>
              <td class="r num">
                {{ p.price === null ? '缺数据' : p.price.toFixed(2) }}
                <div v-if="p.quotedAt" class="stamp">价格时间 {{ fmtTime(p.quotedAt) }}</div>
              </td>
              <td class="r num">{{ p.marketValueCents === null ? '—' : yuan(p.marketValueCents) }}</td>
              <td class="r num" :class="pnlClass(p.pnlCents)">
                {{ p.pnlCents === null ? '—' : signed(p.pnlCents) }}
                <span v-if="p.pnlPct !== null" class="dim">（{{ p.pnlPct }}%）</span>
              </td>
              <td class="r num">
                <template v-if="p.weightPct === null">—</template>
                <template v-else>
                  {{ p.weightPct }}%
                  <span v-if="p.overLimit" class="badge">超{{ data.alertPct }}%</span>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-head">
        <h2>交易流水</h2>
        <span class="hint">只能作废，不能改</span>
        <div class="spacer"></div>
        <button class="btn sm" @click="showForm = !showForm">
          {{ showForm ? '收起' : '+ 录入交易' }}
        </button>
      </div>
      <div class="card-body">
        <div v-if="showForm" class="form">
          <label>代码<input v-model="form.code" placeholder="301095" /></label>
          <label>方向
            <select v-model="form.side">
              <option value="BUY">买入</option>
              <option value="SELL">卖出</option>
            </select>
          </label>
          <label>股数<input v-model.number="form.shares" type="number" min="1" /></label>
          <label>价格（元）<input v-model.number="form.price" type="number" step="0.01" /></label>
          <label>手续费（元）<input v-model.number="form.fee" type="number" step="0.01" /></label>
          <label>日期<input v-model="form.date" type="date" /></label>
          <button class="btn primary sm" :disabled="busy" @click="submit">记一笔</button>
          <span class="small muted">成本价由系统按移动加权算，不用填</span>
        </div>
        <p v-if="formError" class="err">{{ formError }}</p>

        <div v-if="!data.trades || !data.trades.length" class="empty">还没有交易记录</div>
        <table v-else class="tbl">
          <thead>
            <tr>
              <th style="width: 96px">日期</th>
              <th style="width: 82px">代码</th>
              <th>名称</th>
              <th style="width: 64px">方向</th>
              <th class="r">价格</th>
              <th class="r">数量</th>
              <th class="r">手续费</th>
              <th class="r">金额</th>
              <th style="width: 76px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in data.trades" :key="t.id" :class="{ voided: t.side === 'VOID' }">
              <td class="num">{{ t.trade_date }}</td>
              <td class="num">{{ t.code }}</td>
              <td>{{ t.name || '—' }}</td>
              <td>
                <span class="side" :class="t.side.toLowerCase()">{{ sideLabel(t.side) }}</span>
              </td>
              <td class="r num">{{ (t.price_cents / 100).toFixed(2) }}</td>
              <td class="r num">{{ t.shares }}</td>
              <td class="r num">{{ (t.fee_cents / 100).toFixed(2) }}</td>
              <td class="r num">{{ money(t) }}</td>
              <td>
                <button
                  v-if="t.side !== 'VOID' && !voidedIds.has(t.id)"
                  class="btn sm"
                  :disabled="busy"
                  @click="onVoid(t)"
                >作废</button>
                <span v-else-if="t.side === 'VOID'" class="small muted">作废记录</span>
                <span v-else class="small muted">已作废</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
// T5.2 持仓+交易、T5.3 作废、T5.4 仓位告警。
// 关键口径都写在这里，别在别处再算一遍：
//   · 成本价含手续费，卖出不变
//   · 占比分母 = 个股市值 + 现金
//   · 作废 = 插一条 VOID，后端整链重算
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client.js'

const router = useRouter()
const data = ref({ positions: [], trades: [], portfolios: [] })
const showForm = ref(false)
const busy = ref(false)
const error = ref('')
const formError = ref('')

function today() {
  const d = new Date()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

const form = ref({ code: '', side: 'BUY', shares: 100, price: null, fee: 0, date: today() })

// 后续 VOID 记录指向原交易，前端据此把原行标成"已作废"
const voidedIds = computed(() => new Set(
  (data.value.trades || []).filter((t) => t.side === 'VOID' && t.voids_trade_id)
    .map((t) => t.voids_trade_id)))

function yuan(cents) {
  if (cents === null || cents === undefined) return '—'
  return (cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function signed(cents) {
  if (cents === null || cents === undefined) return '—'
  return (cents > 0 ? '+' : '') + yuan(cents)
}
function pnlClass(cents) {
  if (cents === null || cents === undefined) return ''
  if (cents > 0) return 'up'
  if (cents < 0) return 'down'
  return ''
}
function money(t) {
  const amt = (t.shares * t.price_cents) / 100
  return amt.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function sideLabel(s) {
  return { BUY: '买入', SELL: '卖出', VOID: '作废' }[s] || s
}
function nameOf(code) {
  const t = (data.value.trades || []).find((x) => x.code === code && x.name)
  return t ? t.name : code
}
function fmtTime(iso) {
  return String(iso || '').replace('T', ' ').slice(0, 16)
}
function open(code) {
  router.push(`/stocks/${code}`)
}

async function load() {
  try {
    data.value = await api.portfolio()
  } catch (e) {
    error.value = `读取失败：${e.message}`
  }
}

async function submit() {
  formError.value = ''
  if (!form.value.code.trim()) { formError.value = '填一下代码'; return }
  if (!form.value.price || form.value.price <= 0) { formError.value = '价格要大于 0'; return }
  busy.value = true
  try {
    const r = await api.addTrade({
      code: form.value.code.trim(),
      side: form.value.side,
      shares: Number(form.value.shares),
      priceCents: Math.round(Number(form.value.price) * 100),
      feeCents: Math.round(Number(form.value.fee || 0) * 100),
      tradeDate: form.value.date,
    })
    data.value = { ...data.value, ...r.valuation, trades: r.valuation.trades || [] }
    await load()
  } catch (e) {
    formError.value = e.message
  } finally {
    busy.value = false
  }
}

async function onVoid(t) {
  if (!window.confirm(`作废 ${t.trade_date} 这笔 ${sideLabel(t.side)} ${t.shares} 股？\n作废后会从第一条流水重算全部持仓。`)) return
  busy.value = true
  formError.value = ''
  try {
    await api.voidTrade({ tradeId: t.id })
    await load()
  } catch (e) {
    formError.value = e.message
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.stack { display: flex; flex-direction: column; gap: 14px; }
.summary { display: flex; gap: 26px; flex-wrap: wrap; margin-bottom: 12px; }
.summary .k { display: block; color: var(--fg-3); font-size: 11px; }
.summary .v { font-size: 15px; font-weight: 600; }
.r { text-align: right; }
.dim { color: var(--fg-3); font-size: 11px; }
.stamp { color: var(--fg-3); font-size: 10px; font-weight: 400; }
.up { color: var(--red); }
.down { color: var(--green); }
.err { color: var(--red); font-size: 12px; margin: 0 0 8px; }
.warn {
  background: var(--amber-soft);
  border: 1px solid #f0d9a0;
  border-radius: 5px;
  padding: 7px 10px;
  font-size: 12px;
  color: #7a4b0a;
  margin: 0 0 10px;
}
tr.over td { background: #fdf3f3; }
tr.over td:nth-child(8) { color: var(--red); font-weight: 600; }
tr.voided td { opacity: 0.45; text-decoration: line-through; }
.badge {
  display: inline-block;
  background: var(--red);
  color: #fff;
  border-radius: 3px;
  padding: 0 4px;
  font-size: 10px;
  margin-left: 3px;
}
.side { font-size: 12px; }
.side.buy { color: var(--red); }
.side.sell { color: var(--green); }
.side.void { color: var(--fg-3); }

.form {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  flex-wrap: wrap;
  background: #fafbfc;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.form label { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: var(--fg-2); }
.form input, .form select {
  height: 28px;
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  padding: 0 7px;
  background: #fff;
}
.form input[type='number'] { width: 90px; }
</style>
