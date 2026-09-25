import base64
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import FeedingRecord, Batch, FeedingConflict
from ..schemas import (
    FeedingRecordCreate, FeedingRecordResponse, FeedingSyncResult,
    FeedingRecordPage, FeedingConflictResponse, ReviewAction, RevokeRequest,
)
from ..services import feeding_service

router = APIRouter(
    prefix="/api/feeding-records",
    tags=["投喂记录"]
)

_OUTCOME_MESSAGES = {
    "created": "新建投喂记录",
    "duplicate": "重复同步,返回原记录,未重复累计",
    "conflict": "同键内容冲突:已保留原记录,冲突已登记待处理",
    "revised": "更正已作为新版本接收,将在生效时点影响汇总",
    "revoked": "撤销已作为新版本接收,将在生效时点影响汇总",
    "pending": "记录已进入待审队列,审核通过前不计入汇总",
}


def _encode_cursor(received_at, record_id: int) -> str:
    raw = f"{received_at.isoformat()}:{record_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        ts, id_str = raw.rsplit(":", 1)
        return datetime.fromisoformat(ts), int(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="游标无效")


def _latest_version_map(db: Session, records: List[FeedingRecord]) -> dict:
    """root_id -> 该逻辑记录的最新版本行(只看版本号,与生效时点无关,用于展示标志)。"""
    root_ids = {(r.root_id or r.id) for r in records}
    if not root_ids:
        return {}
    family = db.query(FeedingRecord).filter(
        or_(FeedingRecord.root_id.in_(root_ids), FeedingRecord.id.in_(root_ids))
    ).all()
    latest: dict = {}
    for r in family:
        key = r.root_id or r.id
        incumbent = latest.get(key)
        if incumbent is None or r.version > incumbent.version or \
           (r.version == incumbent.version and r.id > incumbent.id):
            latest[key] = r
    return latest


def _serialize(db: Session, record: FeedingRecord, latest_map: dict = None) -> dict:
    if latest_map is None:
        latest_map = _latest_version_map(db, [record])
    key = record.root_id or record.id
    latest = latest_map.get(key, record)
    is_revoked = latest.action == "revoke"
    has_newer = latest.id != record.id
    is_late = bool(
        record.pending_reason in ("late_closed", "late_window")
        or (record.received_at is not None
            and record.received_at.date() > record.feeding_date)
    )
    return FeedingRecordResponse(
        id=record.id,
        batch_id=record.batch_id,
        feeding_date=record.feeding_date,
        feed_type=record.feed_type,
        feed_quantity=record.feed_quantity,
        feeding_time=record.feeding_time,
        weather=record.weather,
        water_temperature=record.water_temperature,
        notes=record.notes,
        created_at=record.created_at,
        device_id=record.device_id,
        client_seq=record.client_seq,
        content_hash=record.content_hash,
        version=record.version,
        action=record.action,
        root_id=record.root_id,
        replaces_id=record.replaces_id,
        occurred_at=record.occurred_at,
        effective_at=record.effective_at,
        received_at=record.received_at,
        review_status=record.review_status,
        pending_reason=record.pending_reason,
        conflict_flag=record.conflict_flag,
        is_late=is_late,
        is_revoked=is_revoked,
        has_newer_version=has_newer,
    ).model_dump()


@router.post("/")
def sync_feeding_record(record: FeedingRecordCreate, db: Session = Depends(get_db)):
    """设备/前端统一接收入口。

    * 重复同步(同设备+流水号+版本且内容相同):200 返回原记录;
    * 同键内容冲突:409,保留原记录并登记冲突;
    * 更正/撤销:以新版本落库,按 effective_at 影响汇总;
    * 关闭批次后到达或超窗补传:202 进入待审。
    """
    payload = record.model_dump(exclude_unset=True)
    db_batch = db.query(Batch).filter(Batch.id == record.batch_id).first()
    if not db_batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    try:
        result = feeding_service.ingest_feeding(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    body = FeedingSyncResult(
        outcome=result.outcome,
        message=_OUTCOME_MESSAGES[result.outcome],
        record=FeedingRecordResponse(**_serialize(db, result.record)),
        conflict_id=result.conflict.id if result.conflict else None,
    )

    status_code = {
        "created": 201,
        "revised": 201,
        "duplicate": 200,
        "revoked": 201,
        "pending": 202,
    }.get(result.outcome, 200)
    if result.outcome == "conflict":
        status_code = 409
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body))


@router.get("/", response_model=FeedingRecordPage)
def get_feeding_records(
    limit: int = 50,
    cursor: Optional[str] = None,
    batch_id: Optional[int] = None,
    review_status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """键集游标分页:固定沿只增的接收轴 (received_at, id) 升序推进。

    迟报的业务日期可能早于已有记录,若按业务日期分页,跨午夜/并发补传会插到
    已翻过的页之前造成漏项;按服务器接收时间推进,任何新到达的行都落在游标之后,
    因而并发补传下既不重复也不漏项。
    """
    limit = max(1, min(limit, 200))
    query = db.query(FeedingRecord)
    if batch_id is not None:
        query = query.filter(FeedingRecord.batch_id == batch_id)
    if review_status is not None:
        query = query.filter(FeedingRecord.review_status == review_status)

    if cursor:
        cursor_ts, cursor_id = _decode_cursor(cursor)
        query = query.filter(
            or_(
                FeedingRecord.received_at > cursor_ts,
                and_(FeedingRecord.received_at == cursor_ts, FeedingRecord.id > cursor_id),
            )
        )

    # 多取一条判断是否还有下一页
    rows = query.order_by(
        FeedingRecord.received_at.asc(), FeedingRecord.id.asc()
    ).limit(limit + 1).all()

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    latest_map = _latest_version_map(db, page_rows)
    items = [_serialize(db, r, latest_map) for r in page_rows]

    next_cursor = None
    if has_more and page_rows:
        next_cursor = _encode_cursor(page_rows[-1].received_at, page_rows[-1].id)

    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


@router.get("/{record_id}/", response_model=FeedingRecordResponse)
def get_feeding_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    return _serialize(db, record)


@router.post("/{record_id}/revoke/")
def revoke_feeding_record(
    record_id: int,
    body: Optional[RevokeRequest] = None,
    db: Session = Depends(get_db),
):
    """撤销一条投喂记录:追加 revoke 新版本(不删除历史),可指定生效时点。"""
    target = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="投喂记录不存在")

    effective_at = (body.effective_at if body else None) or datetime.utcnow()
    payload = {
        "batch_id": target.batch_id,
        "feeding_date": target.feeding_date,
        "feed_type": target.feed_type,
        "feed_quantity": target.feed_quantity,
        "feeding_time": target.feeding_time,
        "weather": target.weather,
        "water_temperature": target.water_temperature,
        "notes": target.notes,
        "action": "revoke",
        "root_id": target.root_id or target.id,
        "effective_at": effective_at,
    }
    try:
        result = feeding_service.ingest_feeding(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return JSONResponse(
        status_code=201,
        content=jsonable_encoder(FeedingSyncResult(
            outcome=result.outcome,
            message=_OUTCOME_MESSAGES[result.outcome],
            record=FeedingRecordResponse(**_serialize(db, result.record)),
        )),
    )


@router.post("/{record_id}/review/", response_model=FeedingRecordResponse)
def review_feeding_record(record_id: int, review: ReviewAction, db: Session = Depends(get_db)):
    """审核待审记录:approve 批准(立即或按指定时点生效),reject 驳回。"""
    record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    if record.review_status != "pending":
        raise HTTPException(status_code=400, detail="该记录不在待审状态")

    if review.action == "approve":
        record.review_status = "approved"
        record.pending_reason = None
        record.effective_at = review.effective_at or datetime.utcnow()
    elif review.action == "reject":
        record.review_status = "rejected"
    else:
        raise HTTPException(status_code=400, detail="不支持的审核动作")

    db.commit()
    db.refresh(record)
    return _serialize(db, record)


@router.get("/conflicts/list/", response_model=List[FeedingConflictResponse])
def list_conflicts(
    resolved: Optional[bool] = False,
    batch_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    query = db.query(FeedingConflict)
    if resolved is not None:
        query = query.filter(FeedingConflict.resolved == resolved)
    if batch_id is not None:
        query = query.filter(FeedingConflict.batch_id == batch_id)
    return query.order_by(FeedingConflict.created_at.desc()).all()


@router.post("/conflicts/{conflict_id}/resolve/", response_model=FeedingConflictResponse)
def resolve_conflict(conflict_id: int, review: ReviewAction, db: Session = Depends(get_db)):
    """处理冲突:kept=维持原记录(默认),applied=采用冲突请求中的内容(生成新版本)。"""
    conflict = db.query(FeedingConflict).filter(FeedingConflict.id == conflict_id).first()
    if not conflict:
        raise HTTPException(status_code=404, detail="冲突记录不存在")

    resolution = review.resolution or "kept"
    if resolution not in ("kept", "applied"):
        raise HTTPException(status_code=400, detail="resolution 只支持 kept / applied")

    if resolution == "applied":
        import json
        payload = json.loads(conflict.incoming_payload or "{}")
        existing = db.query(FeedingRecord).filter(
            FeedingRecord.id == conflict.existing_record_id
        ).first()
        if existing:
            payload.setdefault("device_id", conflict.device_id)
            payload["root_id"] = existing.root_id or existing.id
            payload.pop("version", None)
            try:
                feeding_service.ingest_feeding(db, payload)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

    conflict.resolved = True
    conflict.resolution = resolution
    conflict.resolved_at = datetime.utcnow()
    if conflict.existing_record_id:
        original = db.query(FeedingRecord).filter(
            FeedingRecord.id == conflict.existing_record_id
        ).first()
        if original and resolution == "kept":
            original.conflict_flag = False
    db.commit()
    db.refresh(conflict)
    return conflict


@router.delete("/{record_id}/")
def delete_feeding_record(record_id: int, db: Session = Depends(get_db)):
    """删除语义已改为撤销(保留历史版本链,日报重放不受影响)。"""
    target = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    payload = {
        "batch_id": target.batch_id,
        "feeding_date": target.feeding_date,
        "feed_type": target.feed_type,
        "feed_quantity": target.feed_quantity,
        "feeding_time": target.feeding_time,
        "weather": target.weather,
        "water_temperature": target.water_temperature,
        "notes": target.notes,
        "action": "revoke",
        "root_id": target.root_id or target.id,
    }
    result = feeding_service.ingest_feeding(db, payload)
    return {
        "message": "投喂记录已撤销(保留历史版本)",
        "outcome": result.outcome,
        "record_id": result.record.id,
    }
