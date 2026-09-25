from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, ForeignKey, Text,
    Boolean, UniqueConstraint, Index
)
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
    closed_at = Column(DateTime, comment="批次关闭时间,关闭后到达的补传进入待审")
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


class FeedingRecord(Base):
    """投喂记录(版本化追加表)。

    现场设备只保证「设备号 + 本地流水号」唯一:同一次业务事件可能在网络恢复后
    被多次补传。每次更正/撤销不是覆盖旧行,而是追加一条新版本行,通过 root_id
    串成同一条逻辑记录;汇总时按逻辑组选取在指定时点生效的最新版本。
    """
    __tablename__ = "feeding_records"
    __table_args__ = (
        # 幂等键:同一设备同一流水号同一版本只允许落库一次(重启后仍然成立)。
        # SQLite 中 NULL 互不相等,因此人工录入(device_id 为空)不受约束。
        UniqueConstraint("device_id", "client_seq", "version", name="uq_feeding_device_seq_version"),
        Index("ix_feeding_batch_date_id", "batch_id", "feeding_date", "id"),
        # 游标分页沿只增的接收轴推进,并发补传的新行一定落在游标之后
        Index("ix_feeding_batch_received_id", "batch_id", "received_at", "id"),
        Index("ix_feeding_root_id", "root_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    feeding_date = Column(Date, nullable=False, comment="业务/投喂日期(可能早于上传日期)")
    feed_type = Column(String(100), nullable=False, comment="饲料类型")
    feed_quantity = Column(Float, nullable=False, comment="投喂量(公斤)")
    feeding_time = Column(String(20), comment="投喂时间")
    weather = Column(String(50), comment="天气情况")
    water_temperature = Column(Float, comment="水温(℃)")
    notes = Column(Text, comment="备注")
    created_at = Column(DateTime, default=datetime.utcnow)

    # ---- 离线补传 / 幂等 ----
    device_id = Column(String(64), nullable=True, comment="现场设备号")
    client_seq = Column(String(64), nullable=True, comment="设备本地流水号")
    content_hash = Column(String(64), nullable=True, comment="业务内容指纹,用于区分重复与冲突")

    # ---- 版本链 ----
    version = Column(Integer, nullable=False, default=1, comment="同一业务事件的版本号,从1开始")
    action = Column(String(16), nullable=False, default="upsert", comment="upsert=登记/更正, revoke=撤销")
    root_id = Column(Integer, ForeignKey("feeding_records.id", ondelete="SET NULL"),
                     nullable=True, comment="逻辑记录ID,指向该事件首个版本行")
    replaces_id = Column(Integer, ForeignKey("feeding_records.id", ondelete="SET NULL"),
                         nullable=True, comment="本版本取代的上一版本行ID")

    # ---- 时间轴 ----
    occurred_at = Column(DateTime, nullable=True, comment="设备上事件发生时间")
    effective_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                          comment="生效时点:新版本从此刻起影响汇总")
    received_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                         comment="服务端接收时间")

    # ---- 审核 / 标记 ----
    review_status = Column(String(16), nullable=False, default="approved",
                           comment="approved=已生效, pending=待审, rejected=已驳回")
    pending_reason = Column(String(40), nullable=True, comment="待审原因: late_closed/late_window")
    conflict_flag = Column(Boolean, nullable=False, default=False, comment="同键异内容冲突标记")

    batch = relationship("Batch", back_populates="feeding_records", foreign_keys=[batch_id])


class FeedingConflict(Base):
    """同幂等键但业务内容不一致的到达记录(与单纯重复区分开)。"""
    __tablename__ = "feeding_conflicts"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(64), nullable=False, index=True)
    client_seq = Column(String(64), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True)
    existing_record_id = Column(Integer, ForeignKey("feeding_records.id", ondelete="SET NULL"),
                                nullable=True, comment="已存在的原记录")
    existing_hash = Column(String(64), nullable=True)
    incoming_hash = Column(String(64), nullable=True)
    incoming_payload = Column(Text, comment="冲突请求的完整内容(JSON)")
    resolved = Column(Boolean, nullable=False, default=False, comment="是否已人工处理")
    resolution = Column(String(20), nullable=True, comment="kept=保留原记录, applied=采用新内容")
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


class DailyReport(Base):
    """已签署的投喂日报。签署时对当时生效的数据做不可变快照,
    之后的更正/撤销不影响历史日报,可随时按快照重放。"""
    __tablename__ = "daily_reports"
    __table_args__ = (
        UniqueConstraint("batch_id", "business_date", name="uq_daily_report_batch_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    business_date = Column(Date, nullable=False, comment="业务日期")
    signed_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="签署时间")
    total_quantity = Column(Float, nullable=False, default=0)
    record_count = Column(Integer, nullable=False, default=0)
    # 签署时点:重放时按该时点的 effective_at 还原
    as_of = Column(DateTime, nullable=False)
    snapshot = Column(Text, nullable=False, comment="当时生效记录的完整快照(JSON)")
    created_at = Column(DateTime, default=datetime.utcnow)

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
