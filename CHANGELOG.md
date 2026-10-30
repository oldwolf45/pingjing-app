# 更新日志

所有重要的版本变更都记录在这个文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
并且本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

---

## [0.2.0] - 2026-10-30

> 工程化重构版本：6 阶段重构（R1-R5）完成，代码质量和工程规范全面提升。

### 新增

- **统一日志系统**：`app/utils/logger.py`，控制台 + 文件按天轮转，支持环境变量配置级别
- **统一配置管理**：`app/utils/config.py`，环境变量 + 默认值 + 类型校验
- **统一异常体系**：`app/exceptions.py`，AppError 基类 + 4 种标准异常
- **API 全局异常处理**：统一错误响应格式 + 请求日志中间件
- **健康检查端点**：`/api/health`、`/api/info`
- **版本号管理脚本**：`scripts/bump_version.py`，一处修改三处同步
- **CI/CD**：GitHub Actions 自动测试 + 前端构建 + 代码质量检查
- **数据库迁移指南**：`docs/DATABASE_MIGRATIONS.md`

### 改进

- **R1 仓库标准化**：根目录项目配置、MIT License、CHANGELOG、开发/部署文档、.gitignore 完善、测试数据目录整理、应用图标
- **R2 迁移框架**：迁移引擎移至 `app/db/migrations/`，SQL 脚本文件化，新增 11 个迁移测试
- **R3 代码质量**：utils 工具包（日志/配置/algo_logger），10 个配置测试
- **R4 API 规范化**：5 个 service 异常接入 AppError 体系，全局中间件
- **R5 前端升级**：引入 Element Plus 组件库 + Vue Router，全面提升 UI 品质

### 技术债务已解决

- [x] 仓库结构散乱，缺少标准配置文件
- [x] 数据库迁移内嵌在代码里，不透明
- [x] 缺少统一日志系统（77 处 print 待逐步替换）
- [x] 服务层异常不统一
- [x] 前端无组件库，纯手写 CSS

### 测试

- 核心测试：82/82 通过（迁移 31 + 配置 10 + API 9 + Schema 23 + 备份 9）
- 算法指纹校验：完整保留，算法逻辑一行未改

---

## [0.1.0] - 2026-10-18

### 新增

- 🎉 首个可运行版本：紫苏叶策略桌面 APP
- 📊 三池 + 金枪鱼池 + 已移除，共五池动态管理
- 🧠 6 维评分引擎（满分 9.0）
- 🛡️ 壁垒 8 维评估系统
- 🐟 金枪鱼出池机制（连续 2 周命中即出池）
- 📈 个股详情页：6 维雷达图 + 评分拆解 + 壁垒明细
- 💰 持仓与交易流水（移动加权平均成本）
- 📋 决策清单：每周加仓/持有/减仓/观察四选一
- 📡 多数据源适配 + 健康状态页
- 🔒 本地 SQLite + WAL + 每日备份 + 升级前备份
- 📤 导出 JSON / CSV
- 🖼️ PyInstaller + Inno Setup 打包成 Windows 安装包

### 技术栈

- 后端：Python 3.12 + FastAPI + Uvicorn + SQLite
- 前端：Vue 3 + Vite + ECharts
- 桌面壳：pywebview（WebView2）
- 打包：PyInstaller onedir + Inno Setup

---

## 版本说明

| 状态 | 含义 |
|------|------|
| ✅ | 已完成并通过测试 |
| 🚧 | 进行中 |
| 📋 | 计划中 |

### 版本号规则

`主版本.次版本.修订版本`

- **主版本**：不兼容的 API 改动
- **次版本**：向下兼容的功能性新增
- **修订版本**：向下兼容的问题修正
