<template>
  <section class="card">
    <div class="card-head">
      <h2>决策清单</h2>
      <span class="hint">{{ data.week || '—' }}</span>
      <div class="spacer"></div>
      <span class="tag">{{ data.decided || 0 }} / {{ data.total || 0 }} 已定</span>
    </div>

    <div class="card-body">
      <p class="lead">
        每周日晚拍板。点一下就是决定，没有提交按钮——关掉窗口也不会丢。
      </p>
      <p v-if="error" class="err">{{ error }}</p>

      <div v-if="!data.items || !data.items.length" class="empty">
        池子里还没有票。先去「股票池」加标的，或者点右上角跑一次更新。
      </div>

      <table v-else class="tbl decision-tbl">
        <thead>
          <tr>
            <th style="width: 88px">代码</th>
            <th>名称</th>
            <th>赛道</th>
            <th style="width: 88px">所在池</th>
            <th style="width: 300px">本周决定</th>
            <th>上次</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="it in data.items" :key="it.code" :class="{ done: it.weekDecision }">
            <td class="num">{{ it.code }}</td>
            <td>
              <a href="#" class="link" @click.prevent="open(it.code)">{{ it.name }}</a>
            </td>
            <td class="muted">{{ it.sector || '—' }}</td>
            <td><span class="pill" :class="poolClass(it.pool)">{{ poolLabel(it.pool) }}</span></td>
            <td>
              <button
                v-for="opt in options"
                :key="opt.value"
                class="pick"
                :class="[opt.value, { active: it.weekDecision === opt.value }]"
                :disabled="saving === it.code"
                @click="pick(it, opt.value)"
              >{{ opt.label }}</button>
            </td>
            <td class="muted small">
              <template v-if="it.lastDecisionLabel">
                {{ it.lastDecisionLabel }}<span class="dim"> · {{ it.lastDecisionWeek }}</span>
              </template>
              <template v-else>—</template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
// T5.1 决策清单：四选一，点完即存（无提交按钮），下周打开还在。
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, POOL_LABELS, poolClass } from '../api/client.js'

const router = useRouter()
const data = ref({ items: [], week: '', decided: 0, total: 0 })
const options = ref([
  { value: 'add', label: '加仓' }, { value: 'hold', label: '持有' },
  { value: 'trim', label: '减仓' }, { value: 'watch', label: '观察' },
])
const saving = ref('')
const error = ref('')

function poolLabel(p) {
  return POOL_LABELS[p] || p || '—'
}

function open(code) {
  router.push(`/stocks/${code}`)
}

async function load() {
  try {
    data.value = await api.decisions()
  } catch (e) {
    error.value = `读取失败：${e.message}`
  }
}

async function pick(it, value) {
  // 点一下就是决定。先在本地标记，请求失败再回滚——不让人等。
  const prev = it.weekDecision
  it.weekDecision = value
  it.weekDecisionLabel = options.value.find((o) => o.value === value)?.label || ''
  saving.value = it.code
  error.value = ''
  try {
    await api.setDecision({ code: it.code, week: data.value.week, decision: value })
    data.value = await api.decisions()
  } catch (e) {
    it.weekDecision = prev
    error.value = `保存失败：${e.message}`
  } finally {
    saving.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.lead { margin: 0 0 10px; font-size: 13px; color: var(--fg-2); }
.err { color: var(--red); font-size: 12px; margin: 0 0 8px; }
.dim { color: var(--fg-3); }
tr.done td { background: #fbfaff; }
.decision-tbl td { vertical-align: middle; }

.pick {
  height: 26px;
  padding: 0 10px;
  margin-right: 6px;
  border: 1px solid var(--line-strong);
  background: var(--card);
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  color: var(--fg-2);
}
.pick:hover { background: #f7f7f9; }
.pick.active { color: #fff; border-color: transparent; font-weight: 600; }
.pick.active.add { background: var(--red); }
.pick.active.hold { background: var(--purple); }
.pick.active.trim { background: var(--green); }
.pick.active.watch { background: var(--fg-3); }
.pick:disabled { opacity: 0.5; cursor: wait; }
</style>
