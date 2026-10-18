// 后端 API 客户端。所有请求都走同源（后端挂载了前端静态资源）。
const BASE = ''

async function req(path) {
  const r = await fetch(BASE + path, { headers: { Accept: 'application/json' } })
  if (!r.ok) {
    let detail = r.statusText
    try {
      const j = await r.json()
      detail = j.detail || detail
    } catch (e) { /* 非 JSON 错误体，保留状态文本 */ }
    throw new Error(`${r.status} ${detail}`)
  }
  return r.json()
}

async function post(path, body) {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!r.ok) {
    let detail = r.statusText
    try {
      const j = await r.json()
      detail = j.detail || detail
    } catch (e) { /* 同上 */ }
    throw new Error(`${r.status} ${detail}`)
  }
  return r.json()
}

export const api = {
  health: () => req('/health'),
  paths: () => req('/api/meta/paths'),
  dashboard: () => req('/api/dashboard'),
  pools: (params = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') q.set(k, v)
    })
    const s = q.toString()
    return req('/api/pools' + (s ? `?${s}` : ''))
  },
  stock: (code) => req(`/api/stocks/${code}`),
  stockPlr: (code) => req(`/api/stocks/${code}/plr`),
  search: (q) => req(`/api/search?q=${encodeURIComponent(q)}`),
  startUpdate: (body) => post('/api/update/start', body || {}),
  updateStatus: () => req('/api/update/status'),
  cancelUpdate: () => post('/api/update/cancel'),

  // 决策清单（T5.1）：点一下即存，没有提交按钮
  decisions: (week) => req('/api/decisions' + (week ? `?week=${week}` : '')),
  decisionOptions: () => req('/api/decisions/options'),
  setDecision: (body) => post('/api/decisions', body),

  // 持仓与交易（T5.2~T5.4）
  portfolio: () => req('/api/portfolio'),
  addTrade: (body) => post('/api/portfolio/trades', body),
  voidTrade: (body) => post('/api/portfolio/void', body),
  setCash: (body) => post('/api/portfolio/cash', body),

  // 推荐追踪（T5.5 / T5.6）
  recommendations: () => req('/api/recommendations'),
  recStats: () => req('/api/recommendations/stats'),
  createRecommendation: (body) => post('/api/recommendations', body),

  // 数据源 / 设置 / 导出（T5.7 / T5.8 / T5.10）
  sources: () => req('/api/ops/sources'),
  testSource: (body) => post('/api/ops/sources/test', body),
  toggleSource: (body) => post('/api/ops/sources/toggle', body),
  settings: () => req('/api/ops/settings'),
  backup: () => post('/api/ops/backup'),
  restore: (body) => post('/api/ops/restore', body),
  openFolder: (which) => post(`/api/ops/open-folder?which=${which || 'data'}`),
  exportData: (fmt) => post(`/api/ops/export?fmt=${fmt || 'json'}`),

  // 添加标的（T5.9）：后端硬编码进储备池
  addStock: (body) => post('/api/ops/stocks', body),
}

// 六维的展示元数据（满分口径与后端一致，别在前端另起一套）
export const DIMENSIONS = [
  { key: 'track_barrier', label: '赛道壁垒', full: 2.5 },
  { key: 'earnings_growth', label: '业绩增长', full: 2.5 },
  { key: 'profitability', label: '盈利能力', full: 1.5 },
  { key: 'prosperity_check', label: '景气验证', full: 0.5 },
  { key: 'heat_position', label: '热度估值', full: 1.0 },
  { key: 'market_cap_elasticity', label: '市值弹性', full: 1.0 },
]

export const TOTAL_FULL = 9.0

export const POOL_LABELS = {
  candidate: '候选池',
  observation: '观察池',
  reserve: '储备池',
  tuna: '金枪鱼池',
  removed: '已移除',
}

export function poolClass(pool) {
  return `p-${pool || 'none'}`
}

export function fmt(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Number(v).toFixed(digits)
}
