<template>
  <el-container class="shell">
    <el-aside width="210px" class="sidebar">
      <div class="brand">
        <div class="name">紫苏叶</div>
        <div class="sub">策略跟踪</div>
      </div>

      <el-menu
        :default-active="activeMenu"
        class="nav-menu"
        background-color="transparent"
        text-color="var(--text-secondary)"
        active-text-color="var(--primary)"
        @select="onMenuSelect"
      >
        <el-menu-item
          v-for="r in navItems"
          :key="r.path"
          :index="r.path"
        >
          <el-icon class="nav-ico"><component :is="iconOf(r.path)" /></el-icon>
          <template #title>{{ r.meta.title }}</template>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-foot">
        <el-tag :type="dbOk ? 'success' : 'danger'" size="small" effect="plain" round>
          {{ dbOk ? '数据库已连接' : '数据库未连接' }}
        </el-tag>
        <div class="ver">{{ version }}</div>
      </div>
    </el-aside>

    <el-container>
      <el-header class="topbar">
        <div class="search-wrap">
          <el-autocomplete
            v-model="keyword"
            :fetch-suggestions="querySearch"
            placeholder="搜索代码 / 名称 / 赛道"
            size="default"
            clearable
            style="width: 360px"
            @select="onSelectResult"
          >
            <template #default="{ item }">
              <div class="search-result-item">
                <span class="code">{{ item.code }}</span>
                <span class="nm">{{ item.name }}</span>
                <el-tag v-if="item.pool_label" size="small" :type="poolTagType(item.pool)" effect="plain">
                  {{ item.pool_label }}
                </el-tag>
              </div>
            </template>
          </el-autocomplete>
        </div>

        <div class="spacer"></div>

        <el-tag
          :type="freshness.stale ? 'warning' : 'info'"
          size="small"
          effect="plain"
          round
        >
          {{ freshnessText }}
        </el-tag>

        <el-button type="primary" :loading="updating" @click="onUpdate">
          {{ updating ? '更新中…' : '更新数据' }}
        </el-button>
      </el-header>

      <el-main class="content">
        <router-view />
      </el-main>
    </el-container>

    <UpdatePanel ref="updatePanel" @done="onUpdateDone" />
  </el-container>
</template>

<script setup>
import { onMounted, onUnmounted, ref, computed, markRaw } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { api, POOL_LABELS } from './api/client.js'
import { routes } from './router.js'
import UpdatePanel from './components/UpdatePanel.vue'
import {
  DataAnalysis,
  Grid,
  EditPen,
  Money,
  Star,
  Connection,
  Setting,
} from '@element-plus/icons-vue'

const router = useRouter()
const route = useRoute()

const navItems = routes.filter((r) => !r.meta.hidden)
const version = ref('v0.1.0')
const dbOk = ref(false)
const keyword = ref('')
const results = ref([])
const updating = ref(false)
const updatePanel = ref(null)
const freshness = ref({ latest_score_date: null, days_since: null, stale: false })

const iconMap = {
  '/': markRaw(DataAnalysis),
  '/pools': markRaw(Grid),
  '/decisions': markRaw(EditPen),
  '/portfolio': markRaw(Money),
  '/recommendations': markRaw(Star),
  '/sources': markRaw(Connection),
  '/settings': markRaw(Setting),
}
function iconOf(path) { return iconMap[path] || Grid }

const activeMenu = computed(() => {
  if (route.path.startsWith('/stocks')) return '/pools'
  if (route.path.startsWith('/pools')) return '/pools'
  return route.path
})

const freshnessText = computed(() => {
  const f = freshness.value
  if (!f.latest_score_date) return '还没有评分数据'
  if (f.days_since === null) return `最近评分 ${f.latest_score_date}`
  if (f.days_since === 0) return '最近评分 今天'
  return `最近评分 ${f.days_since} 天前`
})

function poolTagType(pool) {
  const m = {
    candidate: 'primary',
    observation: 'success',
    reserve: 'info',
    tuna: 'warning',
    removed: 'info',
  }
  return m[pool] || 'info'
}

function onMenuSelect(index) {
  if (index === route.path) return
  router.push(index)
}

let timer = null
function querySearch(q, cb) {
  clearTimeout(timer)
  const query = (q || '').trim()
  if (!query) {
    cb([])
    return
  }
  timer = setTimeout(async () => {
    try {
      const r = await api.search(query)
      results.value = r.items
      cb(r.items.map((it) => ({ ...it, value: it.name })))
    } catch (e) {
      results.value = []
      cb([])
    }
  }, 180)
}

function onSelectResult(item) {
  keyword.value = ''
  router.push(`/stocks/${item.code}`)
}

function onUpdate() {
  if (updating.value) return
  updating.value = true
  updatePanel.value?.start()
}

function onUpdateDone(st) {
  updating.value = false
  if (st && ['ok', 'partial'].includes(st.status)) {
    loadMeta()
  }
}

async function loadMeta() {
  try {
    const h = await api.health()
    dbOk.value = !!h.ok
    version.value = `v${h.version || '0.1.0'}`
    const d = await api.dashboard()
    freshness.value = d.freshness
  } catch (e) {
    dbOk.value = false
  }
}

onMounted(async () => {
  await loadMeta()
  notifyDesktopReady()
})

function waitForContent(minLen = 200, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const started = Date.now()
    const tick = () => {
      if ((document.body.innerText || '').length >= minLen) return resolve(true)
      if (Date.now() - started >= timeoutMs) return resolve(false)
      window.setTimeout(tick, 60)
    }
    window.setTimeout(tick, 0)
  })
}

async function notifyDesktopReady() {
  await waitForContent()

  const payload = () => ({
    title: document.title,
    bodyLen: (document.body.innerText || '').length,
    hasApp: !!document.querySelector('#app'),
    url: location.href,
  })

  let done = false
  const send = () => {
    if (done) return true
    done = true

    fetch('/api/meta/client-ready', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload()),
    }).catch(() => {
      done = false
    })

    const a = window.pywebview && window.pywebview.api
    if (a && typeof a.app_ready === 'function') {
      try {
        a.app_ready(payload())
      } catch (e) {
        /* 桥不通不影响 HTTP 自报 */
      }
    }
    return true
  }

  if (send()) return
  window.addEventListener('pywebviewready', send)
  let tries = 0
  const timer = window.setInterval(() => {
    tries += 1
    if (send() || tries >= 200) window.clearInterval(timer)
  }, 50)
}

onUnmounted(() => {})
</script>

<style scoped>
.shell {
  height: 100vh;
  width: 100%;
  background: var(--bg-page);
}

.sidebar {
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.brand {
  padding: 20px 18px 16px;
  border-bottom: 1px solid var(--border);
}
.brand .name {
  font-size: 20px;
  font-weight: 700;
  color: var(--primary);
  letter-spacing: 2px;
}
.brand .sub {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 2px;
  letter-spacing: 4px;
}

.nav-menu {
  flex: 1;
  border-right: none !important;
  margin-top: 8px;
}
.nav-menu :deep(.el-menu-item) {
  height: 42px;
  line-height: 42px;
  margin: 2px 8px;
  border-radius: 8px;
}
.nav-menu :deep(.el-menu-item.is-active) {
  background: var(--primary-light) !important;
}
.nav-ico {
  font-size: 18px;
}

.sidebar-foot {
  padding: 12px 14px 16px;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sidebar-foot .ver {
  font-size: 11px;
  color: var(--text-tertiary);
  text-align: center;
}

.topbar {
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 20px;
  gap: 14px;
  height: 58px !important;
}
.search-wrap { flex-shrink: 0; }
.search-result-item {
  display: flex;
  align-items: center;
  gap: 10px;
}
.search-result-item .code {
  font-family: 'SF Mono', Monaco, monospace;
  font-size: 12px;
  color: var(--text-tertiary);
  width: 56px;
}
.search-result-item .nm {
  flex: 1;
  font-size: 13px;
}
.spacer { flex: 1; }

.content {
  padding: 20px 24px !important;
  overflow-y: auto;
  background: var(--bg-page);
}
</style>
