"""投喂同步/版本/分页/日报 的核心业务逻辑。

设计要点：
* (device_id, device_seq) 幂等台账 + 内容指纹：重复同步返回原记录，
  同键内容不同登记冲突，二者结果可区分；
* 更正/撤销追加新版本行，按版本 effective_at 选择“当时生效版本”，
  不需要后台任务切换；
* 待审核（迟报/关闭批次）版本不参与生效选择，批准后才进入汇总；
* 游标分页按到达序 (created_at, id) 做 keyset；写操作经进程内写锁串行化，
  使 id 提交顺序与到达顺序一致，并发补传不重不漏；
* 已签署日报保存完整快照，重放只依赖快照本身。
"""
from __future__ import annotations

import base64
import json
import threading
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import asc, or_, and_
from sqlalchemy.exc import IntegrityError

from ..models import (
    Batch, FeedingRecord, FeedingSyncKey, FeedingConflict, DailyFeedingReport,
    FEEDING_STATUS_ACTIVE, FEEDING_STATUS_REVOKED,
    REVIEW_APPROVED, REVIEW_PENDING, REVIEW_REJECTED,
    SOURCE_DEVICE, SOURCE_MANUAL, CLOSED_BATCH_STATUSES,
)
from ..schemas import FEEDING_CONTENT_FIELDS


# 单进程内串行化所有投喂写事务（默认单 uvicorn worker 部署）。
# 数据库唯一约束仍是跨进程的最后防线。
_writer_lock = threading.RLock()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.utcnow()


def _norm(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float):
        # 避免 10.0 / 10.000 之类的表示差异
        return round(value, 6)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):  # date
        return value.isoformat()
    return value


def content_hash_from_payload(data: dict) -> str:
    canonical = json.dumps(
        {k: _norm(data.get(k)) for k in FEEDING_CONTENT_FIELDS},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    import hashlib
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_hash_of_record(record: FeedingRecord) -> str:
    return content_hash_from_payload({
        "batch_id": record.batch_id,
        "feeding_date": record.feeding_date,
        "feed_type": record.feed_type,
        "feed_quantity": record.feed_quantity,
        "feeding_time": record.feeding_time,
        "weather": record.weather,
        "water_temperature": record.water_temperature,
        "notes": record.notes,
    })


def encode_cursor(last_created_at: datetime, last_id: int) -> str:
    raw = json.dumps({"ts": last_created_at.isoformat(), "id": last_id})
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        return datetime.fromisoformat(raw["ts"]), int(raw["id"])
    except Exception as exc:  # noqa: BLE001
        raise ValueError("无效的分页游标") from exc


def is_batch_closed(batch: Batch) -> bool:
    return batch.status in CLOSED_BATCH_STATUSES


# --------------------------------------------------------------------------- #
# 接收：幂等创建 / 冲突 / 更正 / 撤销
# --------------------------------------------------------------------------- #

def _payload_dict(record) -> dict:
    return {k: getattr(record, k) for k in FEEDING_CONTENT_FIELDS}


def _make_version_row(db, *, latest: FeedingRecord, updates: dict, reason: str,
                      effective_at: datetime, review_status: str, is_late: bool,
                      status: str) -> FeedingRecord:
    """在版本链上追加一个新版本。"""
    new = FeedingRecord(
        batch_id=updates.get("batch_id", latest.batch_id),
        feeding_date=updates.get("feeding_date", latest.feeding_date),
        feed_type=updates.get("feed_type", latest.feed_type),
        feed_quantity=updates.get("feed_quantity", latest.feed_quantity),
        feeding_time=updates.get("feeding_time", latest.feeding_time) if "feeding_time" in updates else latest.feeding_time,
        weather=updates.get("weather", latest.weather) if "weather" in updates else latest.weather,
        water_temperature=updates.get("water_temperature", latest.water_temperature) if "water_temperature" in updates else latest.water_temperature,
        notes=updates.get("notes", latest.notes) if "notes" in updates else latest.notes,
        device_id=latest.device_id,
        device_seq=latest.device_seq,
        source=latest.source,
        occurred_at=latest.occurred_at,
        content_hash=content_hash_from_payload({
            "batch_id": updates.get("batch_id", latest.batch_id),
            "feeding_date": updates.get("feeding_date", latest.feeding_date),
            "feed_type": updates.get("feed_type", latest.feed_type),
            "feed_quantity": updates.get("feed_quantity", latest.feed_quantity),
            "feeding_time": updates.get("feeding_time", latest.feeding_time) if "feeding_time" in updates else latest.feeding_time,
            "weather": updates.get("weather", latest.weather) if "weather" in updates else latest.weather,
            "water_temperature": updates.get("water_temperature", latest.water_temperature) if "water_temperature" in updates else latest.water_temperature,
            "notes": updates.get("notes", latest.notes) if "notes" in updates else latest.notes,
        }),
        logical_id=latest.logical_id or latest.id,
        version=latest.version + 1,
        supersedes_id=latest.id,
        is_current=True,
        status=status,
        revision_reason=reason,
        effective_at=effective_at,
        review_status=review_status,
        reviewed_at=utcnow() if review_status == REVIEW_APPROVED else None,
        is_late=is_late,
    )
    latest.is_current = False
    db.add(new)
    db.flush()
    return new


def _load_batch(db, batch_id: int) -> Batch:
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


def sync_feeding(db, payload) -> dict:
    """处理一次设备/人工上送。返回 {result, record, conflict_id?, message?}。"""
    data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
    mode = (data.get("sync_mode") or "create").lower()
    effective_at = data.get("effective_at") or utcnow()

    device_id = data.get("device_id")
    device_seq = data.get("device_seq")
    if device_id is not None and device_seq is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="提供 device_id 时必须同时提供 device_seq")

    with _writer_lock:
        if mode in ("revision", "revocation"):
            result_kind = "revised" if mode == "revision" else "revoked"
            record = _apply_revision(
                db, data=data, mode=mode, effective_at=effective_at,
                device_id=device_id, device_seq=device_seq,
            )
            db.commit()
            db.refresh(record)
            return {"result": result_kind, "record": record}

        return _create_or_dedupe(db, data=data, device_id=device_id,
                                 device_seq=device_seq)


def _create_or_dedupe(db, *, data, device_id, device_seq) -> dict:
    batch = _load_batch(db, data["batch_id"])
    incoming_hash = content_hash_from_payload(data)
    now = utcnow()
    closed = is_batch_closed(batch)
    review_status = REVIEW_PENDING if closed else REVIEW_APPROVED

    # 人工录入（无设备键）：直接建链，不参与幂等台账
    if not device_id:
        record = FeedingRecord(
            **{k: data[k] for k in FEEDING_CONTENT_FIELDS},
            source=SOURCE_MANUAL,
            content_hash=incoming_hash,
            version=1, is_current=True, status=FEEDING_STATUS_ACTIVE,
            effective_at=now,
            review_status=review_status,
            reviewed_at=now if not closed else None,
            is_late=closed,
        )
        db.add(record)
        db.flush()
        record.logical_id = record.id
        db.commit()
        db.refresh(record)
        return {"result": "pending" if closed else "created", "record": record}

    # 设备上送：先查幂等台账（加行锁，锁内完成读改写）
    ledger = db.query(FeedingSyncKey).filter(
        FeedingSyncKey.device_id == device_id,
        FeedingSyncKey.device_seq == device_seq,
    ).with_for_update().first()

    if ledger is None:
        record = FeedingRecord(
            **{k: data[k] for k in FEEDING_CONTENT_FIELDS},
            device_id=device_id, device_seq=device_seq, source=SOURCE_DEVICE,
            occurred_at=data.get("occurred_at"),
            content_hash=incoming_hash,
            version=1, is_current=True, status=FEEDING_STATUS_ACTIVE,
            effective_at=now,
            review_status=review_status,
            reviewed_at=now if not closed else None,
            is_late=closed,
        )
        db.add(record)
        try:
            db.flush()
        except IntegrityError:
            # 并发首传竞争：唯一约束兜底，转为“已存在”分支
            db.rollback()
            return _existing_key_branch(db, device_id, device_seq, incoming_hash, data, now)

        record.logical_id = record.id
        ledger = FeedingSyncKey(
            device_id=device_id, device_seq=device_seq,
            batch_id=record.batch_id,
            root_record_id=record.id, current_record_id=record.id,
            content_hash=incoming_hash, has_conflict=False,
            last_seen_at=now,
        )
        db.add(ledger)
        db.commit()
        db.refresh(record)
        return {"result": "pending" if closed else "created", "record": record}

    return _existing_key_branch(db, device_id, device_seq, incoming_hash, data, now,
                                ledger=ledger)


def _existing_key_branch(db, device_id, device_seq, incoming_hash, data, now,
                         ledger: Optional[FeedingSyncKey] = None) -> dict:
    if ledger is None:
        ledger = db.query(FeedingSyncKey).filter(
            FeedingSyncKey.device_id == device_id,
            FeedingSyncKey.device_seq == device_seq,
        ).with_for_update().first()
        if ledger is None:
            # 极端竞争下约束行又消失（不会发生），安全重试整单
            return _create_or_dedupe(db, data=data, device_id=device_id,
                                     device_seq=device_seq)

    ledger.last_seen_at = now
    current = db.query(FeedingRecord).filter(
        FeedingRecord.id == ledger.current_record_id
    ).first()

    if incoming_hash == ledger.content_hash:
        # 内容完全相同：幂等返回原记录，绝不重复累计
        db.commit()
        return {"result": "duplicate", "record": current,
                "message": "重复同步，已返回原记录"}

    # 同键内容冲突：登记（相同冲突内容只挂一条 open 工单），不产生投喂
    existing_open = db.query(FeedingConflict).filter(
        FeedingConflict.sync_key_id == ledger.id,
        FeedingConflict.content_hash == incoming_hash,
        FeedingConflict.status == "open",
    ).first()
    if existing_open is None:
        conflict = FeedingConflict(
            sync_key_id=ledger.id,
            device_id=device_id, device_seq=device_seq,
            payload_json=json.dumps(_serialize_payload(data), ensure_ascii=False),
            content_hash=incoming_hash,
            status="open",
        )
        db.add(conflict)
        ledger.has_conflict = True
        db.flush()
        conflict_id = conflict.id
    else:
        conflict_id = existing_open.id
    db.commit()
    db.refresh(current)
    return {
        "result": "conflict", "record": current, "conflict_id": conflict_id,
        "message": "同一设备流水号上送了不同内容，已挂起待人工裁决",
    }


def _serialize_payload(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if isinstance(v, datetime) or hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _resolve_target_ledger(db, *, data, device_id, device_seq):
    rev_id = data.get("revision_of_device_id")
    rev_seq = data.get("revision_of_device_seq")
    if rev_id is not None and rev_seq is not None:
        device_id, device_seq = rev_id, rev_seq
    if not device_id or device_seq is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail="更正/撤销必须携带设备键（device_id+device_seq 或 revision_of_*）")
    ledger = db.query(FeedingSyncKey).filter(
        FeedingSyncKey.device_id == device_id,
        FeedingSyncKey.device_seq == device_seq,
    ).with_for_update().first()
    if ledger is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="找不到该设备流水号对应的原记录")
    return ledger


def _apply_revision(db, *, data, mode, effective_at, device_id, device_seq) -> FeedingRecord:
    ledger = _resolve_target_ledger(db, data=data, device_id=device_id, device_seq=device_seq)
    latest = db.query(FeedingRecord).filter(
        FeedingRecord.id == ledger.current_record_id
    ).first()
    batch = _load_batch(db, latest.batch_id)
    closed = is_batch_closed(batch)
    review_status = REVIEW_PENDING if closed else REVIEW_APPROVED

    if mode == "revocation":
        if latest.status == FEEDING_STATUS_REVOKED and latest.is_current:
            return latest  # 撤销幂等
        updates = {  # 保留原业务字段，仅切状态
            "batch_id": latest.batch_id,
            "feeding_date": latest.feeding_date,
            "feed_type": latest.feed_type,
            "feed_quantity": latest.feed_quantity,
            "feeding_time": latest.feeding_time,
            "weather": latest.weather,
            "water_temperature": latest.water_temperature,
            "notes": latest.notes,
        }
        new = _make_version_row(
            db, latest=latest, updates=updates, reason="revocation",
            effective_at=effective_at, review_status=review_status,
            is_late=closed, status=FEEDING_STATUS_REVOKED,
        )
    else:
        updates = {k: data[k] for k in FEEDING_CONTENT_FIELDS if data.get(k) is not None}
        # 必填字段缺省沿用原值
        for k in FEEDING_CONTENT_FIELDS:
            updates.setdefault(k, getattr(latest, k))
        new_hash = content_hash_from_payload(updates)
        if new_hash == content_hash_of_record(latest):
            return latest  # 更正内容与当前版本相同：幂等，不追加版本
        new = _make_version_row(
            db, latest=latest, updates=updates, reason="correction",
            effective_at=effective_at, review_status=review_status,
            is_late=closed, status=FEEDING_STATUS_ACTIVE,
        )

    ledger.current_record_id = new.id
    ledger.content_hash = new.content_hash
    db.flush()
    return new


# --------------------------------------------------------------------------- #
# 版本选择 / 汇总
# --------------------------------------------------------------------------- #

def _approved_version_rows(db, as_of: datetime):
    """所有在 as_of 时点已批准且已到生效时点的版本行。"""
    return db.query(FeedingRecord).filter(
        FeedingRecord.review_status == REVIEW_APPROVED,
        FeedingRecord.effective_at <= as_of,
        FeedingRecord.status.in_([FEEDING_STATUS_ACTIVE, FEEDING_STATUS_REVOKED]),
    ).all()


def effective_records(db, *, batch_id: Optional[int] = None,
                      business_date=None, as_of: Optional[datetime] = None,
                      include_revoked: bool = False) -> list[FeedingRecord]:
    """每个逻辑链在 as_of 时点的生效版本（最新的已批准已生效版本）。"""
    as_of = as_of or utcnow()
    rows = _approved_version_rows(db, as_of)
    latest_by_chain: dict[int, FeedingRecord] = {}
    for row in rows:
        key = row.logical_id or row.id
        prev = latest_by_chain.get(key)
        if prev is None or row.version > prev.version:
            latest_by_chain[key] = row
    result = list(latest_by_chain.values())
    if batch_id is not None:
        result = [r for r in result if r.batch_id == batch_id]
    if business_date is not None:
        result = [r for r in result if r.feeding_date == business_date]
    if not include_revoked:
        result = [r for r in result if r.status == FEEDING_STATUS_ACTIVE]
    result.sort(key=lambda r: (r.feeding_date, r.id))
    return result


def daily_totals(db, *, batch_id: int, business_date, as_of: Optional[datetime] = None) -> dict:
    as_of = as_of or utcnow()
    active = effective_records(db, batch_id=batch_id, business_date=business_date,
                               as_of=as_of, include_revoked=False)
    total = round(sum(r.feed_quantity for r in active), 6)
    pending = db.query(FeedingRecord).filter(
        FeedingRecord.batch_id == batch_id,
        FeedingRecord.feeding_date == business_date,
        FeedingRecord.review_status == REVIEW_PENDING,
    ).count()
    return {
        "batch_id": batch_id,
        "business_date": business_date,
        "as_of": as_of,
        "total_quantity": total,
        "feeding_count": len(active),
        "pending_count": pending,
    }


# --------------------------------------------------------------------------- #
# 游标分页
# --------------------------------------------------------------------------- #

def paginate_records(db, *, limit: int, cursor: Optional[str] = None,
                     batch_id: Optional[int] = None,
                     business_date=None,
                     review_status: Optional[str] = None,
                     current_only: bool = True) -> dict:
    query = db.query(FeedingRecord)
    if current_only:
        query = query.filter(FeedingRecord.is_current.is_(True))
    if batch_id is not None:
        query = query.filter(FeedingRecord.batch_id == batch_id)
    if business_date is not None:
        query = query.filter(FeedingRecord.feeding_date == business_date)
    if review_status:
        query = query.filter(FeedingRecord.review_status == review_status)

    if cursor:
        ts, last_id = decode_cursor(cursor)
        # (created_at, id) > (ts, last_id)
        query = query.filter(
            or_(
                FeedingRecord.created_at > ts,
                and_(FeedingRecord.created_at == ts, FeedingRecord.id > last_id),
            )
        )

    query = query.order_by(asc(FeedingRecord.created_at), asc(FeedingRecord.id))
    rows = query.limit(limit + 1).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        next_cursor = encode_cursor(page[-1].created_at, page[-1].id)
    return {"items": page, "next_cursor": next_cursor, "has_more": has_more}


# --------------------------------------------------------------------------- #
# 审核 / 冲突裁决
# --------------------------------------------------------------------------- #

def review_record(db, record_id: int, approve: bool, note: Optional[str] = None) -> FeedingRecord:
    with _writer_lock:
        record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
        if record is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="投喂记录不存在")
        if record.review_status != REVIEW_PENDING:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="该记录不处于待审状态")
        record.reviewed_at = utcnow()
        if note:
            record.notes = (record.notes or "") + f"｜审核备注: {note}"

        if approve:
            record.review_status = REVIEW_APPROVED
            # 批准时点开始影响汇总（迟报不回溯到关闭前）
            record.effective_at = utcnow()
        else:
            record.review_status = REVIEW_REJECTED
            record.is_current = False
            # 驳回新版本：恢复它取代的最近一个已批准版本为当前版本
            prev_id = record.supersedes_id
            while prev_id is not None:
                prev = db.query(FeedingRecord).filter(
                    FeedingRecord.id == prev_id
                ).first()
                if prev is None:
                    break
                if prev.review_status == REVIEW_APPROVED:
                    prev.is_current = True
                    if record.device_id:
                        ledger = db.query(FeedingSyncKey).filter(
                            FeedingSyncKey.device_id == record.device_id,
                            FeedingSyncKey.device_seq == record.device_seq,
                        ).with_for_update().first()
                        if ledger is not None:
                            ledger.current_record_id = prev.id
                            ledger.content_hash = prev.content_hash
                    break
                prev_id = prev.supersedes_id
        db.commit()
        db.refresh(record)
        return record


def resolve_conflict(db, conflict_id: int, action: str,
                     note: Optional[str] = None) -> dict:
    """action=accept：以冲突来报作为更正版本；keep：维持原记录。"""
    with _writer_lock:
        conflict = db.query(FeedingConflict).filter(FeedingConflict.id == conflict_id).first()
        if conflict is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="冲突工单不存在")
        if conflict.status != "open":
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="该冲突已裁决")

        ledger = db.query(FeedingSyncKey).filter(
            FeedingSyncKey.id == conflict.sync_key_id
        ).with_for_update().first()
        latest = db.query(FeedingRecord).filter(
            FeedingRecord.id == ledger.current_record_id
        ).first()
        now = utcnow()

        if action == "accept":
            raw_payload = json.loads(conflict.payload_json)
            # 经 pydantic 还原为 date/datetime 等类型
            from ..schemas import FeedingRecordCreate
            payload = FeedingRecordCreate(**raw_payload).model_dump()
            batch = _load_batch(db, payload["batch_id"])
            closed = is_batch_closed(batch)
            updates = {k: payload.get(k, getattr(latest, k)) for k in FEEDING_CONTENT_FIELDS}
            new = _make_version_row(
                db, latest=latest, updates=updates, reason="conflict_resolution",
                effective_at=now,
                review_status=REVIEW_PENDING if closed else REVIEW_APPROVED,
                is_late=closed, status=FEEDING_STATUS_ACTIVE,
            )
            ledger.current_record_id = new.id
            ledger.content_hash = conflict.content_hash
            conflict.new_version_record_id = new.id
            conflict.status = "resolved_accept"
        elif action == "keep":
            conflict.status = "resolved_keep"
        else:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="action 必须是 accept 或 keep")

        conflict.resolution_note = note
        conflict.resolved_at = now
        db.commit()
        return {"action": action, "conflict_id": conflict.id,
                "new_record_id": conflict.new_version_record_id}


# --------------------------------------------------------------------------- #
# 日报：签署 / 重放
# --------------------------------------------------------------------------- #

def _line_dict(r: FeedingRecord) -> dict:
    return {
        "record_id": r.id,
        "logical_id": r.logical_id or r.id,
        "version": r.version,
        "status": r.status,
        "revision_reason": r.revision_reason,
        "feed_type": r.feed_type,
        "feed_quantity": r.feed_quantity if r.status == FEEDING_STATUS_ACTIVE else 0.0,
        "feeding_date": r.feeding_date.isoformat(),
        "feeding_time": r.feeding_time,
        "is_late": r.is_late,
        "device_id": r.device_id,
        "device_seq": r.device_seq,
    }


def build_daily_snapshot(db, *, batch_id: int, business_date,
                         as_of: Optional[datetime] = None) -> dict:
    as_of = as_of or utcnow()
    _load_batch(db, batch_id)
    # 撤销行也入快照（置零并标记），便于日后看到“当时已撤销”
    rows = effective_records(db, batch_id=batch_id, business_date=business_date,
                             as_of=as_of, include_revoked=True)
    active = [r for r in rows if r.status == FEEDING_STATUS_ACTIVE]
    lines = [_line_dict(r) for r in rows]
    return {
        "batch_id": batch_id,
        "business_date": business_date.isoformat(),
        "as_of": as_of.isoformat(),
        "lines": lines,
        "totals": {
            "total_quantity": round(sum(r.feed_quantity for r in active), 6),
            "feeding_count": len(active),
        },
    }


def sign_daily_report(db, *, batch_id: int, business_date,
                      as_of: Optional[datetime] = None,
                      resign: bool = False) -> DailyFeedingReport:
    with _writer_lock:
        existing = db.query(DailyFeedingReport).filter(
            DailyFeedingReport.batch_id == batch_id,
            DailyFeedingReport.business_date == business_date,
        ).first()
        if existing and not resign:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="该日报已签署，可通过重放接口查看当时版本")

        as_of = as_of or utcnow()
        snapshot = build_daily_snapshot(db, batch_id=batch_id,
                                        business_date=business_date, as_of=as_of)
        totals = snapshot["totals"]
        if existing:
            # 仅在测试/管理员显式 resign 时覆盖；正常流程不会走到
            existing.snapshot_json = json.dumps(snapshot, ensure_ascii=False)
            existing.as_of = as_of
            existing.signed_at = utcnow()
            existing.total_quantity = totals["total_quantity"]
            existing.feeding_count = totals["feeding_count"]
            db.commit()
            return existing

        report = DailyFeedingReport(
            batch_id=batch_id,
            business_date=business_date,
            as_of=as_of,
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            total_quantity=totals["total_quantity"],
            feeding_count=totals["feeding_count"],
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        return report


def replay_daily_report(db, report_id: int) -> dict:
    """按签署时快照重放，并对比当前生效版本标注 changed_since_sign。"""
    report = db.query(DailyFeedingReport).filter(
        DailyFeedingReport.id == report_id
    ).first()
    if report is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="日报不存在")

    snapshot = json.loads(report.snapshot_json)
    # 重放：仅用快照数据重新汇总，不读取后来的任何版本
    replayed_total = round(
        sum(line["feed_quantity"] for line in snapshot["lines"]
            if line["status"] == FEEDING_STATUS_ACTIVE), 6)
    replayed_count = sum(1 for line in snapshot["lines"]
                         if line["status"] == FEEDING_STATUS_ACTIVE)

    current_rows = effective_records(
        db, batch_id=report.batch_id, business_date=report.business_date,
        as_of=utcnow(), include_revoked=True)
    current_map = {
        (r.logical_id or r.id): (r.version, r.status, round(r.feed_quantity, 6),
                                 r.feed_type)
        for r in current_rows
    }
    snap_map = {
        line["logical_id"]: (line["version"], line["status"],
                             round(line["feed_quantity"], 6), line["feed_type"])
        for line in snapshot["lines"]
    }
    changed = current_map != snap_map
    current_total = round(sum(q for _, st, q, _ in current_map.values()
                              if st == FEEDING_STATUS_ACTIVE), 6)
    current_count = sum(1 for _, st, _, _ in current_map.values()
                        if st == FEEDING_STATUS_ACTIVE)

    return {
        "id": report.id,
        "batch_id": report.batch_id,
        "business_date": report.business_date,
        "signed_at": report.signed_at,
        "as_of": report.as_of,
        "lines": snapshot["lines"],
        "total_quantity": replayed_total,
        "feeding_count": replayed_count,
        "changed_since_sign": changed,
        "current_totals": {
            "total_quantity": current_total,
            "feeding_count": current_count,
        },
    }
