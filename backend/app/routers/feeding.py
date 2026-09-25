import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date, datetime
from ..database import get_db
from ..models import (
    FeedingRecord, FeedingSyncKey, FeedingConflict, Batch,
    REVIEW_PENDING, FEEDING_STATUS_ACTIVE, FEEDING_STATUS_REVOKED,
    SOURCE_MANUAL, CLOSED_BATCH_STATUSES,
)
from ..schemas import (
    FeedingRecordCreate, FeedingRecordUpdate, FeedingRecordResponse,
    FeedingSyncResult, FeedingPage, FeedingConflictResponse,
    DailyFeedingReportResponse, FEEDING_CONTENT_FIELDS,
)
from ..services import feeding_service as svc

router = APIRouter(
    prefix="/api/feeding-records",
    tags=["投喂记录"]
)


def _serialize(db: Session, record: FeedingRecord, now: Optional[datetime] = None) -> dict:
    now = now or svc.utcnow()
    has_conflict = False
    if record.device_id:
        ledger = db.query(FeedingSyncKey).filter(
            FeedingSyncKey.device_id == record.device_id,
            FeedingSyncKey.device_seq == record.device_seq,
        ).first()
        has_conflict = bool(ledger and ledger.has_conflict)
    data = FeedingRecordResponse.model_validate(record).model_dump()
    data["has_conflict"] = has_conflict
    data["in_effect"] = (
        record.review_status == "approved"
        and record.effective_at <= now
        and record.is_current
        and record.status == FEEDING_STATUS_ACTIVE
    )
    return data


@router.post("/", response_model=FeedingSyncResult)
def sync_feeding_record(record: FeedingRecordCreate, db: Session = Depends(get_db)):
    """设备/人工上送入口：幂等创建、识别冲突、更正、撤销。"""
    result = svc.sync_feeding(db, record)
    return {
        "result": result["result"],
        "record": _serialize(db, result["record"]),
        "conflict_id": result.get("conflict_id"),
        "message": result.get("message"),
    }


@router.get("/", response_model=FeedingPage)
def get_feeding_records(
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = None,
    batch_id: Optional[int] = None,
    business_date: Optional[date] = None,
    review_status: Optional[str] = None,
    include_history: bool = False,
    db: Session = Depends(get_db),
):
    """游标分页（按到达序稳定排列），默认只返回每个逻辑记录的当前版本。"""
    try:
        page = svc.paginate_records(
            db, limit=limit, cursor=cursor, batch_id=batch_id,
            business_date=business_date, review_status=review_status,
            current_only=not include_history,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    now = svc.utcnow()
    return {
        "items": [_serialize(db, r, now) for r in page["items"]],
        "next_cursor": page["next_cursor"],
        "has_more": page["has_more"],
    }


@router.get("/daily-totals/")
def get_daily_totals(batch_id: int, business_date: date,
                     as_of: Optional[datetime] = None, db: Session = Depends(get_db)):
    return svc.daily_totals(db, batch_id=batch_id, business_date=business_date, as_of=as_of)


@router.get("/conflicts/", response_model=List[FeedingConflictResponse])
def list_conflicts(status: Optional[str] = "open", db: Session = Depends(get_db)):
    query = db.query(FeedingConflict)
    if status:
        query = query.filter(FeedingConflict.status == status)
    conflicts = query.order_by(FeedingConflict.created_at.desc()).all()
    result = []
    for c in conflicts:
        ledger = db.query(FeedingSyncKey).filter(
            FeedingSyncKey.id == c.sync_key_id).first()
        current = None
        if ledger:
            row = db.query(FeedingRecord).filter(
                FeedingRecord.id == ledger.current_record_id).first()
            if row:
                current = _serialize(db, row)
        result.append({
            "id": c.id,
            "device_id": c.device_id,
            "device_seq": c.device_seq,
            "payload": json.loads(c.payload_json),
            "content_hash": c.content_hash,
            "status": c.status,
            "resolution_note": c.resolution_note,
            "created_at": c.created_at,
            "resolved_at": c.resolved_at,
            "current_record": current,
        })
    return result


@router.post("/conflicts/{conflict_id}/resolve/")
def resolve_conflict(conflict_id: int, action: str, note: Optional[str] = None,
                     db: Session = Depends(get_db)):
    return svc.resolve_conflict(db, conflict_id, action=action, note=note)


@router.post("/{record_id}/approve/")
def approve_record(record_id: int, note: Optional[str] = None, db: Session = Depends(get_db)):
    record = svc.review_record(db, record_id, approve=True, note=note)
    return {"message": "迟报已批准并计入", "record": _serialize(db, record)}


@router.post("/{record_id}/reject/")
def reject_record(record_id: int, note: Optional[str] = None, db: Session = Depends(get_db)):
    record = svc.review_record(db, record_id, approve=False, note=note)
    return {"message": "迟报已驳回，不计入汇总", "record": _serialize(db, record)}


@router.get("/{record_id}/", response_model=FeedingRecordResponse)
def get_feeding_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    return _serialize(db, record)


@router.put("/{record_id}/", response_model=FeedingSyncResult)
def manual_correct_record(record_id: int, record: FeedingRecordUpdate,
                          effective_at: Optional[datetime] = None,
                          db: Session = Depends(get_db)):
    """页面编辑 = 人工更正：追加新版本，在指定时点生效（默认立即）。"""
    db_record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    if not db_record.is_current:
        raise HTTPException(status_code=400, detail="只能更正当前版本")

    update_data = record.model_dump(exclude_unset=True)
    batch_id = update_data.get("batch_id", db_record.batch_id)
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    closed = batch.status in CLOSED_BATCH_STATUSES

    merged = {k: getattr(db_record, k) for k in FEEDING_CONTENT_FIELDS}
    merged.update(update_data)
    new_hash = svc.content_hash_from_payload(merged)
    if new_hash == svc.content_hash_of_record(db_record):
        return {"result": "duplicate", "record": _serialize(db, db_record),
                "message": "内容无变化"}

    with svc._writer_lock:
        new = svc._make_version_row(
            db, latest=db_record, updates=merged, reason="correction",
            effective_at=effective_at or svc.utcnow(),
            review_status=REVIEW_PENDING if closed else "approved",
            is_late=closed, status=FEEDING_STATUS_ACTIVE,
        )
        if db_record.device_id:
            ledger = db.query(FeedingSyncKey).filter(
                FeedingSyncKey.device_id == db_record.device_id,
                FeedingSyncKey.device_seq == db_record.device_seq,
            ).with_for_update().first()
            if ledger:
                ledger.current_record_id = new.id
                ledger.content_hash = new.content_hash
        db.commit()
        db.refresh(new)
    return {"result": "revised", "record": _serialize(db, new)}


@router.delete("/{record_id}/")
def revoke_feeding_record(record_id: int, effective_at: Optional[datetime] = None,
                          db: Session = Depends(get_db)):
    """页面删除 = 撤销：追加撤销版本，在指定时点起从汇总移除。"""
    db_record = db.query(FeedingRecord).filter(FeedingRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="投喂记录不存在")
    if not db_record.is_current:
        raise HTTPException(status_code=400, detail="只能撤销当前版本")

    batch = db.query(Batch).filter(Batch.id == db_record.batch_id).first()
    closed = batch.status in CLOSED_BATCH_STATUSES
    with svc._writer_lock:
        new = svc._make_version_row(
            db, latest=db_record,
            updates={k: getattr(db_record, k) for k in FEEDING_CONTENT_FIELDS},
            reason="revocation",
            effective_at=effective_at or svc.utcnow(),
            review_status=REVIEW_PENDING if closed else "approved",
            is_late=closed, status=FEEDING_STATUS_REVOKED,
        )
        if db_record.device_id:
            ledger = db.query(FeedingSyncKey).filter(
                FeedingSyncKey.device_id == db_record.device_id,
                FeedingSyncKey.device_seq == db_record.device_seq,
            ).with_for_update().first()
            if ledger:
                ledger.current_record_id = new.id
                ledger.content_hash = new.content_hash
        db.commit()
        db.refresh(new)
    return {"message": "记录已撤销（新版本），将在生效时点起移出汇总",
            "record": _serialize(db, new)}


# --------------------------------------------------------------------------- #
# 日报：签署 / 查询 / 重放
# --------------------------------------------------------------------------- #

daily_router = APIRouter(
    prefix="/api/feeding-daily-reports",
    tags=["投喂日报"]
)


@daily_router.post("/sign/")
def sign_report(batch_id: int, business_date: date,
                as_of: Optional[datetime] = None, db: Session = Depends(get_db)):
    report = svc.sign_daily_report(db, batch_id=batch_id,
                                   business_date=business_date, as_of=as_of)
    return svc.replay_daily_report(db, report.id)


@daily_router.get("/")
def list_reports(batch_id: Optional[int] = None, db: Session = Depends(get_db)):
    from ..models import DailyFeedingReport
    query = db.query(DailyFeedingReport)
    if batch_id is not None:
        query = query.filter(DailyFeedingReport.batch_id == batch_id)
    reports = query.order_by(
        DailyFeedingReport.business_date.desc()
    ).all()
    return [
        {
            "id": r.id,
            "batch_id": r.batch_id,
            "business_date": r.business_date,
            "signed_at": r.signed_at,
            "as_of": r.as_of,
            "total_quantity": r.total_quantity,
            "feeding_count": r.feeding_count,
        }
        for r in reports
    ]


@daily_router.get("/{report_id}/replay/")
def replay_report(report_id: int, db: Session = Depends(get_db)):
    return svc.replay_daily_report(db, report_id)
