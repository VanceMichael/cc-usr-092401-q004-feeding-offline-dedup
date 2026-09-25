from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import date, datetime
from ..database import get_db
from ..models import Batch, Pond, StockingRecord, FeedingRecord, CostRecord, HarvestSale, WaterQualityRecord, MedicationRecord
from ..schemas import CultureCycleAnalysis, BatchTraceability, BatchInfo, PondInfo
from ..services import feeding_service

router = APIRouter(
    prefix="/api/analysis",
    tags=["养殖周期分析"]
)

@router.get("/cycle/{batch_id}/", response_model=CultureCycleAnalysis)
def analyze_cycle(batch_id: int, as_of: Optional[datetime] = None,
                  db: Session = Depends(get_db)):
    """周期分析。投喂量只统计在 as_of（默认现在）时点生效的版本，
    重复同步不累计、撤销版本不计、迟报未审批不计。"""
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()

    initial_quantity = db.query(func.sum(StockingRecord.quantity)).filter(
        StockingRecord.batch_id == batch.id
    ).scalar() or 0

    harvest_weight = db.query(func.sum(HarvestSale.weight)).filter(
        HarvestSale.batch_id == batch.id
    ).scalar() or 0

    # 投喂：取版本链在该时点的生效记录（已去重、已排除撤销/待审）
    effective_feedings = feeding_service.effective_records(
        db, batch_id=batch.id, as_of=as_of, include_revoked=False
    )
    feed_total = round(sum(r.feed_quantity for r in effective_feedings), 6)

    total_cost = db.query(func.sum(CostRecord.amount)).filter(
        CostRecord.batch_id == batch.id
    ).scalar() or 0

    total_revenue = db.query(func.sum(HarvestSale.total_amount)).filter(
        HarvestSale.batch_id == batch.id
    ).scalar() or 0

    harvest_date = batch.actual_harvest_date
    days_cultured = None
    if harvest_date:
        days_cultured = (harvest_date - batch.stocking_date).days

    survival_rate = 0
    if initial_quantity > 0 and harvest_weight > 0:
        avg_weight_per_fish = 0.5
        estimated_survival = harvest_weight / avg_weight_per_fish
        survival_rate = (estimated_survival / initial_quantity) * 100

    feed_conversion_ratio = 0
    if harvest_weight > 0 and feed_total > 0:
        feed_conversion_ratio = feed_total / harvest_weight

    yield_per_mu = 0
    if pond and pond.area > 0:
        yield_per_mu = harvest_weight / pond.area

    profit = total_revenue - total_cost

    costs = db.query(
        CostRecord.cost_type,
        func.sum(CostRecord.amount).label('total')
    ).filter(
        CostRecord.batch_id == batch.id
    ).group_by(CostRecord.cost_type).all()

    cost_breakdown = {c.cost_type: c.total for c in costs}

    known_types = ['feed', 'medicine', 'labor', 'electricity']
    other_cost = sum(
        amount for cost_type, amount in cost_breakdown.items()
        if cost_type not in known_types
    )

    cost_summary_dict = {
        "feed_cost": cost_breakdown.get('feed', 0),
        "medicine_cost": cost_breakdown.get('medicine', 0),
        "labor_cost": cost_breakdown.get('labor', 0),
        "electricity_cost": cost_breakdown.get('electricity', 0),
        "other_cost": other_cost,
        "total_cost": total_cost
    }

    # 按饲料类型汇总生效版本
    by_type: dict[str, list[FeedingRecord]] = {}
    for r in effective_feedings:
        by_type.setdefault(r.feed_type, []).append(r)

    feeding_count = len(effective_feedings)
    avg_daily_feed = 0
    if days_cultured and days_cultured > 0:
        avg_daily_feed = feed_total / days_cultured

    feeding_summary_result = {
        "total_feed_weight": feed_total,
        "feeding_count": feeding_count,
        "avg_daily_feed": avg_daily_feed
    }

    return CultureCycleAnalysis(
        batch_number=batch.batch_number,
        pond_name=pond.name if pond else "未知",
        species=batch.species,
        stocking_date=batch.stocking_date,
        harvest_date=harvest_date,
        days_cultured=days_cultured,
        initial_quantity=initial_quantity,
        harvest_weight=harvest_weight,
        survival_rate=round(survival_rate, 2),
        feed_total=feed_total,
        feed_conversion_ratio=round(feed_conversion_ratio, 2),
        area=pond.area if pond else 0,
        yield_per_mu=round(yield_per_mu, 2),
        total_cost=total_cost,
        total_revenue=total_revenue,
        profit=profit,
        cost_summary=cost_summary_dict,
        feeding_summary=feeding_summary_result
    )

@router.get("/traceability/{batch_id}/", response_model=BatchTraceability)
def batch_traceability(batch_id: int, as_of: Optional[datetime] = None,
                       include_revoked: bool = True, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()

    stocking_records = db.query(StockingRecord).filter(
        StockingRecord.batch_id == batch.id
    ).all()

    # 投喂只列当前生效版本（含已撤销标记行），重复同步的旧行不出现
    feeding_records = feeding_service.effective_records(
        db, batch_id=batch.id, as_of=as_of, include_revoked=include_revoked
    )

    water_quality_records = db.query(WaterQualityRecord).filter(
        WaterQualityRecord.batch_id == batch.id
    ).all()

    medication_records = db.query(MedicationRecord).filter(
        MedicationRecord.batch_id == batch.id
    ).all()

    cost_records = db.query(CostRecord).filter(
        CostRecord.batch_id == batch.id
    ).all()

    harvest_sales = db.query(HarvestSale).filter(
        HarvestSale.batch_id == batch.id
    ).all()

    return BatchTraceability(
        batch=BatchInfo(
            batch_number=batch.batch_number,
            species=batch.species,
            stocking_date=batch.stocking_date,
            harvest_date=batch.actual_harvest_date,
            status=batch.status,
            pond_id=batch.pond_id
        ),
        pond_info=PondInfo(
            name=pond.name if pond else None,
            area=pond.area if pond else None,
            water_depth=pond.water_depth if pond else None
        ),
        stocking_records=[
            {
                "species": r.species,
                "quantity": r.quantity,
                "source": r.source,
                "batch_number": r.batch_number,
                "stocking_date": r.created_at.date() if hasattr(r, 'created_at') else None
            } for r in stocking_records
        ],
        feeding_records=[
            {
                "feeding_date": r.feeding_date,
                "feed_type": r.feed_type,
                "quantity": r.feed_quantity if r.status == "active" else 0.0,
                "unit": "kg",
                "status": r.status,
                "revision_reason": r.revision_reason,
                "version": r.version,
                "is_late": r.is_late,
                "review_status": r.review_status,
                "effective_at": r.effective_at,
                "device_id": r.device_id,
                "device_seq": r.device_seq,
                "record_id": r.id,
            } for r in feeding_records
        ],
        water_quality_records=[
            {
                "record_date": r.record_date,
                "water_temperature": r.water_temperature,
                "ph_value": r.ph_value,
                "dissolved_oxygen": r.dissolved_oxygen
            } for r in water_quality_records
        ],
        medication_records=[
            {
                "medication_date": r.medication_date,
                "medication_name": r.drug_name,
                "dosage": r.dosage,
                "unit": r.dosage_unit
            } for r in medication_records
        ],
        cost_records=[
            {
                "cost_date": r.cost_date,
                "cost_type": r.cost_type,
                "amount": r.amount,
                "description": r.description
            } for r in cost_records
        ],
        harvest_sales=[
            {
                "sale_date": r.sale_date,
                "weight": r.weight,
                "unit_price": r.unit_price,
                "total_amount": r.total_amount,
                "buyer": r.buyer
            } for r in harvest_sales
        ]
    )

@router.get("/trace-by-number/{batch_number}/", response_model=BatchTraceability)
def trace_by_batch_number(batch_number: str, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.batch_number == batch_number).first()
    if not batch:
        raise HTTPException(status_code=404, detail=f"批次号 {batch_number} 不存在")
    return batch_traceability(batch.id, db)
