<template>
  <div class="portfolio-page">
    <!-- 持仓总览 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="title">持仓</span>
          <span class="subtitle">{{ data.portfolio?.name || '主账户' }}</span>
          <div class="spacer"></div>
          <el-tag type="primary" effect="plain" size="small">
            总资产 {{ yuan(data.totalAssetsCents) }}
          </el-tag>
          <el-tag type="info" effect="plain" size="small">
            仓位 {{ data.cashWeightPct === null ? '—' : (100 - data.cashWeightPct).toFixed(1) + '%' }}
          </el-tag>
        </div>
      </template>

      <el-row :gutter="24" class="summary-row">
        <el-col :sm="12" :md="6">
          <el-statistic title="持仓市值" :value="yuan(data.marketValueCents)" />
        </el-col>
        <el-col :sm="12" :md="6">
          <el-statistic title="现金" :value="yuan(data.cashCents)" />
        </el-col>
        <el-col :sm="12" :md="6">
          <el-statistic title="浮动盈亏" :value="signed(data.totalPnlCents)">
            <template #suffix>
              <span v-if="data.totalPnlPct !== null" class="pnl-pct">（{{ data.totalPnlPct }}%）</span>
            </template>
          </el-statistic>
        </el-col>
        <el-col :sm="12" :md="6">
          <el-statistic title="已实现" :value="signed(data.realizedCents)" />
        </el-col>
      </el-row>

      <el-alert
        v-if="data.incomplete"
        type="warning"
        :closable="false"
        show-icon
        class="warn-alert"
      >
        <template #title>
          有 {{ data.missingPrice?.length }} 只票没有现价（{{ data.missingPrice?.join('、') }}），
          总资产和占比不完整。跑一次更新就能补上。
        </template>
      </el-alert>

      <el-table
        v-loading="loading"
        :data="data.positions || []"
        :row-class-name="row => row.row.overLimit ? 'row-over' : ''"
        empty-text=""
        @row-click="row => open(row.code)"
      >
        <template #empty>
          <el-empty description="还没有持仓。下面录第一笔买入。" :image-size="80" />
        </template>

        <el-table-column prop="code" label="代码" width="85">
          <template #default="{ row }">
            <span class="num code-col">{{ row.code }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="name" label="名称" min-width="110">
          <template #default="{ row }">
            <el-link type="primary" @click.stop="open(row.code)">{{ nameOf(row.code) }}</el-link>
          </template>
        </el-table-column>

        <el-table-column label="持股" width="90" align="right">
          <template #default="{ row }">
            <span class="num">{{ row.shares }}</span>
          </template>
        </el-table-column>

        <el-table-column label="成本价" width="90" align="right">
          <template #default="{ row }">
            <span class="num">{{ row.costPrice.toFixed(3) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="现价" width="100" align="right">
          <template #default="{ row }">
            <div class="price-cell">
              <span class="num">{{ row.price === null ? '缺数据' : row.price.toFixed(2) }}</span>
              <span v-if="row.quotedAt" class="stamp">价格时间 {{ fmtTime(row.quotedAt) }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="市值" width="110" align="right">
          <template #default="{ row }">
            <span class="num">{{ row.marketValueCents === null ? '—' : yuan(row.marketValueCents) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="浮动盈亏" width="130" align="right">
          <template #default="{ row }">
            <div class="pnl-cell" :class="pnlClass(row.pnlCents)">
              <span class="num">{{ row.pnlCents === null ? '—' : signed(row.pnlCents) }}</span>
              <span v-if="row.pnlPct !== null" class="pct">（{{ row.pnlPct }}%）</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="仓位占比" width="120" align="right">
          <template #default="{ row }">
            <template v-if="row.weightPct === null">—</template>
            <template v-else>
              <span class="num">{{ row.weightPct }}%</span>
              <el-tag v-if="row.overLimit" type="danger" size="small" effect="dark" style="margin-left:4px">
                超{{ data.alertPct }}%
              </el-tag>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 交易流水 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="title">交易流水</span>
          <span class="subtitle">只能作废，不能改</span>
          <div class="spacer"></div>
          <el-button type="primary" size="small" @click="showForm = !showForm">
            {{ showForm ? '收起表单' : '录入交易' }}
          </el-button>
        </div>
      </template>

      <el-form
        v-if="showForm"
        :model="form"
        inline
        class="trade-form"
        label-width="60px"
      >
        <el-form-item label="代码">
          <el-input v-model="form.code" placeholder="301095" style="width: 120px" />
        </el-form-item>
        <el-form-item label="方向">
          <el-select v-model="form.side" style="width: 90px">
            <el-option label="买入" value="BUY" />
            <el-option label="卖出" value="SELL" />
          </el-select>
        </el-form-item>
        <el-form-item label="股数">
          <el-input-number v-model="form.shares" :min="1" :step="100" style="width: 130px" />
        </el-form-item>
        <el-form-item label="价格">
          <el-input-number v-model="form.price" :min="0" :precision="2" :step="0.1" style="width: 130px" />
        </el-form-item>
        <el-form-item label="手续费">
          <el-input-number v-model="form.fee" :min="0" :precision="2" :step="0.1" style="width: 110px" />
        </el-form-item>
        <el-form-item label="日期">
          <el-date-picker v-model="form.date" type="date" value-format="YYYY-MM-DD" style="width: 150px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="busy" @click="submit">记一笔</el-button>
        </el-form-item>
        <el-form-item>
          <span class="tip-text">成本价由系统按移动加权算，不用填</span>
        </el-form-item>
      </el-form>

      <el-table
        :data="data.trades || []"
        :row-class-name="row => row.row.side === 'VOID' ? 'row-voided' : ''"
        empty-text=""
      >
        <template #empty>
          <el-empty description="还没有交易记录" :image-size="80" />
        </template>

        <el-table-column prop="trade_date" label="日期" width="105">
          <template #default="{ row }">
            <span class="num">{{ row.trade_date }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="code" label="代码" width="75">
          <template #default="{ row }">
            <span class="num">{{ row.code }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="name" label="名称" min-width="100">
          <template #default="{ row }">{{ row.name || '—' }}</template>
        </el-table-column>

        <el-table-column label="方向" width="70">
          <template #default="{ row }">
            <el-tag :type="sideTagType(row.side)" size="small" effect="plain">
              {{ sideLabel(row.side) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="价格" width="80" align="right">
          <template #default="{ row }">
            <span class="num">{{ (row.price_cents / 100).toFixed(2) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="数量" width="80" align="right">
          <template #default="{ row }">
            <span class="num">{{ row.shares }}</span>
          </template>
        </el-table-column>

        <el-table-column label="手续费" width="80" align="right">
          <template #default="{ row }">
            <span class="num">{{ (row.fee_cents / 100).toFixed(2) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="金额" width="100" align="right">
          <template #default="{ row }">
            <span class="num">{{ money(row) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="80" align="center">
          <template #default="{ row }">
            <template v-if="row.side !== 'VOID' && !voidedIds.has(row.id)">
              <el-button type="danger" link size="small" :disabled="busy" @click="onVoid(row)">
                作废
              </el-button>
            </template>
            <span v-else-if="row.side === 'VOID'" class="muted small">作废记录</span>
            <span v-else class="muted small">已作废</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client.js'

const router = useRouter()
const data = ref({ positions: [], trades: [], portfolios: [] })
const loading = ref(true)
const showForm = ref(false)
const busy = ref(false)

function today() {
  const d = new Date()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

const form = ref({ code: '', side: 'BUY', shares: 100, price: 10, fee: 0, date: today() })

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
  if (cents > 0) return 'pnl-up'
  if (cents < 0) return 'pnl-down'
  return ''
}
function money(t) {
  const amt = (t.shares * t.price_cents) / 100
  return amt.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function sideLabel(s) { return { BUY: '买入', SELL: '卖出', VOID: '作废' }[s] || s }
function sideTagType(s) {
  return { BUY: 'danger', SELL: 'success', VOID: 'info' }[s] || 'info'
}
function nameOf(code) {
  const t = (data.value.trades || []).find((x) => x.code === code && x.name)
  return t ? t.name : code
}
function fmtTime(iso) { return String(iso || '').replace('T', ' ').slice(0, 16) }
function open(code) { router.push(`/stocks/${code}`) }

async function load() {
  loading.value = true
  try {
    data.value = await api.portfolio()
  } catch (e) {
    ElMessage.error(`读取失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!form.value.code.trim()) { ElMessage.warning('填一下代码'); return }
  if (!form.value.price || form.value.price <= 0) { ElMessage.warning('价格要大于 0'); return }
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
    ElMessage.success('已录入')
    showForm.value = false
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    busy.value = false
  }
}

async function onVoid(t) {
  try {
    await ElMessageBox.confirm(
      `作废 ${t.trade_date} 这笔 ${sideLabel(t.side)} ${t.shares} 股？\n作废后会从第一条流水重算全部持仓。`,
      '确认作废',
      { type: 'warning', confirmButtonText: '确认作废', cancelButtonText: '取消' }
    )
  } catch { return }
  busy.value = true
  try {
    await api.voidTrade({ tradeId: t.id })
    ElMessage.success('已作废')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.portfolio-page { display: flex; flex-direction: column; gap: 16px; }

.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
}
.card-header .title { font-weight: 600; font-size: 14px; }
.card-header .subtitle { font-size: 12px; color: var(--text-tertiary); }
.spacer { flex: 1; }

.summary-row { margin-bottom: 12px; }
.summary-row :deep(.el-statistic__head) { font-size: 12px; color: var(--text-tertiary); }
.summary-row :deep(.el-statistic__content) { font-size: 18px; font-weight: 600; }
.pnl-pct { font-size: 12px; color: var(--text-tertiary); font-weight: normal; margin-left: 4px; }

.warn-alert { margin-bottom: 12px; }

.num { font-variant-numeric: tabular-nums; }
.code-col { color: var(--text-secondary); font-size: 12.5px; }
.muted { color: var(--text-tertiary); }
.small { font-size: 11.5px; }

.price-cell { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.stamp { color: var(--text-tertiary); font-size: 10px; }

.pnl-cell { display: flex; align-items: baseline; justify-content: flex-end; gap: 4px; }
.pnl-cell.pnl-up { color: var(--danger); }
.pnl-cell.pnl-down { color: var(--success); }
.pnl-cell .pct { font-size: 11px; color: var(--text-tertiary); }
.pnl-up .pct { color: var(--danger); }
.pnl-down .pct { color: var(--success); }

.tip-text { font-size: 11px; color: var(--text-tertiary); }

.trade-form {
  background: #fafbfc;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.trade-form :deep(.el-form-item) { margin-bottom: 8px; }

:deep(.el-table__row) { cursor: pointer; }
:deep(.row-over td) { background: #fef2f2 !important; }
:deep(.row-voided td) { opacity: 0.45; text-decoration: line-through; cursor: default; }
</style>
