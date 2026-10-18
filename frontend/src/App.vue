<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="name">紫苏叶</div>
        <div class="sub">策略跟踪</div>
      </div>
      <nav class="nav">
        <button
          v-for="r in navItems"
          :key="r.path"
          class="nav-item"
          :class="{ active: isActive(r) }"
          @click="go(r.path)"
        >
          <span class="ico">{{ r.meta.icon }}</span>
          <span>{{ r.meta.title }}</span>
        </button>
      </nav>
      <div class="sidebar-foot">
        <div><span class="dot" :class="dbOk ? 'ok' : 'bad'"></span>{{ dbOk ? '数据库已连接' : '数据库未连接' }}</div>
        <div>{{ version }}</div>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="search-box">
          <span class="mag">⌕</span>
          <input
            v-model="keyword"
            placeholder="搜索代码 / 名称 / 赛道"
            @input="onSearch"
            @focus="onSearch"
            @keydown.enter="jumpFirst"
            @keydown.esc="closeSearch"
          />
          <div v-if="showPop" class="search-pop">
            <button v-for="it in results" :key="it.code" @click="openStock(it.code)">
              <span class="code">{{ it.code }}</span>
              <span>{{ it.name }}</span>
              <span v-if="it.pool_label" class="pill" :class="poolClass(it.pool)">{{ it.pool_label }}</span>
            </button>
            <div v-if="!results.length" class="empty">没有匹配的票</div>
          </div>
        </div>

        <div class="spacer"></div>

        <div class="freshness">
          <span class="tag" :class="{ stale: freshness.stale }">{{ freshnessText }}</span>
        </div>

        <button class="btn primary" :disabled="updating" @click="onUpdate">
          {{ updating ? '更新中…' : '更新数据' }}
        </button>
      </header>

      <main class="content">
        <router-view />
      </main>
    </div>

    <UpdatePanel ref="updatePanel" @done="onUpdateDone" />
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { api, POOL_LABELS, poolClass } from './api/client.js'
import { routes } from './router.js'
import UpdatePanel from './components/UpdatePanel.vue'

const router = useRouter()
const route = useRoute()

const navItems = routes.filter((r) => !r.meta.hidden)
const version = ref('v0.1.0')
const dbOk = ref(false)
const keyword = ref('')
const results = ref([])
const showPop = ref(false)
const updating = ref(false)
const updatePanel = ref(null)
const freshness = ref({ latest_score_date: null, days_since: null, stale: false })

const freshnessText = computed(() => {
  const f = freshness.value
  if (!f.latest_score_date) return '还没有评分数据'
  if (f.days_since === null) return `最近评分 ${f.latest_score_date}`
  if (f.days_since === 0) return `最近评分 今天`
  return `最近评分 ${f.days_since} 天前`
})

function isActive(r) {
  if (r.path === '/') return route.path === '/'
  if (r.name === 'pools') return route.path.startsWith('/pools') || route.path.startsWith('/stocks')
  return route.path.startsWith(r.path)
}

function go(path) {
  router.push(path)
}

let timer = null
async function onSearch() {
  clearTimeout(timer)
  const q = keyword.value.trim()
  if (!q) {
    showPop.value = false
    results.value = []
    return
  }
  timer = setTimeout(async () => {
    try {
      const r = await api.search(q)
      results.value = r.items
      showPop.value = true
    } catch (e) {
      results.value = []
    }
  }, 180)
}

function jumpFirst() {
  if (results.value.length) openStock(results.value[0].code)
}

function openStock(code) {
  showPop.value = false
  keyword.value = ''
  router.push(`/stocks/${code}`)
}

function closeSearch() {
  showPop.value = false
}

function onUpdate() {
  if (updating.value) return
  updating.value = true
  // 真正的 7 阶段跑在后端线程里，进度面板自己轮询 /api/update/status
  updatePanel.value?.start()
}

function onUpdateDone(st) {
  updating.value = false
  if (st && ['ok', 'partial'].includes(st.status)) {
    loadMeta()           // 数据变新了，顶栏的「最近评分」跟着更新
  }
}

function onDocClick(e) {
  if (!e.target.closest('.search-box')) showPop.value = false
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
  document.addEventListener('click', onDocClick)
  await loadMeta()        // 先把顶栏数据拉下来，骨架才有内容
  notifyDesktopReady()    // 再自报就绪
})

// 等页面「真有内容」再往下走。
//
// 为什么不能在 onMounted 里直接报：那一刻路由组件还没挂载完，
// loadMeta 的请求也还没回来，body.innerText 只有导航骨架，
// 外壳和冒烟脚本拿到一个很小的 bodyLen，会误判成白屏。
// 客户端渲染没有 load 事件可用，就只能轮询到有内容为止；
// 到点了还没内容也照报，但把真实的 bodyLen 带出去，让检查如实失败。
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

// 前端就绪自报。
//
// 原来走 pywebview 的 JS 桥（window.pywebview.api.app_ready），实测不行：
// 6.2.1 的 edgechromium 后端注入桥脚本本身就会卡住，window.pywebview 根本不存在。
// 改成走 HTTP 自报，桌面外壳和冒烟脚本都从 GET /api/meta/ready 读取状态。
// 桥那条路留着当兼容，桥在就顺手也报一下。
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
      done = false // 后端还没起来就下次再试
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

onUnmounted(() => document.removeEventListener('click', onDocClick))
</script>
