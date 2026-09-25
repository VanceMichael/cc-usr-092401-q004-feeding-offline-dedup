from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, Text, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class Pond(Base):
    __tablename__ = "ponds"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    area = Column(Float, nullable=False, comment="面积(亩)")
    water_depth = Column(Float, nullable=False, comment="水深(米)")
    species = Column(String(100), comment="养殖品种")
    status = Column(String(20), default="active", comment="状态: active, inactive")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    batches = relationship("Batch", back_populates="pond")

class Batch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_number = Column(String(50), unique=True, index=True, nullable=False, comment="批次号")
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    species = Column(String(100), nullable=False, comment="养殖品种")
    stocking_date = Column(Date, nullable=False, comment="放苗日期")
    estimated_harvest_date = Column(Date, comment="预计收获日期")
    actual_harvest_date = Column(Date, comment="实际收获日期")
    status = Column(String(20), default="active", comment="状态: active, harvested, closed")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pond = relationship("Pond", back_populates="batches")
    stocking_records = relationship("StockingRecord", back_populates="batch")
    feeding_records = relationship("FeedingRecord", back_populates="batch")
    water_quality_records = relationship("WaterQualityRecord", back_populates="batch")
    medication_records = relationship("MedicationRecord", back_populates="batch")
    cost_records = relationship("CostRecord", back_populates="batch")
    harvest_sales = relationship("HarvestSale", back_populates="batch")

class StockingRecord(Base):
    __tablename__ = "stocking_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    species = Column(String(100), nullable=False, comment="品种")
    quantity = Column(Integer, nullable=False, comment="数量(尾)")
    source = Column(String(200), comment="来源")
    batch_number = Column(String(50), comment="苗种批次号")
    weight_per_unit = Column(Float, comment="单重(克/尾)")
    total_weight = Column(Float, comment="总重量(公斤)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="stocking_records")


# ---------------------------------------------------------------------------
# 投喂记录：版本化 + 离线同步幂等
#
# 现场设备只保证 (device_id, device_seq) 唯一：
#   * 重复同步同一键时，由 FeedingSyncKey 台账识别并返回原记录；
#   * 同键但内容不同构成冲突（FeedingConflict），不产生新投喂；
#   * 更正 / 撤销不改写旧行，而是追加新版本（同一 logical_id 链）；
#   * 每个版本带 effective_at，汇总在指定生效时点切换；
#   * 批次关闭后到达的记录 review_status=pending（迟报待审），审批后才计入；
#   * 已签署日报的快照存于 DailyFeedingReport，之后按当时版本重放。
# ---------------------------------------------------------------------------

# 记录行生命周期状态
FEEDING_STATUS_ACTIVE = "active"        # 当前生效版本
FEEDING_STATUS_SUPERSEDED = "superseded"  # 已被新版本取代
FEEDING_STATUS_REVOKED = "revoked"      # 已撤销

# 审核状态
REVIEW_APPROVED = "approved"
REVIEW_PENDING = "pending"
REVIEW_REJECTED = "rejected"

# 来源
SOURCE_DEVICE = "device"
SOURCE_MANUAL = "manual"

CLOSED_BATCH_STATUSES = ("closed", "harvested")


class FeedingRecord(Base):
    __tablename__ = "feeding_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False, index=True)

    # —— 业务内容 ——
    feeding_date = Column(Date, nullable=False, index=True, comment="业务日期(可能与上传日不同)")
    feed_type = Column(String(100), nullable=False, comment="饲料类型")
    feed_quantity = Column(Float, nullable=False, comment="投喂量(公斤)")
    feeding_time = Column(String(20), comment="投喂时间")
    weather = Column(String(50), comment="天气情况")
    water_temperature = Column(Float, comment="水温(℃)")
    notes = Column(Text, comment="备注")

    # —— 离线同步身份 ——（人工录入时为空）
    device_id = Column(String(64), nullable=True, index=True, comment="设备号")
    device_seq = Column(Integer, nullable=True, comment="设备本地流水号")
    source = Column(String(20), default=SOURCE_MANUAL, nullable=False, comment="device/manual")
    occurred_at = Column(DateTime, nullable=True, comment="设备侧事件发生时间")
    content_hash = Column(String(64), nullable=True, comment="业务内容指纹")

    # —— 版本链 ——
    logical_id = Column(Integer, ForeignKey("feeding_records.id"), nullable=True, index=True,
                        comment="逻辑记录id, 指向版本链根行; 首版指向自身")
    version = Column(Integer, default=1, nullable=False)
    supersedes_id = Column(Integer, nullable=True, comment="本版本取代的上一版本行id")
    is_current = Column(Boolean, default=True, nullable=False, index=True)
    status = Column(String(20), default=FEEDING_STATUS_ACTIVE, nullable=False,
                    comment="active/superseded/revoked")
    revision_reason = Column(String(30), nullable=True, comment="correction/revocation/conflict_resolution")

    # —— 生效与审核 ——
    effective_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True,
                          comment="版本对汇总生效的时点")
    review_status = Column(String(20), default=REVIEW_APPROVED, nullable=False, index=True,
                           comment="approved/pending/rejected")
    reviewed_at = Column(DateTime, nullable=True)
    is_late = Column(Boolean, default=False, nullable=False, comment="批次关闭后到达的迟报")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    batch = relationship("Batch", back_populates="feeding_records")

    __table_args__ = (
        # 幂等约束建在台账表上；版本链允许多行共用同一设备键，故此处只建普通索引
        Index("ix_feeding_device_key", "device_id", "device_seq"),
        Index("ix_feeding_lookup", "batch_id", "feeding_date"),
    )


class FeedingSyncKey(Base):
    """设备同步幂等台账：(device_id, device_seq) 全局唯一，服务重启后仍可判定。"""
    __tablename__ = "feeding_sync_keys"

    id = Column(Integer, primary_key=True)
    device_id = Column(String(64), nullable=False)
    device_seq = Column(Integer, nullable=False)
    batch_id = Column(Integer, nullable=False, comment="首次到达时的批次")
    root_record_id = Column(Integer, ForeignKey("feeding_records.id"), nullable=False, comment="版本链根行")
    current_record_id = Column(Integer, ForeignKey("feeding_records.id"), nullable=False, comment="当前版本行")
    content_hash = Column(String(64), nullable=False, comment="首次上送的内容指纹")
    has_conflict = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("device_id", "device_seq", name="uq_feeding_sync_device_key"),
    )


class FeedingConflict(Base):
    """同键不同内容的冲突来报，挂起等待人工裁决，不进入汇总。"""
    __tablename__ = "feeding_conflicts"

    id = Column(Integer, primary_key=True)
    sync_key_id = Column(Integer, ForeignKey("feeding_sync_keys.id"), nullable=False, index=True)
    device_id = Column(String(64), nullable=False)
    device_seq = Column(Integer, nullable=False)
    payload_json = Column(Text, nullable=False, comment="冲突来报的完整内容")
    content_hash = Column(String(64), nullable=False)
    status = Column(String(20), default="open", nullable=False, index=True, comment="open/resolved_accept/resolved_keep")
    resolution_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    new_version_record_id = Column(Integer, ForeignKey("feeding_records.id"), nullable=True)


class DailyFeedingReport(Base):
    """已签署日报：固化签署时点每个版本行，支持日后按当时版本重放。"""
    __tablename__ = "daily_feeding_reports"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    business_date = Column(Date, nullable=False, comment="业务日期")
    signed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    as_of = Column(DateTime, nullable=False, comment="签署时采用的生效时点")
    # 快照内容: {lines: [...], totals: {...}}
    snapshot_json = Column(Text, nullable=False)
    total_quantity = Column(Float, nullable=False, default=0)
    feeding_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("batch_id", "business_date", name="uq_daily_feeding_report"),
    )


class WaterQualityRecord(Base):
    __tablename__ = "water_quality_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    record_date = Column(Date, nullable=False, comment="检测日期")
    record_time = Column(String(20), comment="检测时间")
    water_temperature = Column(Float, comment="水温(℃)")
    ph_value = Column(Float, comment="pH值")
    dissolved_oxygen = Column(Float, comment="溶解氧(mg/L)")
    ammonia_nitrogen = Column(Float, comment="氨氮(mg/L)")
    nitrite = Column(Float, comment="亚硝酸盐(mg/L)")
    transparency = Column(Float, comment="透明度(cm)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="water_quality_records")

class MedicationRecord(Base):
    __tablename__ = "medication_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    medication_date = Column(Date, nullable=False, comment="用药日期")
    drug_name = Column(String(200), nullable=False, comment="药品名称")
    drug_type = Column(String(50), comment="药品类型")
    dosage = Column(Float, comment="用量")
    dosage_unit = Column(String(20), default="kg", comment="用量单位")
    administration_method = Column(String(100), comment="施用方法")
    purpose = Column(String(200), comment="用途")
    manufacturer = Column(String(200), comment="生产厂家")
    batch_number = Column(String(50), comment="药品批次号")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="medication_records")

class CostRecord(Base):
    __tablename__ = "cost_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    cost_date = Column(Date, nullable=False, comment="费用日期")
    cost_type = Column(String(50), nullable=False, comment="费用类型: feed, medicine, labor, electricity, other")
    amount = Column(Float, nullable=False, comment="金额(元)")
    description = Column(String(500), comment="费用描述")
    quantity = Column(Float, comment="数量")
    unit = Column(String(20), comment="单位")
    unit_price = Column(Float, comment="单价")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="cost_records")

class HarvestSale(Base):
    __tablename__ = "harvest_sales"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    sale_date = Column(Date, nullable=False, comment="销售日期")
    weight = Column(Float, nullable=False, comment="重量(公斤)")
    unit_price = Column(Float, nullable=False, comment="单价(元/公斤)")
    total_amount = Column(Float, comment="总金额(元)")
    buyer = Column(String(200), comment="买家")
    batch_number = Column(String(50), comment="追溯批次号")
    quality_grade = Column(String(50), comment="质量等级")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="harvest_sales")
