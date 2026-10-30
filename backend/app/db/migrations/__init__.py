"""数据库迁移包。

导出：
- run_migrations: 执行迁移
- needs_migration: 检查是否需要迁移
- MigrationInterrupted: 迁移中断异常
- list_migrations: 列出所有迁移状态
"""

from app.db.migrations.engine import (
    CRITICAL_TABLES,
    TARGET_VERSION,
    MigrationInterrupted,
    list_migrations,
    needs_migration,
    recover_from_interruption,
    reset_state,
    run_migrations,
    self_check,
)

__all__ = [
    "CRITICAL_TABLES",
    "TARGET_VERSION",
    "MigrationInterrupted",
    "list_migrations",
    "needs_migration",
    "recover_from_interruption",
    "reset_state",
    "run_migrations",
    "self_check",
]
