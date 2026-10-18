<template>
  <div class="pools">
    <div class="tabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="tab"
        :class="{ active: pool === t.key }"
        @click="setPool(t.key)"
      >
        {{ t.label }}
        <span class="cnt num">{{ t.count }}</span>
      </button>
      <div class="spacer"></div>
      <input
        v-model="q"
        class="pool-search"
        placeholder="在列表里筛选"
        @input="debouncedLoad"
      />
      <button class="btn sm" @click="toggleSort">
        排序：{{ sortLabel }}{{ desc ? ' ↓' : ' ↑' }}
      </button>
      <button class="btn sm" @click="showAdd = true">+ 添加标的</button>
    </div>

    <!-- T5.9：人工添加只能进储备池，当周没有买入价。这里如实写出来，
         免得用户以为是自己操作错了。 -->
    <div v-if="showAdd" class="add-mask" @click.self="showAdd = false">
      <div class="add-box">
        <h3>添加标的</h3>
        <p class="add-tip">
          人工添加的票一律进<strong>储备池</strong>，当周不给买入价。
          这是规则，不是失败——人工加的票没过情报和一次完整更新的验证，
          储备池就是它的观察位。
        </p>
        <label>代码<input v-model="addForm.code" placeholder="六位代码" /></label>
        <label>名称<input v-model="addForm.name" placeholder="股票名称" /></label>
        <label>赛道<input v-model="addForm.sector" placeholder="可留空" /></label>
        <label>备注<input v-model="addForm.note" placeholder="为什么加它（可留空）" /></label>
        <p v-if="addError" class="err">{{ addError }}</p>
        <div class="add-actions">
          <button class="btn sm" @click="showAdd = false">取消</button>
          <button class="btn primary sm" :disabled="addBusy" @click="submitAdd">
            {{ addBusy ? '提交中…' : '确认添加' }}
          </button>
        </div>
      </div>
    </div>

    <section class="card">
      <div class="card-head">
        <h2>{{ currentLabel }}</h2>
        <span class="hint">{{ items.length }} 只{{ q ? ` · 筛选「${q}」` : '' }}</span>
      </div>

      <div v-if="loading" class="loading">正在读取…</div>
      <div v-else-if="error" class="err">{{ error }}</div>
      <div v-else-if="!items.length" class="empty">
        {{ q ? '没有匹配的票' : emptyText }}
      </div>

      <table v-else class="tbl">
        <thead>
          <tr>
            <th class="sortable" @click="sortBy('code')">代码</th>
            <th class="sortable" @click="sortBy('name')">名称 · 赛道</th>
            <th class="sortable" @click="sortBy('score')">评分</th>
            <th>池</th>
            <th>建议买点</th>
            <th>止损位</th>
            <th class="sortable" @click="sortBy('weeks')">跟踪</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="it in items" :key="it.code" @click="open(it.code)">
            <td class="num">{{ it.code }}</td>
            <td>
              <div class="nm">{{ it.name }}</div>
              <div class="small muted">{{ it.sector || '—' }}</div>
            </td>
            <td>
              <ScoreBar :value="it.total" :full="9" />
              <span v-if="it.is_fallback" class="badge-fallback" style="margin-left: 6px">兜底分</span>
            </td>
            <td><span class="pill" :class="poolClass(it.pool)">{{ it.pool_label }}</span></td>
            <td>
              <template v-if="it.price_ready">{{ it.ideal_buy_text || '—' }}</template>
              <span v-else class="badge-fallback">待确认</span>
            </td>
            <td>{{ it.stop_loss_text || '—' }}</td>
            <td class="num small">{{ it.weeks_in_pool }} 周</td>
          </tr>
        </tbody>
      </table>
    </section>

    <p class="small muted tip">
      买点列显示「待确认」表示这只票进池当周还没建仓，按规则第 2 周才给数字。
      分数旁的「兜底分」表示该赛道情报还没录入，壁垒按 1.2 兜底算的，别当真实分数看。
    </p>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, POOL_LABELS, poolClass } from '../api/client.js'
import ScoreBar from '../components/ScoreBar.vue'

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
const sort = ref('score')

// ---- 添加标的（T5.9）：池子落点由后端硬编码，这里不传、也传不进去 ----
const showAdd = ref(false)
const addBusy = ref(false)
const addError = ref('')
const addForm = ref({ code: '', name: '', sector: '', note: '' })

async function submitAdd() {
  addError.value = ''
  if (!addForm.value.code.trim() || !addForm.value.name.trim()) {
    addError.value = '代码和名称都要填'
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
    showAdd.value = false
    addForm.value = { code: '', name: '', sector: '', note: '' }
    pool.value = 'reserve'      // 加完直接跳储备池，让用户看见它落哪了
    await load()
  } catch (e) {
    addError.value = e.message
  } finally {
    addBusy.value = false
  }
}
const desc = ref(true)
const items = ref([])
const loading = ref(true)
const error = ref('')

const currentLabel = computed(() => {
  if (pool.value === 'all') return '全部标的'
  return POOL_LABELS[pool.value] || pool.value
})

const sortLabel = computed(
  () => ({ score: '评分', code: '代码', name: '名称', weeks: '跟踪周数' }[sort.value] || '评分')
)

const emptyText = computed(
  () => (pool.value === 'removed' ? '还没有票被移除' : '这个池子现在是空的')
)

let timer = null
function debouncedLoad() {
  clearTimeout(timer)
  timer = setTimeout(load, 200)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const r = await api.pools({ pool: pool.value, q: q.value, sort: sort.value, desc: desc.value })
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
  } finally {
    loading.value = false
  }
}

function setPool(p) {
  pool.value = p
  router.replace({ path: '/pools', query: { ...route.query, pool: p } })
  load()
}

function sortBy(field) {
  if (sort.value === field) {
    desc.value = !desc.value
  } else {
    sort.value = field
    desc.value = field !== 'code'
  }
  load()
}

function toggleSort() {
  const order = ['score', 'code', 'weeks']
  const i = order.indexOf(sort.value)
  sort.value = order[(i + 1) % order.length]
  load()
}

function open(code) {
  router.push(`/stocks/${code}`)
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
.pools { display: flex; flex-direction: column; gap: 12px; }
.tabs { display: flex; align-items: center; gap: 6px; }
.tab {
  border: 1px solid var(--line);
  background: #fff;
  border-radius: 16px;
  padding: 4px 12px;
  cursor: pointer;
  font-size: 12.5px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tab:hover { border-color: var(--purple); }
.tab.active { background: var(--purple); border-color: var(--purple); color: #fff; }
.tab .cnt { font-size: 11px; opacity: 0.75; }
.spacer { flex: 1; }
.pool-search {
  height: 28px;
  width: 180px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0 9px;
  outline: none;
}
.pool-search:focus { border-color: var(--purple); }
.nm { font-weight: 500; }
.tip { margin: 0 2px; line-height: 1.7; }

/* ---- 添加标的弹层 ---- */
.add-mask {
  position: fixed;
  inset: 0;
  background: rgba(20, 22, 30, 0.32);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 70;
}
.add-box {
  width: 380px;
  background: var(--card);
  border-radius: 8px;
  padding: 16px 18px;
  box-shadow: 0 12px 36px rgba(16, 24, 40, 0.2);
}
.add-box h3 { margin: 0 0 8px; font-size: 14px; }
.add-tip {
  font-size: 11px;
  line-height: 1.7;
  color: var(--fg-2);
  background: #faf7ff;
  border-radius: 5px;
  padding: 7px 9px;
  margin: 0 0 12px;
}
.add-box label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
  color: var(--fg-2);
  margin-bottom: 9px;
}
.add-box input {
  height: 30px;
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  padding: 0 8px;
}
.add-box input:focus { outline: none; border-color: var(--purple); }
.add-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
