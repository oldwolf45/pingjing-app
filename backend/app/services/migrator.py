"""迁移模块兼容层。

迁移引擎已移至 app.db.migrations.engine，此处保留旧导入路径以兼容现有代码。
新代码请直接 from app.db.migrations import run_migrations。

注意：本模块的 MIGRATIONS 变量是对 engine 模块 MIGRATIONS 的引用代理，
修改本模块的 MIGRATIONS 会同步修改 engine 里的那份（测试依赖这个行为）。
"""

import sys

from app.db.migrations import engine as _engine

# 直接暴露 engine 模块的所有公共符号
TARGET_VERSION = _engine.TARGET_VERSION
Migration = _engine.Migration
MigrationInterrupted = _engine.MigrationInterrupted
CRITICAL_TABLES = _engine.CRITICAL_TABLES
MIGRATION_STATE_KEY = _engine.MIGRATION_STATE_KEY
run_migrations = _engine.run_migrations
needs_migration = _engine.needs_migration
self_check = _engine.self_check
recover_from_interruption = _engine.recover_from_interruption
reset_state = _engine.reset_state


# 用模块代理实现 MIGRATIONS 的双向同步
class _MigratorModule:
    """代理模块：MIGRATIONS 读写都转发到 engine 模块。"""

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def __getattr__(self, name):
        if name == "MIGRATIONS":
            # 返回 tuple，保持向后兼容（旧代码用 tuple(m for m in MIGRATIONS if ...) 过滤）
            return tuple(_engine.MIGRATIONS)
        return getattr(self._wrapped, name)

    def __setattr__(self, name, value):
        if name == "_wrapped":
            super().__setattr__(name, value)
        elif name == "MIGRATIONS":
            # 统一存为 list，tuple 也接受（兼容旧测试代码）
            _engine.MIGRATIONS = list(value)
        else:
            setattr(self._wrapped, name, value)


# 替换 sys.modules 中的本模块为代理
sys.modules[__name__] = _MigratorModule(sys.modules[__name__])


__all__ = [
    "CRITICAL_TABLES",
    "MIGRATION_STATE_KEY",
    "MIGRATIONS",
    "TARGET_VERSION",
    "Migration",
    "MigrationInterrupted",
    "needs_migration",
    "recover_from_interruption",
    "reset_state",
    "run_migrations",
    "self_check",
]
