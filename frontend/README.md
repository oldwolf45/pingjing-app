# 紫苏叶策略小程序 · 前端

基于 **Vue 3 + Vite + Element Plus** 的桌面端前端。

## 技术栈

| 领域 | 选型 |
|------|------|
| 框架 | Vue 3.5 (Composition API, `<script setup>`) |
| 构建 | Vite 6 |
| UI 组件库 | Element Plus 2.x（按需自动导入） |
| 路由 | Vue Router 4（hash 模式，适配 pywebview） |
| 图表 | ECharts 5（雷达图等） |
| HTTP | 原生 `fetch` + 统一封装 |

## 目录结构

```
frontend/
├── src/
│   ├── api/             # API 客户端（统一封装 fetch）
│   │   └── client.js    # 所有后端接口调用集中在此
│   ├── components/      # 可复用组件
│   │   ├── PoolCard.vue
│   │   ├── Radar6.vue   # 六维雷达图（ECharts）
│   │   ├── ScoreBar.vue
│   │   └── UpdatePanel.vue
│   ├── views/           # 页面级组件（路由级）
│   │   ├── Dashboard.vue        # 仪表盘
│   │   ├── StockPools.vue       # 股票池（三池 + 金枪鱼 + 已移除）
│   │   ├── StockDetail.vue      # 个股详情（概览/壁垒/历史/说明 Tab）
│   │   ├── Decisions.vue        # 决策清单
│   │   ├── Portfolio.vue        # 持仓与交易流水
│   │   ├── Recommendations.vue  # 推荐追踪
│   │   ├── Sources.vue          # 数据源健康
│   │   └── Settings.vue         # 设置与备份
│   ├── App.vue          # 根组件（侧边栏 + 顶栏 + 内容区）
│   ├── main.js          # 入口
│   ├── router.js        # 路由配置（hash 模式）
│   └── styles.css       # 全局样式 + Element Plus 主题变量覆盖
├── index.html
├── vite.config.js       # Vite 配置（含 Element Plus 自动导入）
├── package.json
└── dist/                # 构建产物（后端挂载用）
```

## 开发

```bash
# 安装依赖
npm install

# 开发模式（带热更新，后端需在 8000 端口运行）
npm run dev

# 生产构建（输出到 dist/，后端会挂载这些静态文件）
npm run build
```

开发模式下，Vite dev server 会把 `/api` 和 `/health` 代理到 `http://127.0.0.1:8000`，
所以需要先启动后端。

## Element Plus 使用说明

### 按需自动导入
通过 `unplugin-auto-import` + `unplugin-vue-components` 实现：
- 组件（`ElButton`、`ElTable` 等）**无需手动 import**，直接在模板里用
- 自动导入也覆盖了 Vue / Vue Router 的 API（`ref`、`computed`、`onMounted` 等）
- 图标需要手动 import（从 `@element-plus/icons-vue`）

### 主题定制
主题色在 `src/styles.css` 中通过 CSS 变量覆盖，主色为紫苏紫 `#5b3a8f`：

```css
:root {
  --el-color-primary: #5b3a8f;
  --el-border-radius-base: 8px;
  --el-font-size-base: 13px;
  /* ... */
}
```

### 常用组件对照

| 原手写 | Element Plus |
|--------|-------------|
| `.card` + `.card-head` + `.card-body` | `<el-card>` + `#header` 插槽 |
| `table.tbl` | `<el-table>` + `<el-table-column>` |
| `.tabs` / `.tab` | `<el-tabs>` + `<el-tab-pane>` |
| `.btn` / `.btn.primary` | `<el-button>` + `type` |
| `.pill` | `<el-tag>` + effect 类型 |
| `.loading` | `v-loading` 指令 / `<el-skeleton>` |
| `.empty` | `<el-empty>` |
| `.err` | `<el-alert type="error">` |
| `window.confirm` | `ElMessageBox.confirm` |
| 内联提示 | `ElMessage` |
| 弹窗 | `<el-dialog>` |
| 表单 | `<el-form>` + `<el-form-item>` |
| 描述列表 | `<el-descriptions>` + `<el-descriptions-item>` |
| 进度条 | `<el-progress>` |

## API 约定

所有 API 调用集中在 `src/api/client.js`，通过 `api.xxx()` 调用。
后端基础路径为空（同源部署），开发模式走 Vite proxy。

```js
import { api } from './api/client.js'

const data = await api.dashboard()
const pool = await api.pools({ pool: 'candidate', sort: 'score' })
```

## 与后端的集成

前端构建产物在 `dist/` 目录，由 FastAPI 以静态资源方式挂载（`/` 路径）。
pywebview 桌面壳直接加载后端地址 `http://127.0.0.1:{port}/`，不走 file://。

### 桌面端就绪信号
前端加载完成后，会通过 `POST /api/meta/client-ready` 向后端自报就绪，
桌面壳据此判断白屏/启动成功。

## 迁移进度（R5 阶段）

- ✅ 布局：侧边栏 + 顶栏 + 内容区（Element Plus Container + Menu）
- ✅ 仪表盘：ElCard + ElRow/ElCol + ElTable + ElProgress + ElStatistic
- ✅ 股票池：ElTabs + ElTable + ElDialog + ElForm
- ✅ 个股详情：ElTabs + ElDescriptions + ElProgress
- ✅ 决策清单：ElTable + ElRadioGroup
- ✅ 持仓：ElCard + ElStatistic + ElTable + ElForm
- ✅ 推荐追踪：ElCard + ElTable (expand)
- ✅ 数据源：ElTable + ElTag
- ✅ 设置：ElDescriptions + ElTable
- ✅ 统一加载/空状态/错误提示
- ✅ 全局主题色覆盖（紫苏紫）
- ⏳ Pinia 状态管理（当前路由/API 已够用，后续再抽）
- ⏳ 页面级更多细节打磨（次要迭代）
