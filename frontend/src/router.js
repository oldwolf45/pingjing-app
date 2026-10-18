import { createRouter, createWebHashHistory } from 'vue-router'
import Dashboard from './views/Dashboard.vue'
import StockPools from './views/StockPools.vue'
import StockDetail from './views/StockDetail.vue'
import Decisions from './views/Decisions.vue'
import Portfolio from './views/Portfolio.vue'
import Recommendations from './views/Recommendations.vue'
import Sources from './views/Sources.vue'
import Settings from './views/Settings.vue'

// 用 hash 路由：pywebview 里刷新/回退都不会打到后端 404
export const routes = [
  { path: '/', name: 'dashboard', component: Dashboard, meta: { title: '仪表盘', icon: '◎' } },
  { path: '/pools', name: 'pools', component: StockPools, meta: { title: '股票池', icon: '▤' } },
  { path: '/stocks/:code', name: 'stock', component: StockDetail, meta: { title: '个股详情', hidden: true } },
  { path: '/decisions', name: 'decisions', component: Decisions, meta: { title: '决策清单', icon: '✎' } },
  { path: '/portfolio', name: 'portfolio', component: Portfolio, meta: { title: '持仓', icon: '¥' } },
  { path: '/recommendations', name: 'recommendations', component: Recommendations, meta: { title: '推荐追踪', icon: '◈' } },
  { path: '/sources', name: 'sources', component: Sources, meta: { title: '数据源', icon: '⇄' } },
  { path: '/settings', name: 'settings', component: Settings, meta: { title: '设置', icon: '⚙' } },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
