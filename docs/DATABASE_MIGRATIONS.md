# 数据库迁移指南

> 紫苏叶策略使用自有的轻量迁移框架，不需要 Alembic 等额外依赖。

---

## 设计原则

| 原则 | 说明 |
|------|------|
| **版本化** | 每个迁移一个版本号，顺序执行，可追踪 |
| **事务性** | 每个迁移在独立事务中执行，失败自动回滚 |
| **可回滚** | 迁移前自动备份，失败用备份恢复 |
| **断电保护** | 迁移中途崩溃，下次启动检测到并提示恢复 |
| **自检** | 迁移后检查关键表存在、行数不减少、外键完整 |

---

## 目录结构

```
backend/app/db/migrations/
├── __init__.py          # 对外导出
├── engine.py            # 迁移引擎核心
└── versions/            # 迁移脚本
    ├── 001_init.sql         # v1 初始建表（24 表 + 视图 + 触发器）
    ├── 002_index_and_sources.sql  # v2 索引 + 数据源种子
    ├── 003_quotes.sql         # v3 行情表
    └── 004_pool_last_judged.sql  # v4 pool_states.last_judged_at
```

---

## 当前版本

- **当前目标版本：v4**（`TARGET_VERSION = 4`）
- 版本记录表：`schema_migrations`

---

## 新增一个迁移

### 1. 新建 SQL 文件

在 `backend/app/db/migrations/versions/` 下新建：

```
005_example_change.sql
```

文件头部加注释说明：

```sql
-- 紫苏叶策略 APP · 数据库迁移 v005
-- 版本：5
-- 名称：005_example_change
-- 说明：一句话说明这个迁移做什么
-- 影响：哪些表/列会变，是否需要数据迁移
```

### 2. 在 engine.py 中注册

在 `MIGRATIONS` 列表末尾加一个：

```python
Migration(5, "005_example_change", _m005),
```

并定义对应的函数：

```python
def _m005(conn: sqlite3.Connection) -> None:
    """一句话说明。"""
    _apply_sql_file(conn, "005_example_change.sql")
```

如果是纯 SQL 迁移，直接调 `_apply_sql_file` 就行。
如果需要程序化逻辑（比如数据迁移、条件判断），写在函数里。

### 3. 更新 TARGET_VERSION

```python
TARGET_VERSION = 5  # 改成新版本号
```

### 4. 写测试

在 `tests/test_migration_engine.py` 或 `tests/test_migrator.py` 里加测试：

- 空库升级到最新版本
- 旧版本升级后数据不丢失
- 迁移失败自动回滚

### 5. 验证

```bash
pytest tests/test_migrator.py tests/test_migration_engine.py tests/test_upgrade_drill.py -v
```

---

## 运行时流程

```
程序启动
  │
  ├─ 检查 schema_version < TARGET_VERSION?
  │     │
  │     ├─ 否 → 正常启动
  │     │
  │     └─ 是 → 检查 migration_state
  │              │
  │              ├─ running → 上次中断，提示用户恢复/重试
  │              │
  │              └─ 其他 → 开始迁移
  │                       │
  │                       ├─ 1. 备份数据库（pre_migration）
  │                       ├─ 2. 标记 state = running
  │                       ├─ 3. 逐个执行迁移（每个独立事务）
  │                       ├─ 4. 自检（关键表 + 行数 + 外键）
  │                       ├─ 5. 标记 state = done
  │                       └─ 6. 返回报告
  │
  └─ 完成
```

---

## 常见问题

### Q: 迁移失败了怎么办？

程序会自动回滚到迁移前的备份，并弹出提示。用户可以选择：
- **恢复到升级前** — 用备份覆盖当前库
- **重试** — 再试一次（可能是临时问题）

如果是开发环境，直接删库重建最快。

### Q: 为什么不用 Alembic？

- 我们的迁移很简单（加列、加表、加索引），SQLite 不支持的 ALTER 操作极少
- Alembic 依赖 SQLAlchemy ORM，我们用的是原生 sqlite3
- 自有框架只有 ~200 行，出了问题一眼能看懂
- 断电保护、自检、自动回滚这些核心能力都有了

### Q: 迁移脚本写错了，已经在某些库上执行了怎么办？

**不要改已发布的迁移脚本！** 正确做法是：
1. 写一个新的迁移（v006）来修正 v005 的错误
2. 已升级的库执行 v006 修复
3. 没升级的库按顺序执行 v005 → v006，最终结果一样

只有迁移脚本还没发出去（只有你本地有）的时候，可以删掉重来。

### Q: 怎么查看数据库的当前版本？

```python
from app.db import session
print(session.get_schema_version(conn))
```

或在代码里：

```python
from app.db.migrations import list_migrations
for m in list_migrations(conn):
    print(f"{m['version']:3d} {m['name']:30s} {m['status']}")
```
