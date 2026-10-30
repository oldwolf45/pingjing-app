# 🌿 紫苏叶策略

> 一个帮「紫苏叶策略」使用者每周花 20 分钟做完决策的本地桌面工具。
>
> 不盯盘，不预测，不荐股。只做三件事：**记录、判定、留证据**。

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)]()
[![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)]()
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white)]()
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ✨ 功能特性

| 特性 | 说明 |
|------|------|
| 📊 **三池动态管理** | 候选 / 观察 / 储备 / 金枪鱼 / 已移除，五池自动流转 |
| 🧠 **6 维评分体系** | 壁垒 2.5 + 业绩增长 2.5 + 盈利能力 1.5 + 业绩加速度 0.5 + 热度估值 1.0 + 市值弹性 1.0 = 满分 9.0 |
| 🛡️ **壁垒 8 维评估** | 技术壁垒 / 客户认证 / 产能 / 上游集中度 / 替代成本 / 价格控制 / 空间 / 玩家数量 |
| 🐟 **金枪鱼出池机制** | 连续 2 周命中「变热」或「变大」即出池，带滞回设计防反复横跳 |
| 💰 **持仓与盈亏** | 移动加权平均成本，交易流水只追加不修改，仓位超 10% 自动预警 |
| 📈 **推荐追踪** | 三档分开统计命中率，样本不足 10 条不显示数字，绝不造假 |
| 🔒 **数据安全** | SQLite 单文件 + WAL + 每日备份 + 升级前备份 + 只追加表触发器保护 |
| 📦 **绿色安装** | 卸载不删数据，升级不掉数据，备份即复制即用 |

---

## 🏗️ 架构与技术栈

```
┌───────────────────────────────────────────────┐
│              桌面窗口（pywebview）              │  ← Windows 自带 WebView2，不背 Chromium
├───────────────────────────────────────────────┤
│  前端：Vue 3 + Vite + ECharts + Element Plus   │  ← 本地页面，与手机端可复用
├───────────────────────────────────────────────┤
│  后端：FastAPI + Uvicorn（127.0.0.1 随机端口）  │  ← 只监听本机，不暴露外网
├───────────────────────────────────────────────┤
│  数据库：SQLite + WAL + 只追加触发器            │  ← 单文件，备份即复制
└───────────────────────────────────────────────┘
```

**技术选型要点：**

- **pywebview**：比 Electron 轻 150MB，只用 Python 一套运行时
- **SQLite**：单文件数据库，配合 WAL 模式 + synchronous=FULL，断电也不丢数据
- **算法原样拷贝**：评分引擎、壁垒引擎、池子管理器从 Python 版原样迁移，用 28 只核心票金样本对拍验证一致性
- **只追加设计**：评分快照、池变动、推荐记录三张表用触发器锁死 UPDATE/DELETE，保证历史不可篡改

---

## 🚀 快速开始

### 环境要求

- Python 3.12+
- Node.js 18+（开发前端用）
- Windows 10/11（打包桌面端用，Linux/macOS 可开发后端）

### 本地开发

#### 1. 克隆项目

```bash
git clone <仓库地址>
cd zisuye-app
```

#### 2. 后端

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -e ".[dev]"
```

#### 3. 前端

```bash
cd frontend
npm install
npm run build    # 构建静态文件，后端会读取
# 或开发模式：npm run dev
```

#### 4. 运行

```bash
cd backend
python -m app.main
```

启动后会弹出桌面窗口。如果只想看 API 文档，浏览器打开 `http://127.0.0.1:<端口>/docs`（端口启动时在控制台输出）。

### 运行测试

```bash
cd backend
pytest
```

### 打包桌面安装包

```bash
# 先构建前端
cd frontend && npm run build && cd ..

# 用 PyInstaller 打包
pyinstaller zisuye.spec

# 用 Inno Setup 打安装包（需先装 Inno Setup）
iscc installer/zisuye.iss
```

安装包输出在 `installer/Output/` 目录。

---

## 📁 目录结构

```
zisuye-app/
├── backend/                    # Python 后端
│   ├── app/
│   │   ├── core/               # 核心算法引擎（冻结区，不改逻辑）
│   │   │   ├── scoring_engine.py     # 6 维评分
│   │   │   ├── barrier_engine.py     # 壁垒 8 维
│   │   │   ├── pool_manager.py       # 池子进出规则
│   │   │   └── ...
│   │   ├── data/               # 数据访问层
│   │   │   ├── session.py            # 数据库连接
│   │   │   ├── migrations/           # Alembic 迁移脚本
│   │   │   └── repositories/         # 仓储类
│   │   ├── services/           # 业务服务
│   │   ├── api/                # FastAPI 路由
│   │   ├── utils/              # 工具（日志、配置、错误码）
│   │   └── main.py             # 入口
│   ├── tests/                  # 单元测试
│   └── pyproject.toml
├── frontend/                   # Vue 3 前端
│   ├── src/
│   │   ├── views/              # 页面
│   │   ├── components/         # 组件
│   │   ├── stores/             # Pinia 状态
│   │   └── ...
│   └── package.json
├── installer/                  # Inno Setup 安装包配置
├── tools/                      # 辅助脚本（冒烟测试、截图等）
├── docs/                       # 开发文档
│   ├── DEVELOPMENT.md          # 开发指南
│   ├── DEPLOYMENT.md           # 打包发布
│   └── ARCHITECTURE.md         # 架构说明
├── zisuye.spec                 # PyInstaller 配置
├── CHANGELOG.md                # 更新日志
├── LICENSE                     # MIT 协议
└── README.md                   # 你在这里
```

---

## 🧪 核心设计原则

### 诚实展示四条铁律

这套系统最容易骗人的地方是「看起来是实时的」。四条硬规则：

1. **不假装实时** — 所有价格旁标「价格时间：yyyy-MM-dd HH:mm（点更新时拉取）」
2. **不假装完整** — 缺数据的维度显示「缺数据」灰色斜纹，不给默认分、不画 0
3. **不假装有趋势** — 快照少于 8 个日期，趋势区显示「还不能画趋势」，绝不连线
4. **不假装有结论** — 推荐样本不足 10 条，显示「样本不足」，不显示任何推算数字

### 池子进出三原则

1. **先判金枪鱼，再判分数** — 顺序反了出口就错
2. **兜底不能给 0** — 0 分不是「没数据」，是「判死刑」
3. **宁可空着不硬补** — 名额空着是诚实，降标准才是失控

---

## 📝 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

---

## 📄 License

MIT License — 随便用，不保证赚钱，亏了别找我。

---

## ⚠️ 免责声明

本工具仅供个人投资研究与记录使用，**不构成任何投资建议**。
股市有风险，入市需谨慎。所有评分与推荐仅基于历史数据，不代表未来表现。
