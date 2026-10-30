<template>
  <el-card shadow="never">
    <template #header>
      <div class="card-header">
        <span class="title">决策清单</span>
        <span class="subtitle">{{ data.week || '—' }}</span>
        <div class="spacer"></div>
        <el-tag type="primary" effect="plain" size="small">
          {{ data.decided || 0 }} / {{ data.total || 0 }} 已定
        </el-tag>
      </div>
    </template>

    <el-alert type="info" :closable="false" show-icon class="lead-alert">
      <template #title>
        每周日晚拍板。点一下就是决定，没有提交按钮——关掉窗口也不会丢。
      </template>
    </el-alert>

    <el-table
      v-loading="loading"
      :data="data.items || []"
      class="decision-tbl"
      :row-class-name="row => row.row.weekDecision ? 'row-done' : ''"
      empty-text=""
    >
      <template #empty>
        <el-empty description="池子里还没有票。先去「股票池」加标的，或者点右上角跑一次更新。" :image-size="80" />
      </template>

      <el-table-column prop="code" label="代码" width="85">
        <template #default="{ row }">
          <span class="num code-col">{{ row.code }}</span>
        </template>
      </el-table-column>

      <el-table-column prop="name" label="名称" min-width="110">
        <template #default="{ row }">
          <el-link type="primary" @click="open(row.code)">{{ row.name }}</el-link>
        </template>
      </el-table-column>

      <el-table-column prop="sector" label="赛道" min-width="100">
        <template #default="{ row }">
          <span class="muted">{{ row.sector || '—' }}</span>
        </template>
      </el-table-column>

      <el-table-column label="所在池" width="90">
        <template #default="{ row }">
          <el-tag :type="poolTagType(row.pool)" size="small" effect="plain">
            {{ poolLabel(row.pool) }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="本周决定" min-width="320">
        <template #default="{ row }">
          <el-radio-group
            v-model="row.weekDecision"
            size="default"
            @change="val => pick(row, val)"
          >
            <el-radio-button
              v-for="opt in options"
              :key="opt.value"
              :label="opt.value"
              :disabled="saving === row.code"
            >
              {{ opt.label }}
            </el-radio-button>
          </el-radio-group>
        </template>
      </el-table-column>

      <el-table-column label="上次" min-width="140">
        <template #default="{ row }">
          <template v-if="row.lastDecisionLabel">
            <el-tag size="small" effect="plain" :type="decisionTagType(row.lastDecision)">
              {{ row.lastDecisionLabel }}
            </el-tag>
            <span class="muted small" style="margin-left: 6px">· {{ row.lastDecisionWeek }}</span>
          </template>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, POOL_LABELS } from '../api/client.js'

const router = useRouter()
const data = ref({ items: [], week: '', decided: 0, total: 0 })
const loading = ref(true)
const saving = ref('')

const options = [
  { value: 'add', label: '加仓' },
  { value: 'hold', label: '持有' },
  { value: 'trim', label: '减仓' },
  { value: 'watch', label: '观察' },
]

function poolLabel(p) { return POOL_LABELS[p] || p || '—' }
function poolTagType(p) {
  const m = { candidate: 'danger', observation: 'warning', reserve: 'primary', tuna: '', removed: 'info' }
  return m[p] || 'info'
}
function decisionTagType(d) {
  const m = { add: 'danger', hold: 'primary', trim: 'success', watch: 'info' }
  return m[d] || 'info'
}

function open(code) { router.push(`/stocks/${code}`) }

async function load() {
  loading.value = true
  try {
    data.value = await api.decisions()
  } catch (e) {
    ElMessage.error(`读取失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

async function pick(it, value) {
  const prev = it.weekDecision
  it.weekDecision = value
  saving.value = it.code
  try {
    await api.setDecision({ code: it.code, week: data.value.week, decision: value })
    data.value = await api.decisions()
  } catch (e) {
    it.weekDecision = prev
    ElMessage.error(`保存失败：${e.message}`)
  } finally {
    saving.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
}
.card-header .title { font-weight: 600; font-size: 14px; }
.card-header .subtitle { font-size: 12px; color: var(--text-tertiary); }
.spacer { flex: 1; }

.lead-alert { margin-bottom: 16px; }

.num { font-variant-numeric: tabular-nums; }
.code-col { color: var(--text-secondary); font-size: 12.5px; }
.muted { color: var(--text-tertiary); }
.small { font-size: 11.5px; }

.decision-tbl :deep(.row-done td) {
  background: #faf8ff !important;
}
</style>
