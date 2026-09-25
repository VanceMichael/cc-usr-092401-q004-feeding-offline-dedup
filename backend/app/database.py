from sqlalchemy import create_engine, inspect, text, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from pathlib import Path

SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./aquaculture.db"
)

if SQLALCHEMY_DATABASE_URL.startswith("sqlite:///"):
    db_path = SQLALCHEMY_DATABASE_URL.replace("sqlite:///", "")
    db_dir = Path(db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)

_is_sqlite = SQLALCHEMY_DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
if _is_sqlite:
    # 并发补传时写锁等待,而不是立刻 'database is locked'
    _connect_args["timeout"] = 30

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=_connect_args)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 投喂记录新增的列(旧库没有,需要幂等补齐,服务重启后幂等判定仍然成立)。
_FEEDING_NEW_COLUMNS = {
    "device_id": "VARCHAR(64)",
    "client_seq": "VARCHAR(64)",
    "content_hash": "VARCHAR(64)",
    "version": "INTEGER NOT NULL DEFAULT 1",
    "action": "VARCHAR(16) NOT NULL DEFAULT 'upsert'",
    "root_id": "INTEGER",
    "replaces_id": "INTEGER",
    "occurred_at": "DATETIME",
    "effective_at": "DATETIME",
    "received_at": "DATETIME",
    "review_status": "VARCHAR(16) NOT NULL DEFAULT 'approved'",
    "pending_reason": "VARCHAR(40)",
    "conflict_flag": "BOOLEAN NOT NULL DEFAULT 0",
}

_BATCH_NEW_COLUMNS = {
    "closed_at": "DATETIME",
}


def run_lightweight_migrations() -> None:
    """对 SQLite 旧库做最小的列级迁移;新库由 create_all 建表,无需处理。"""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    def _missing_columns(table: str, columns: dict):
        if table not in tables:
            return {}
        existing = {c["name"] for c in inspector.get_columns(table)}
        return {name: ddl for name, ddl in columns.items() if name not in existing}

    with engine.begin() as conn:
        missing_feeding = _missing_columns("feeding_records", _FEEDING_NEW_COLUMNS)
        for name, ddl in missing_feeding.items():
            conn.execute(text(f"ALTER TABLE feeding_records ADD COLUMN {name} {ddl}"))
        # 历史数据视为已审批、已接收的首版本
        if missing_feeding:
            conn.execute(text(
                "UPDATE feeding_records SET effective_at = COALESCE(effective_at, created_at), "
                "received_at = COALESCE(received_at, created_at), root_id = COALESCE(root_id, id) "
                "WHERE effective_at IS NULL OR received_at IS NULL OR root_id IS NULL"
            ))

        missing_batch = _missing_columns("batches", _BATCH_NEW_COLUMNS)
        for name, ddl in missing_batch.items():
            conn.execute(text(f"ALTER TABLE batches ADD COLUMN {name} {ddl}"))
        if missing_batch:
            # 已关闭/已收获批次视为在收获日关闭
            conn.execute(text(
                "UPDATE batches SET closed_at = COALESCE(closed_at, "
                "(SELECT created_at FROM batches b2 WHERE b2.id = batches.id)) "
                "WHERE status IN ('closed', 'harvested') AND closed_at IS NULL"
            ))

        # 旧表缺少版本化时代引入的索引:幂等唯一约束必须在数据库层兜底,
        # 这样即使应用层先查后插发生并发竞争,也只会落库一行。
        if "feeding_records" in tables:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_feeding_device_seq_version "
                "ON feeding_records (device_id, client_seq, version)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_feeding_batch_received_id "
                "ON feeding_records (batch_id, received_at, id)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_feeding_root_id "
                "ON feeding_records (root_id)"
            ))
        if "daily_reports" not in tables:
            # 新表由 create_all 创建;此处无需处理
            pass
