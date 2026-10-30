<template>
  <div class="pools">
    <!-- 顶部 Tabs + 搜索 + 按钮 -->
    <div class="toolbar">
      <el-tabs v-model="pool" type="card" size="default" class="pool-tabs" @tab-change="onTabChange">
        <el-tab-pane
          v-for="t in tabs"
          :key="t.key"
          :label="`${t.label} (${t.count})`"
          :name="t.key"
        />
      </el-tabs>

      <div class="toolbar-right">
        <el-input
          v-model="q"
          placeholder="在列表里筛选"
          size="default"
          clearable
          style="width: 200px"
          @input="debouncedLoad"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-select v-model="sortField" size="default" style="width: 110px" @change="load">
          <el-option label="按评分" value="score" />
          <el-option label="按代码" value="code" />
          <el-option label="按名称" value="name" />
          <el-option label="按跟踪周数" value="weeks" />
        </el-select>

        <el-button :icon="desc ? SortDown : SortUp" circle @click="toggleSortDir" />

        <el-button type="primary" :icon="Plus" @click="showAdd = true">添加标的</el-button>
      </div>
    </div>

    <!-- 添加标的弹窗 -->
    <el-dialog
      v-model="showAdd"
      title="添加标的"
      width="440px"
      :close-on-click-modal="false"
    >
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      >
        <template #title>
          人工添加的票一律进<strong>储备池</strong>，当周不给买入价。
          这是规则，不是失败——人工加的票没过情报和一次完整更新的验证，
          储备池就是它的观察位。
        </template>
      </el-alert>

      <el-form :model="addForm" label-width="70px" label-position="left">
        <el-form-item label="代码" required>
          <el-input v-model="addForm.code" placeholder="六位代码" maxlength="6" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="addForm.name" placeholder="股票名称" />
        </el-form-item>
        <el-form-item label="赛道">
          <el-input v-model="addForm.sector" placeholder="可留空" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="addForm.note" type="textarea" :rows="2" placeholder="为什么加它（可留空）" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showAdd = false">取消</el-button>
        <el-button type="primary" :loading="addBusy" @click="submitAdd">确认添加</el-button>
      </template>
    </el-dialog>

    <!-- 列表卡片 -->
    <el-card shadow="never" class="list-card">
      <template #header>
        <div class="card-header">
          <span class="title">{{ currentLabel }}</span>
          <span class="subtitle">
            {{ items.length }} 只
            <span v-if="q"> · 筛选「{{ q }}」</span>
          </span>
        </div>
      </template>

      <el-table
        v-loading="loading"
        :data="items"
        stripe
        @row-click="row => open(row.code)"
        empty-text=""
      >
        <template #empty>
          <el-empty :description="q ? '没有匹配的票' : emptyText" :image-size="80" />
        </template>

        <el-table-column prop="code" label="代码" width="85" sortable @sort-change="onSortChange('code', $event)">
          <template #default="{ row }">
            <span class="num code-col">{{ row.code }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="name" label="名称 · 赛道" min-width="160" sortable @sort-change="onSortChange('name', $event)">
          <template #default="{ row }">
            <div class="name-col">
              <div class="nm">{{ row.name }}</div>
              <div class="sector">{{ row.sector || '—' }}</div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="评分" min-width="160" sortable @sort-change="onSortChange('score', $event)">
          <template #default="{ row }">
            <div class="score-col">
              <el-progress
                :percentage="((row.total || 0) / 9) * 100"
                :stroke-width="8"
                :show-text="false"
                color="var(--primary)"
              />
              <span class="score-num">{{ row.total?.toFixed?.(2) || '—' }}</span>
              <el-tag v-if="row.is_fallback" type="warning" size="small" effect="light">兜底</el-tag>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="池" width="90">
          <template #default="{ row }">
            <el-tag :type="poolTagType(row.pool)" size="small" effect="plain">
              {{ row.pool_label }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="建议买点" width="110">
          <template #default="{ row }">
            <template v-if="row.price_ready">{{ row.ideal_buy_text || '—' }}</template>
            <el-tag v-else type="warning" size="small" effect="light">待确认</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="止损位" width="100">
          <template #default="{ row }">
            {{ row.stop_loss_text || '—' }}
          </template>
        </el-table-column>

        <el-table-column label="跟踪" width="90" align="right" sortable @sort-change="onSortChange('weeks', $event)">
          <template #default="{ row }">
            <span class="muted">{{ row.weeks_in_pool }} 周</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <p class="tip">
      <el-icon><InfoFilled /></el-icon>
      买点列显示「待确认」表示这只票进池当周还没建仓，按规则第 2 周才给数字。
      分数旁的「兜底」表示该赛道情报还没录入，壁垒按 1.2 兜底算的，别当真实分数看。
    </p>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, Plus, SortDown, SortUp, InfoFilled } from '@element-plus/icons-vue'
import { api, POOL_LABELS } from '../api/client.js'

const route = useRoute()
const router = useRouter()

const tabs = ref([
  { key: 'all', label: '全部', count: 0 },
  { key: 'candidate', label: '候选', count: 0 },
  { key: 'observation', label: '观察', count: 0 },
  { key: 'reserve', label: '储备', count: 0 },
  { key: 'tuna', label: '金枪鱼', count: 0 },
  { key: 'removed', label: '已移除', count: 0 },
])

const pool = ref(route.query.pool || 'candidate')
const q = ref(route.query.q || '')
const sortField = ref('score')
const desc = ref(true)

const showAdd = ref(false)
const addBusy = ref(false)
const addForm = ref({ code: '', name: '', sector: '', note: '' })

const items = ref([])
const loading = ref(true)
const error = ref('')

const currentLabel = computed(() => {
  if (pool.value === 'all') return '全部标的'
  return POOL_LABELS[pool.value] || pool.value
})

const emptyText = computed(
  () => (pool.value === 'removed' ? '还没有票被移除' : '这个池子现在是空的')
)

function poolTagType(p) {
  const m = { candidate: 'danger', observation: 'warning', reserve: 'primary', tuna: '', removed: 'info' }
  return m[p] || 'info'
}

let timer = null
function debouncedLoad() {
  clearTimeout(timer)
  timer = setTimeout(load, 200)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const r = await api.pools({ pool: pool.value, q: q.value, sort: sortField.value, desc: desc.value })
    items.value = r.items
    const dist = r.distribution
    tabs.value = tabs.value.map((t) => ({
      ...t,
      count:
        t.key === 'all'
          ? Object.values(dist).reduce((a, b) => a + b, 0)
          : dist[t.key] || 0,
    }))
  } catch (e) {
    error.value = `读取失败：${e.message}`
    ElMessage.error(error.value)
  } finally {
    loading.value = false
  }
}

function onTabChange(val) {
  pool.value = val
  router.replace({ path: '/pools', query: { ...route.query, pool: val } })
  load()
}

function onSortChange(field, { order }) {
  if (!order) return
  sortField.value = field
  desc.value = order === 'descending'
  load()
}

function toggleSortDir() {
  desc.value = !desc.value
  load()
}

function open(code) { router.push(`/stocks/${code}`) }

async function submitAdd() {
  if (!addForm.value.code.trim() || !addForm.value.name.trim()) {
    ElMessage.warning('代码和名称都要填')
    return
  }
  addBusy.value = true
  try {
    await api.addStock({
      code: addForm.value.code.trim(),
      name: addForm.value.name.trim(),
      sector: addForm.value.sector.trim() || null,
      note: addForm.value.note.trim() || null,
    })
    ElMessage.success('已添加到储备池')
    showAdd.value = false
    addForm.value = { code: '', name: '', sector: '', note: '' }
    pool.value = 'reserve'
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    addBusy.value = false
  }
}

watch(
  () => route.query,
  (nq) => {
    if (nq.pool && nq.pool !== pool.value) {
      pool.value = nq.pool
      load()
    }
    if (nq.q !== undefined && nq.q !== q.value) {
      q.value = nq.q
      load()
    }
  }
)

onMounted(load)
</script>

<style scoped>
.pools { display: flex; flex-direction: column; gap: 14px; }

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.pool-tabs {
  margin-bottom: 0 !important;
}
.pool-tabs :deep(.el-tabs__item) {
  padding: 0 16px;
  height: 36px;
  line-height: 34px;
  font-size: 13px;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.list-card { }

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-header .title { font-weight: 600; font-size: 14px; }
.card-header .subtitle { font-size: 12px; color: var(--text-tertiary); }

.num { font-variant-numeric: tabular-nums; }
.code-col { color: var(--text-secondary); font-size: 12.5px; }

.name-col .nm { font-weight: 500; font-size: 13px; }
.name-col .sector { font-size: 11.5px; color: var(--text-tertiary); margin-top: 2px; }

.score-col {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 130px;
}
.score-col :deep(.el-progress) { flex: 1; min-width: 60px; }
.score-num {
  font-variant-numeric: tabular-nums;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--primary);
  white-space: nowrap;
  min-width: 36px;
}

.muted { color: var(--text-tertiary); font-size: 12px; }

.tip {
  margin: 0;
  font-size: 12px;
  color: var(--text-tertiary);
  display: flex;
  align-items: flex-start;
  gap: 6px;
  line-height: 1.7;
}
.tip .el-icon { margin-top: 2px; flex-shrink: 0; }

:deep(.el-table__row) { cursor: pointer; }
</style>
