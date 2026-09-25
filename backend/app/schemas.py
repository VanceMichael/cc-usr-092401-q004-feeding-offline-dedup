from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime

class PondBase(BaseModel):
    name: str
    area: float
    water_depth: float
    species: Optional[str] = None
    status: Optional[str] = "active"

class PondCreate(PondBase):
    pass

class PondUpdate(BaseModel):
    name: Optional[str] = None
    area: Optional[float] = None
    water_depth: Optional[float] = None
    species: Optional[str] = None
    status: Optional[str] = None

class PondResponse(PondBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class BatchBase(BaseModel):
    batch_number: str
    pond_id: int
    species: str
    stocking_date: date
    estimated_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    status: Optional[str] = "active"

class BatchCreate(BatchBase):
    pass

class BatchUpdate(BaseModel):
    batch_number: Optional[str] = None
    pond_id: Optional[int] = None
    species: Optional[str] = None
    stocking_date: Optional[date] = None
    estimated_harvest_date: Optional[date] = None
    actual_harvest_date: Optional[date] = None
    status: Optional[str] = None

class BatchResponse(BatchBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class StockingRecordBase(BaseModel):
    batch_id: int
    species: str
    quantity: int
    source: Optional[str] = None
    batch_number: Optional[str] = None
    weight_per_unit: Optional[float] = None
    total_weight: Optional[float] = None
    notes: Optional[str] = None

class StockingRecordCreate(StockingRecordBase):
    pass

class StockingRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    species: Optional[str] = None
    quantity: Optional[int] = None
    source: Optional[str] = None
    batch_number: Optional[str] = None
    weight_per_unit: Optional[float] = None
    total_weight: Optional[float] = None
    notes: Optional[str] = None

class StockingRecordResponse(StockingRecordBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class FeedingRecordBase(BaseModel):
    batch_id: int
    feeding_date: date
    feed_type: str
    feed_quantity: float
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None

class FeedingRecordCreate(FeedingRecordBase):
    # 离线补传:设备号 + 设备本地流水号(设备只保证这一组合唯一)
    device_id: Optional[str] = None
    client_seq: Optional[str] = None
    # 设备时间轴:事件发生时间、要求生效的时间(可跨午夜,与上传时间不同)
    occurred_at: Optional[datetime] = None
    effective_at: Optional[datetime] = None
    # upsert=登记或更正, revoke=撤销;version 为设备自报版本号
    action: Optional[str] = "upsert"
    version: Optional[int] = None
    # 人工更正/撤销已有逻辑记录时使用
    root_id: Optional[int] = None

class FeedingRecordUpdate(BaseModel):
    # 更正与撤销一律以新版本实现;这里仅保留审批等管理字段
    review_status: Optional[str] = None

class FeedingRecordResponse(FeedingRecordBase):
    id: int
    created_at: datetime
    device_id: Optional[str] = None
    client_seq: Optional[str] = None
    content_hash: Optional[str] = None
    version: int
    action: str
    root_id: Optional[int] = None
    replaces_id: Optional[int] = None
    occurred_at: Optional[datetime] = None
    effective_at: datetime
    received_at: datetime
    review_status: str
    pending_reason: Optional[str] = None
    conflict_flag: bool
    # 便捷展示标志
    is_late: Optional[bool] = False
    is_revoked: Optional[bool] = False
    has_newer_version: Optional[bool] = False

    class Config:
        from_attributes = True

class FeedingSyncResult(BaseModel):
    """同步结果:重复同步返回原记录;冲突与新建/更正/撤销/待审结果不同。"""
    outcome: str
    message: str
    record: FeedingRecordResponse
    conflict_id: Optional[int] = None

class FeedingRecordPage(BaseModel):
    """键集游标分页:按 (feeding_date, id) 稳定排序,并发补传下不重不漏。"""
    items: List[FeedingRecordResponse]
    next_cursor: Optional[str] = None
    has_more: bool

class FeedingConflictResponse(BaseModel):
    id: int
    device_id: str
    client_seq: str
    version: int
    batch_id: Optional[int] = None
    existing_record_id: Optional[int] = None
    incoming_payload: Optional[str] = None
    resolved: bool
    resolution: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ReviewAction(BaseModel):
    # approve=批准待审记录并立即生效 / reject=驳回 / resolve_conflict=处理冲突
    action: str
    resolution: Optional[str] = None
    effective_at: Optional[datetime] = None

class RevokeRequest(BaseModel):
    effective_at: Optional[datetime] = None

class DailyReportResponse(BaseModel):
    id: int
    batch_id: int
    business_date: date
    signed_at: datetime
    as_of: datetime
    total_quantity: float
    record_count: int
    snapshot: List[FeedingRecordResponse]

    class Config:
        from_attributes = True

class WaterQualityRecordBase(BaseModel):
    batch_id: int
    record_date: date
    record_time: Optional[str] = None
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None
    ammonia_nitrogen: Optional[float] = None
    nitrite: Optional[float] = None
    transparency: Optional[float] = None
    notes: Optional[str] = None

class WaterQualityRecordCreate(WaterQualityRecordBase):
    pass

class WaterQualityRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    record_date: Optional[date] = None
    record_time: Optional[str] = None
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None
    ammonia_nitrogen: Optional[float] = None
    nitrite: Optional[float] = None
    transparency: Optional[float] = None
    notes: Optional[str] = None

class WaterQualityRecordResponse(WaterQualityRecordBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class MedicationRecordBase(BaseModel):
    batch_id: int
    medication_date: date
    drug_name: str
    drug_type: Optional[str] = None
    dosage: Optional[float] = None
    dosage_unit: Optional[str] = "kg"
    administration_method: Optional[str] = None
    purpose: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None

class MedicationRecordCreate(MedicationRecordBase):
    pass

class MedicationRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    medication_date: Optional[date] = None
    drug_name: Optional[str] = None
    drug_type: Optional[str] = None
    dosage: Optional[float] = None
    dosage_unit: Optional[str] = None
    administration_method: Optional[str] = None
    purpose: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None

class MedicationRecordResponse(MedicationRecordBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class CostRecordBase(BaseModel):
    batch_id: int
    cost_date: date
    cost_type: str
    amount: float
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class CostRecordCreate(CostRecordBase):
    pass

class CostRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    cost_date: Optional[date] = None
    cost_type: Optional[str] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class CostRecordResponse(CostRecordBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class HarvestSaleBase(BaseModel):
    batch_id: int
    sale_date: date
    weight: float
    unit_price: float
    total_amount: Optional[float] = None
    buyer: Optional[str] = None
    batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    notes: Optional[str] = None

class HarvestSaleCreate(HarvestSaleBase):
    pass

class HarvestSaleUpdate(BaseModel):
    batch_id: Optional[int] = None
    sale_date: Optional[date] = None
    weight: Optional[float] = None
    unit_price: Optional[float] = None
    total_amount: Optional[float] = None
    buyer: Optional[str] = None
    batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    notes: Optional[str] = None

class HarvestSaleResponse(HarvestSaleBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class CostSummaryItem(BaseModel):
    type: str
    amount: float

class FeedingSummaryItem(BaseModel):
    feed_type: str
    total_quantity: float
    feeding_count: int

class CultureCycleAnalysis(BaseModel):
    batch_number: str
    pond_name: str
    species: str
    stocking_date: date
    harvest_date: Optional[date] = None
    days_cultured: Optional[int] = None
    initial_quantity: int
    harvest_weight: float
    survival_rate: float
    feed_total: float
    feed_conversion_ratio: float
    area: float
    yield_per_mu: float
    total_cost: float
    total_revenue: float
    profit: float
    cost_summary: Optional[dict] = None
    feeding_summary: Optional[dict] = None

class StockingRecordTrace(BaseModel):
    species: str
    quantity: int
    source: Optional[str] = None
    batch_number: Optional[str] = None
    stocking_date: Optional[date] = None

class FeedingRecordTrace(BaseModel):
    feeding_date: date
    feed_type: str
    quantity: float
    unit: Optional[str] = None
    version: Optional[int] = 1
    action: Optional[str] = "upsert"
    review_status: Optional[str] = "approved"
    is_late: Optional[bool] = False
    conflict_flag: Optional[bool] = False
    effective_at: Optional[datetime] = None

class WaterQualityRecordTrace(BaseModel):
    record_date: date
    water_temperature: Optional[float] = None
    ph_value: Optional[float] = None
    dissolved_oxygen: Optional[float] = None

class MedicationRecordTrace(BaseModel):
    medication_date: date
    medication_name: str
    dosage: Optional[float] = None
    unit: Optional[str] = None

class CostRecordTrace(BaseModel):
    cost_date: date
    cost_type: str
    amount: float
    description: Optional[str] = None

class HarvestSaleTrace(BaseModel):
    sale_date: date
    weight: float
    unit_price: float
    total_amount: Optional[float] = None
    buyer: Optional[str] = None

class BatchInfo(BaseModel):
    batch_number: str
    species: str
    stocking_date: date
    harvest_date: Optional[date] = None
    status: str
    pond_id: Optional[int] = None

class PondInfo(BaseModel):
    name: Optional[str] = None
    area: Optional[float] = None
    water_depth: Optional[float] = None

class BatchTraceability(BaseModel):
    batch: BatchInfo
    pond_info: PondInfo
    stocking_records: List[StockingRecordTrace] = []
    feeding_records: List[FeedingRecordTrace] = []
    water_quality_records: List[WaterQualityRecordTrace] = []
    medication_records: List[MedicationRecordTrace] = []
    cost_records: List[CostRecordTrace] = []
    harvest_sales: List[HarvestSaleTrace] = []
