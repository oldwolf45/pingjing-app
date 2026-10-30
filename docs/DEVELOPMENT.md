# 开发指南

> 适用于想要在本地运行、调试、修改紫苏叶策略的开发者。

---

## 环境要求

| 工具 | 版本要求 | 用途 |
|------|---------|------|
| Python | ≥ 3.12 | 后端运行时 |
| Node.js | ≥ 18 | 前端构建 |
| npm | ≥ 9 | 前端包管理 |
| Git | 最新 | 版本管理 |
| Windows 10/11 | — | 桌面端运行（开发时 Linux/macOS 也能用后端模式） |
| Inno Setup | ≥ 6 | 打包安装包（可选） |

---

## 快速搭建

### 1. 克隆仓库

```bash
git clone <仓库地址>
cd zisuye-app
```

### 2. 后端环境

```bash
# 创建虚拟环境（推荐）
python -m venv .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 安装依赖（含开发依赖）
pip install -e ".[dev]"

# 或只装运行时依赖
pip install -r requirements.txt
```

### 3. 前端环境

```bash
cd frontend
npm install
```

### 4. 构建前端

开发模式（热更新）：

```bash
cd frontend
npm run dev
```

生产构建（后端桌面模式需要）：

```bash
cd frontend
npm run build
```

---

## 运行项目

### 桌面模式（完整功能）

```bash
# 确保前端已构建
cd frontend && npm run build && cd ..

# 启动桌面应用
cd backend
python -m app.main
```

启动后会弹出桌面窗口。控制台会显示后端端口号。

### 纯后端模式（浏览器访问）

如果只想调试 API 或在没有桌面环境的机器上运行：

```bash
cd backend
uvicorn app.main:create_app --host 127.0.0.1 --port 8000 --factory
```

然后浏览器打开 `http://127.0.0.1:8000`（需要前端已构建并能被后端读到）。

---

## 运行测试

### 后端测试

```bash
cd backend
pytest
```

带覆盖率：

```bash
pytest --cov=app --cov-report=term-missing
```

### 查看测试报告

覆盖率报告会输出到终端，也可以生成 HTML 版本：

```bash
pytest --cov=app --cov-report=html
# 打开 htmlcov/index.html
```

---

## 项目结构速查

```
backend/
├── app/
│   ├── core/          # 核心算法（scoring/barrier/pool 三大引擎）
│   ├── data/          # 数据库连接 + 仓储层 + 迁移
│   ├── services/      # 业务服务层
│   ├── api/           # FastAPI 路由
│   ├── utils/         # 工具函数（日志、配置、错误码）
│   └── main.py        # 入口
└── tests/             # 单元测试

frontend/
├── src/
│   ├── views/         # 页面级组件
│   ├── components/    # 可复用组件
│   ├── stores/        # Pinia 状态管理
│   ├── api/           # API 请求封装
│   └── main.js        # 入口
```

---

## 常见问题

### Q: 启动报错 "No module named 'app'"

检查你是否在 `backend/` 目录下运行，或者是否用了 `pip install -e .` 安装开发模式。

### Q: 前端页面 404

确认前端已经构建（`npm run build`），且后端能找到 `frontend/dist` 目录。

### Q: 数据库在哪里

- 开发模式：`backend/data_local/zisuye.db`
- 打包安装后：`%LocalAppData%\ZiSuYe\data\zisuye.db`

### Q: 怎么重置数据

删除数据库文件即可，下次启动会自动重建空库。如果有备份，从设置页恢复。

---

## 代码规范

### Python

- 使用 ruff 做 lint 和格式化
- 函数和变量用 snake_case，类用 PascalCase
- 公共函数必须有 docstring
- print 调试一律用 logging 替代

### 前端

- 使用 ESLint + Prettier
- 组件用 PascalCase，props 用 camelCase
- API 请求统一走 axios 封装
- 全局状态放 Pinia，不要靠 props 层层传递

---

## 调试技巧

### 查看日志

日志文件位置：
- 开发模式：`backend/data_local/logs/`
- 打包后：`%LocalAppData%\ZiSuYe\logs\`

### 开启 DEBUG 日志

设置环境变量：

```bash
set ZISUYE_LOG_LEVEL=DEBUG
python -m app.main
```

### 单步调试

VS Code 中，在 `backend/app/main.py` 打个断点，按 F5 启动调试。
