"""投喂记录的幂等接收、版本链与生效时点汇总逻辑。

关键约定
--------
* 幂等键 = (device_id, client_seq, version)。现场设备只保证「设备号 + 本地流水号」
  组合唯一,重发同一事件时原样返回已落库的记录,绝不二次累计。
* 同一幂等键但业务内容指纹不同 = 冲突,拒绝覆盖并登记 FeedingConflict。
* 更正 / 撤销不覆盖旧行,而是追加新版本(action=upsert/revoke),并指定 effective_at;
  汇总在某个 as_of 时点只取每个逻辑记录(root_id)在该时点已生效的最新版本。
* 批次关闭后到达、或超出补传窗口的记录进入 review_status=pending(待审),不计汇总。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, date
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import FeedingRecord, Batch, FeedingConflict

# 补传窗口:业务日期超过该天数的补传视为迟报,需要人工审核(跨午夜补传不受影响)
LATE_WINDOW_DAYS = 3

# 参与内容指纹的业务字段(幂等键、版本元数据不参与)
_CONTENT_FIELDS = (
    "batch_id", "feeding_date", "feed_type", "feed_quantity",
    "feeding_time", "weather", "water_temperature", "notes",
    "action",
)


def compute_content_hash(payload: dict) -> str:
    """对业务内容计算稳定指纹,用于区分「内容相同的重复」与「同键内容冲突」。"""
    canonical = {}
    for field in _CONTENT_FIELDS:
        value = payload.get(field)
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        canonical[field] = value
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_dt(value) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    return None


class IngestResult:
    def __init__(self, record: FeedingRecord, outcome: str, conflict: FeedingConflict = None):
        self.record = record
        # created=新建 / duplicate=内容相同的重复同步 / conflict=同键内容冲突
        # revised=新版本更正 / revoked=新版本撤销 / pending=待审
        self.outcome = outcome
        self.conflict = conflict


def _is_batch_closed(batch: Batch, now: datetime) -> bool:
    if batch.status in ("closed", "harvested"):
        return True
    if batch.closed_at is not None:
        return True
    if batch.actual_harvest_date is not None:
        # 没有显式关闭时间时,收获日次日视为关闭
        return now.date() > batch.actual_harvest_date
    return False


def _classify_arrival(batch: Batch, feeding_date: date, now: datetime) -> tuple:
    """返回 (review_status, pending_reason)。"""
    if _is_batch_closed(batch, now):
        return "pending", "late_closed"
    if (now.date() - feeding_date).days > LATE_WINDOW_DAYS:
        return "pending", "late_window"
    return "approved", None


def _record_conflict(db: Session, existing: FeedingRecord, payload: dict,
                     content_hash: str) -> IngestResult:
    """同键内容冲突:保留原记录,登记冲突。"""
    conflict = FeedingConflict(
        device_id=existing.device_id,
        client_seq=existing.client_seq,
        version=existing.version,
        batch_id=existing.batch_id,
        existing_record_id=existing.id,
        existing_hash=existing.content_hash,
        incoming_hash=content_hash,
        incoming_payload=json.dumps(_jsonable(payload), ensure_ascii=False),
    )
    existing.conflict_flag = True
    db.add(conflict)
    db.commit()
    db.refresh(conflict)
    return IngestResult(existing, "conflict", conflict)


def _commit_new(db: Session, record: FeedingRecord) -> FeedingRecord:
    """提交新版本行;并发下唯一约束兜底,冲突时抛出 IntegrityError 供调用方重读。"""
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    db.refresh(record)
    return record


def ingest_feeding(
    db: Session,
    payload: dict,
    *,
    now: Optional[datetime] = None,
) -> IngestResult:
    """接收一条投喂同步。调用方负责校验批次存在。"""
    now = now or datetime.utcnow()

    device_id = payload.get("device_id")
    client_seq = payload.get("client_seq")
    client_version = payload.get("version")  # 设备自报版本(可选)
    action = payload.get("action") or "upsert"
    if action not in ("upsert", "revoke"):
        raise ValueError("action 只支持 upsert / revoke")

    occurred_at = _parse_dt(payload.get("occurred_at"))
    effective_at = _parse_dt(payload.get("effective_at")) or now

    batch = db.query(Batch).filter(Batch.id == payload["batch_id"]).first()
    feeding_date = payload["feeding_date"]
    if isinstance(feeding_date, str):
        feeding_date = date.fromisoformat(feeding_date)

    content_hash = compute_content_hash({**payload, "action": action, "feeding_date": feeding_date})
    review_status, pending_reason = _classify_arrival(batch, feeding_date, now)

    # ---------- 显式针对某逻辑记录的更正/撤销(含冲突人工采用)优先 ----------
    target_root_id = payload.get("root_id")
    if target_root_id is not None and action in ("upsert", "revoke"):
        root = db.query(FeedingRecord).filter(FeedingRecord.id == target_root_id).first()
        if root is None:
            raise ValueError("root_id 指向的记录不存在")
        family = db.query(FeedingRecord).filter(
            or_(FeedingRecord.root_id == target_root_id, FeedingRecord.id == target_root_id)
        ).order_by(FeedingRecord.version.desc()).all()
        latest = family[0]
        if latest.content_hash == content_hash and latest.action == action:
            return IngestResult(latest, "duplicate")
        new_version = max(r.version for r in family) + 1
        return _persist_new_version(
            db, payload, action=action, feeding_date=feeding_date,
            device_id=device_id or root.device_id,
            client_seq=(str(client_seq) if client_seq is not None else root.client_seq),
            version=new_version, root_id=target_root_id, replaces_id=latest.id,
            content_hash=content_hash, occurred_at=occurred_at,
            effective_at=effective_at, now=now,
            review_status=review_status, pending_reason=pending_reason,
        )

    # ---------- 幂等:设备补传 ----------
    if device_id and client_seq is not None:
        key_filter = (
            FeedingRecord.device_id == device_id,
            FeedingRecord.client_seq == str(client_seq),
        )
        family = db.query(FeedingRecord).filter(*key_filter)\
            .order_by(FeedingRecord.version.asc()).all()

        if client_version is not None:
            same_version = next((r for r in family if r.version == int(client_version)), None)
        else:
            # 未带版本号:该键首版本即代表同一业务事件
            same_version = family[0] if family else None

        if same_version is not None:
            if same_version.content_hash == content_hash:
                return IngestResult(same_version, "duplicate")
            return _record_conflict(db, same_version, payload, content_hash)

        if family:
            # 更高版本号 = 更正/撤销新版本
            latest = family[-1]
            if latest.content_hash == content_hash and latest.action == action:
                # 与最新版本等价,视为重复重发
                return IngestResult(latest, "duplicate")
            max_version = max(r.version for r in family)
            declared = int(client_version) if client_version is not None else None
            new_version = declared if declared and declared > max_version else max_version + 1
            root_id = latest.root_id or latest.id
            return _persist_new_version(
                db, payload, action=action, feeding_date=feeding_date,
                device_id=device_id, client_seq=str(client_seq),
                version=new_version, root_id=root_id, replaces_id=latest.id,
                content_hash=content_hash, occurred_at=occurred_at,
                effective_at=effective_at, now=now,
                review_status=review_status, pending_reason=pending_reason,
            )

    # ---------- 全新事件 ----------
    record = FeedingRecord(
        batch_id=payload["batch_id"],
        feeding_date=feeding_date,
        feed_type=payload["feed_type"],
        feed_quantity=payload["feed_quantity"],
        feeding_time=payload.get("feeding_time"),
        weather=payload.get("weather"),
        water_temperature=payload.get("water_temperature"),
        notes=payload.get("notes"),
        device_id=device_id,
        client_seq=str(client_seq) if client_seq is not None else None,
        content_hash=content_hash,
        version=1,
        action=action,
        root_id=None,  # 插入后回填为自身 id
        replaces_id=None,
        occurred_at=occurred_at,
        effective_at=effective_at,
        received_at=now,
        review_status=review_status,
        pending_reason=pending_reason,
    )
    try:
        db.add(record)
        db.flush()           # 取到自增 id 但仍在同一事务内
        record.root_id = record.id
        db.commit()
    except IntegrityError:
        # 并发补传:另一请求已先落库,重新判定重复/冲突
        db.rollback()
        return _reconcile_after_integrity_error(
            db, payload, action=action, feeding_date=feeding_date,
            device_id=device_id, client_seq=str(client_seq),
            client_version=client_version, content_hash=content_hash,
        )
    db.refresh(record)
    outcome = "pending" if review_status == "pending" else "created"
    return IngestResult(record, outcome)


def _reconcile_after_integrity_error(db, payload, *, action, feeding_date,
                                     device_id, client_seq, client_version,
                                     content_hash) -> IngestResult:
    """并发插入撞唯一约束后,按已落库记录重新给出 重复/冲突 结论。"""
    q = db.query(FeedingRecord).filter(
        FeedingRecord.device_id == device_id,
        FeedingRecord.client_seq == client_seq,
    )
    if client_version is not None:
        existing = q.filter(FeedingRecord.version == int(client_version)).first()
    else:
        existing = q.order_by(FeedingRecord.version.asc()).first()
    if existing is None:
        # 理论上不会发生(约束已命中),退回到该键的任意版本
        existing = q.order_by(FeedingRecord.version.asc()).first()
    if existing is None:
        raise RuntimeError("唯一约束冲突但无法定位已存在记录")
    if existing.content_hash == content_hash:
        return IngestResult(existing, "duplicate")
    return _record_conflict(db, existing, payload, content_hash)


def _persist_new_version(
    db: Session, payload: dict, *, action: str, feeding_date: date,
    device_id, client_seq, version: int, root_id: int, replaces_id: int,
    content_hash: str, occurred_at, effective_at, now: datetime,
    review_status: str, pending_reason,
) -> IngestResult:
    record = FeedingRecord(
        batch_id=payload["batch_id"],
        feeding_date=feeding_date,
        feed_type=payload["feed_type"],
        feed_quantity=payload["feed_quantity"],
        feeding_time=payload.get("feeding_time"),
        weather=payload.get("weather"),
        water_temperature=payload.get("water_temperature"),
        notes=payload.get("notes"),
        device_id=device_id,
        client_seq=client_seq,
        content_hash=content_hash,
        version=version,
        action=action,
        root_id=root_id,
        replaces_id=replaces_id,
        occurred_at=occurred_at,
        effective_at=effective_at,
        received_at=now,
        review_status=review_status,
        pending_reason=pending_reason,
    )
    try:
        _commit_new(db, record)
    except IntegrityError:
        return _reconcile_after_integrity_error(
            db, payload, action=action, feeding_date=feeding_date,
            device_id=device_id, client_seq=client_seq,
            client_version=version, content_hash=content_hash,
        )
    if review_status == "pending":
        outcome = "pending"
    elif action == "revoke":
        outcome = "revoked"
    else:
        outcome = "revised"
    return IngestResult(record, outcome)


def _jsonable(payload: dict) -> dict:
    out = {}
    for k, v in payload.items():
        if isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# 生效版本选择
# ---------------------------------------------------------------------------

def effective_records(
    db: Session,
    batch_id: int,
    *,
    business_date: Optional[date] = None,
    as_of: Optional[datetime] = None,
    include_pending: bool = False,
) -> list:
    """返回某批次(可选某业务日期)在 as_of 时点「生效中」的版本行。

    规则:
    * 只取 approved 且 effective_at <= as_of 的版本(pending 不计入,除非显式包含);
    * 同一逻辑记录(root_id)取版本号最大的一条;
    * action=revoke 的最新版本表示该记录在该时点已撤销 → 不返回;
    * 根行(root_id 为空,历史数据)按自身参与选择。
    """
    as_of = as_of or datetime.utcnow()
    q = db.query(FeedingRecord).filter(FeedingRecord.batch_id == batch_id)
    if business_date is not None:
        q = q.filter(FeedingRecord.feeding_date == business_date)
    rows = q.all()

    candidates = [
        r for r in rows
        if r.effective_at is not None and r.effective_at <= as_of
        and (include_pending or r.review_status == "approved")
    ]

    latest_by_root: dict = {}
    for r in candidates:
        key = r.root_id or r.id
        incumbent = latest_by_root.get(key)
        if incumbent is None or r.version > incumbent.version or \
           (r.version == incumbent.version and r.id > incumbent.id):
            latest_by_root[key] = r

    return [r for r in latest_by_root.values() if r.action != "revoke"]
