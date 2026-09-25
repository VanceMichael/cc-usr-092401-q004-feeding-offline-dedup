"""投喂日报:签署时对当时生效的数据做不可变快照;之后的更正/撤销不影响已签日报。"""
import json
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Batch, DailyReport
from ..schemas import DailyReportResponse, FeedingRecordResponse
from ..services import feeding_service
from .feeding import _serialize

router = APIRouter(
    prefix="/api/feeding-reports",
    tags=["投喂日报"]
)


def _snapshot_records(db: Session, batch_id: int, business_date: date, as_of: datetime):
    records = feeding_service.effective_records(
        db, batch_id, business_date=business_date, as_of=as_of
    )
    return [_serialize(db, r) for r in records]


@router.post("/{batch_id}/{business_date}/", response_model=DailyReportResponse)
def sign_daily_report(batch_id: int, business_date: date, db: Session = Depends(get_db)):
    """签署某业务日期的日报。重复签署返回原报(幂等),不覆盖快照。"""
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    existing = db.query(DailyReport).filter(
        DailyReport.batch_id == batch_id,
        DailyReport.business_date == business_date,
    ).first()
    if existing:
        return _report_response(existing)

    as_of = datetime.utcnow()
    items = _snapshot_records(db, batch_id, business_date, as_of)
    report = DailyReport(
        batch_id=batch_id,
        business_date=business_date,
        signed_at=as_of,
        as_of=as_of,
        total_quantity=sum(float(r["feed_quantity"]) for r in items),
        record_count=len(items),
        snapshot=json.dumps(jsonable_encoder(items), ensure_ascii=False),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return _report_response(report)


def _report_response(report: DailyReport) -> DailyReportResponse:
    return DailyReportResponse(
        id=report.id,
        batch_id=report.batch_id,
        business_date=report.business_date,
        signed_at=report.signed_at,
        as_of=report.as_of,
        total_quantity=report.total_quantity,
        record_count=report.record_count,
        snapshot=json.loads(report.snapshot),
    )


@router.get("/{batch_id}/{business_date}/", response_model=DailyReportResponse)
def get_daily_report(batch_id: int, business_date: date, db: Session = Depends(get_db)):
    """按签署时版本重放日报(不可变快照)。"""
    report = db.query(DailyReport).filter(
        DailyReport.batch_id == batch_id,
        DailyReport.business_date == business_date,
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="该日期日报尚未签署")
    return _report_response(report)


@router.get("/by-id/{report_id}/", response_model=DailyReportResponse)
def get_daily_report_by_id(report_id: int, db: Session = Depends(get_db)):
    report = db.query(DailyReport).filter(DailyReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="日报不存在")
    return _report_response(report)
