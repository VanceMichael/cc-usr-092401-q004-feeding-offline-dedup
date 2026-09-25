from pydantic import BaseModel, Field
from typing import Optional, List, Any
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
        orm_mode = True

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
        orm_mode = True

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
        orm_mode = True

# 参与“内容是否相同”判定的业务字段（设备身份与时间戳不参与）
FEEDING_CONTENT_FIELDS = (
    "batch_id", "feeding_date", "feed_type", "feed_quantity",
    "feeding_time", "weather", "water_temperature", "notes",
)


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
    # —— 离线同步身份：人工录入可不传 ——
    device_id: Optional[str] = Field(default=None, max_length=64)
    device_seq: Optional[int] = None
    occurred_at: Optional[datetime] = None
    # sync_mode=create（默认）幂等创建；revision 更正；revocation 撤销
    sync_mode: Optional[str] = None
    # 更正/撤销时指定的生效时点（缺省为当前，即立即生效）
    effective_at: Optional[datetime] = None
    # 更正/撤销时携带的设备键（也可走同键重传）
    revision_of_device_id: Optional[str] = None
    revision_of_device_seq: Optional[int] = None


class FeedingRecordUpdate(BaseModel):
    batch_id: Optional[int] = None
    feeding_date: Optional[date] = None
    feed_type: Optional[str] = None
    feed_quantity: Optional[float] = None
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None


class FeedingRecordResponse(FeedingRecordBase):
    id: int
    created_at: datetime

    # 同步与版本信息
    device_id: Optional[str] = None
    device_seq: Optional[int] = None
    source: str = "manual"
    occurred_at: Optional[datetime] = None
    logical_id: Optional[int] = None
    version: int = 1
    supersedes_id: Optional[int] = None
    is_current: bool = True
    status: str = "active"
    revision_reason: Optional[str] = None

    # 生效/审核/迟报
    effective_at: datetime
    review_status: str = "approved"
    reviewed_at: Optional[datetime] = None
    is_late: bool = False
    has_conflict: bool = False
    # 是否已对“现在”生效（effective_at<=now 且 已审核 且 当前版本）
    in_effect: bool = True

    class Config:
        from_attributes = True


class FeedingSyncResult(BaseModel):
    """设备同步结果。result 区分：created / duplicate / conflict / revised / revoked / pending"""
    result: str
    record: FeedingRecordResponse
    conflict_id: Optional[int] = None
    message: Optional[str] = None


class FeedingPage(BaseModel):
    items: List[FeedingRecordResponse]
    next_cursor: Optional[str] = None
    has_more: bool = False


class ConflictPayload(BaseModel):
    batch_id: int
    feeding_date: date
    feed_type: str
    feed_quantity: float
    feeding_time: Optional[str] = None
    weather: Optional[str] = None
    water_temperature: Optional[float] = None
    notes: Optional[str] = None
    occurred_at: Optional[datetime] = None


class FeedingConflictResponse(BaseModel):
    id: int
    device_id: str
    device_seq: int
    payload: ConflictPayload
    content_hash: str
    status: str
    resolution_note: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None
    current_record: Optional[FeedingRecordResponse] = None

    class Config:
        from_attributes = True


class DailyReportLine(BaseModel):
    record_id: int
    logical_id: Optional[int] = None
    version: int = 1
    status: str
    feed_type: str
    feed_quantity: float
    feeding_date: date
    feeding_time: Optional[str] = None
    is_late: bool = False
    device_id: Optional[str] = None
    device_seq: Optional[int] = None


class DailyFeedingReportResponse(BaseModel):
    id: int
    batch_id: int
    business_date: date
    signed_at: datetime
    as_of: datetime
    total_quantity: float
    feeding_count: int
    lines: List[DailyReportLine]
    # 重放与当前的差异（签署后是否有新版本生效）
    changed_since_sign: bool = False

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
        orm_mode = True

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
        orm_mode = True

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
        orm_mode = True

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
        orm_mode = True

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
    status: str = "active"
    revision_reason: Optional[str] = None
    version: int = 1
    is_late: bool = False
    review_status: str = "approved"
    effective_at: Optional[datetime] = None
    device_id: Optional[str] = None
    device_seq: Optional[int] = None
    record_id: Optional[int] = None

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
