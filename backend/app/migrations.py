"""轻量启动迁移：在不引入 Alembic 的前提下为旧库补齐投喂版本化字段。

新表由 Base.metadata.create_all 自动建立；对已存在的 feeding_records，
逐列 ALTER TABLE ADD COLUMN 补齐，并回填合理默认值，使：
  * 旧记录成为各自版本链的根行（logical_id=id）；
  * 历史投喂默认已审核、当前生效；
  * 服务重启后幂等台账与唯一约束仍然存在。
"""
from sqlalchemy import text, inspect

FEEDING_COLUMN_DEFAULTS = {
    "device_id": "VARCHAR(64)",
    "device_seq": "INTEGER",
    "source": "VARCHAR(20) NOT NULL DEFAULT 'manual'",
    "occurred_at": "DATETIME",
    "content_hash": "VARCHAR(64)",
    "logical_id": "INTEGER",
    "version": "INTEGER NOT NULL DEFAULT 1",
    "supersedes_id": "INTEGER",
    "is_current": "BOOLEAN NOT NULL DEFAULT 1",
    "status": "VARCHAR(20) NOT NULL DEFAULT 'active'",
    "revision_reason": "VARCHAR(30)",
    "effective_at": "DATETIME",
    "review_status": "VARCHAR(20) NOT NULL DEFAULT 'approved'",
    "reviewed_at": "DATETIME",
    "is_late": "BOOLEAN NOT NULL DEFAULT 0",
    "updated_at": "DATETIME",
}

# 缺失时补建的索引
FEEDING_INDEXES = [
    ("ix_feeding_device_key", "CREATE INDEX ix_feeding_device_key ON feeding_records (device_id, device_seq)"),
    ("ix_feeding_lookup", "CREATE INDEX ix_feeding_lookup ON feeding_records (batch_id, feeding_date)"),
    ("ix_feeding_records_batch_id_idx", None),  # create_all 通常已建
]


def run_startup_migrations(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "feeding_records" not in tables:
        return  # 全新库，create_all 已建好一切

    existing_cols = {c["name"] for c in inspector.get_columns("feeding_records")}
    missing = {k: v for k, v in FEEDING_COLUMN_DEFAULTS.items() if k not in existing_cols}

    with engine.begin() as conn:
        for name, ddl in missing.items():
            conn.execute(text(f"ALTER TABLE feeding_records ADD COLUMN {name} {ddl}"))

        if missing:
            # 旧记录自成版本链根行，生效时点取创建时间
            conn.execute(text(
                "UPDATE feeding_records SET logical_id = id WHERE logical_id IS NULL"
            ))
            conn.execute(text(
                "UPDATE feeding_records SET effective_at = created_at "
                "WHERE effective_at IS NULL"
            ))

        existing_indexes = {ix["name"] for ix in inspector.get_indexes("feeding_records")}
        for name, ddl in FEEDING_INDEXES:
            if ddl and name not in existing_indexes:
                conn.execute(text(ddl))
