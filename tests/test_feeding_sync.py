"""投喂离线同步：幂等、冲突、版本、游标、迟报、日报重放、重启、迁移。"""
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_tmpdir = tempfile.mkdtemp(prefix="aqua_test_")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmpdir}/test.db")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402

from app.main import app  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Pond, Batch, FeedingRecord, FeedingSyncKey, DailyFeedingReport,
)
from app.services import feeding_service as svc  # noqa: E402
from app.schemas import FeedingRecordCreate  # noqa: E402


client = TestClient(app)


def make_batch(status="active"):
    with SessionLocal() as db:
        n = make_batch.counter
        make_batch.counter += 1
        pond = Pond(name=f"塘{n}", area=10, water_depth=2, species="草鱼")
        db.add(pond)
        db.flush()
        batch = Batch(
            batch_number=f"B{n}", pond_id=pond.id, species="草鱼",
            stocking_date=date(2026, 9, 1), status=status,
        )
        db.add(batch)
        db.commit()
        return batch.id


make_batch.counter = 0


def sync_payload(batch_id, qty, device="dev1", seq=1, fdate=None, **extra):
    data = {
        "batch_id": batch_id,
        "feeding_date": (fdate or date(2026, 9, 10)).isoformat(),
        "feed_type": "配合饲料",
        "feed_quantity": qty,
        "device_id": device,
        "device_seq": seq,
    }
    data.update(extra)
    return data


class FeedingSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bid = make_batch()

    def test_01_duplicate_sync_returns_same_record(self):
        p = sync_payload(self.bid, 5.0)
        r1 = client.post("/api/feeding-records/", json=p)
        r2 = client.post("/api/feeding-records/", json=p)
        r3 = client.post("/api/feeding-records/", json=p)
        self.assertEqual(r1.json()["result"], "created")
        self.assertEqual(r2.json()["result"], "duplicate")
        self.assertEqual(r3.json()["result"], "duplicate")
        rid = r1.json()["record"]["id"]
        self.assertEqual(r2.json()["record"]["id"], rid)
        self.assertEqual(r3.json()["record"]["id"], rid)

        totals = svc.daily_totals(SessionLocal(), batch_id=self.bid,
                                  business_date=date(2026, 9, 10))
        self.assertEqual(totals["feeding_count"], 1)
        self.assertEqual(totals["total_quantity"], 5.0)

    def test_02_same_key_conflicting_content_is_distinct_outcome(self):
        p1 = sync_payload(self.bid, 5.0, device="devC", seq=1)
        p2 = sync_payload(self.bid, 7.5, device="devC", seq=1)  # 同键不同量
        p3 = sync_payload(self.bid, 7.5, device="devC", seq=1)  # 再次重放冲突来报
        r1 = client.post("/api/feeding-records/", json=p1).json()
        r2 = client.post("/api/feeding-records/", json=p2).json()
        r3 = client.post("/api/feeding-records/", json=p2).json()
        self.assertEqual(r1["result"], "created")
        self.assertEqual(r2["result"], "conflict")
        self.assertIsNotNone(r2["conflict_id"])
        # 冲突不产生新记录，且相同冲突内容只挂一张 open 工单
        self.assertEqual(r3["result"], "conflict")
        self.assertEqual(r3["conflict_id"], r2["conflict_id"])
        self.assertEqual(r2["record"]["id"], r1["record"]["id"])

        conflicts = client.get("/api/feeding-records/conflicts/").json()
        open_conf = [c for c in conflicts if c["id"] == r2["conflict_id"]]
        self.assertEqual(len(open_conf), 1)
        self.assertEqual(open_conf[0]["payload"]["feed_quantity"], 7.5)

    def test_03_resolve_conflict_accept_creates_correction_version(self):
        bid = make_batch()
        p1 = sync_payload(bid, 5.0, device="devR", seq=1)
        p2 = sync_payload(bid, 9.0, device="devR", seq=1)
        r1 = client.post("/api/feeding-records/", json=p1).json()
        r2 = client.post("/api/feeding-records/", json=p2).json()
        self.assertEqual(r2["result"], "conflict")
        res = client.post(
            f"/api/feeding-records/conflicts/{r2['conflict_id']}/resolve/?action=accept"
        ).json()
        new_id = res["new_record_id"]
        self.assertNotEqual(new_id, r1["record"]["id"])
        totals = svc.daily_totals(SessionLocal(), batch_id=bid,
                                  business_date=date(2026, 9, 10))
        # 冲突来报被采纳后，本链按 9.0 计（不是 5+9 重复累计）
        self.assertEqual(totals["total_quantity"], 9.0)
        self.assertEqual(totals["feeding_count"], 1)

    def test_04_resolve_conflict_keep_changes_nothing(self):
        p1 = sync_payload(self.bid, 3.0, device="devK", seq=1)
        p2 = sync_payload(self.bid, 30.0, device="devK", seq=1)
        r1 = client.post("/api/feeding-records/", json=p1).json()
        r2 = client.post("/api/feeding-records/", json=p2).json()
        client.post(f"/api/feeding-records/conflicts/{r2['conflict_id']}/resolve/?action=keep")
        totals = svc.daily_totals(SessionLocal(), batch_id=self.bid,
                                  business_date=date(2026, 9, 10))
        # 维持 3.0（另两条链 5.0/9.0 已在其他用例，本用例只看本链）
        with SessionLocal() as db:
            rec = db.query(FeedingRecord).filter_by(id=r1["record"]["id"]).first()
            self.assertTrue(rec.is_current)
            self.assertEqual(rec.feed_quantity, 3.0)

    def test_05_correction_new_version_takes_effect_at_specified_time(self):
        bid = make_batch()
        r1 = client.post("/api/feeding-records/",
                         json=sync_payload(bid, 10.0, device="devV", seq=1)).json()
        future = datetime.utcnow() + timedelta(hours=2)
        rev = sync_payload(bid, 12.0, device="devV", seq=1,
                           sync_mode="revision",
                           effective_at=future.isoformat())
        r2 = client.post("/api/feeding-records/", json=rev).json()
        self.assertEqual(r2["result"], "revised")
        self.assertEqual(r2["record"]["version"], 2)
        self.assertEqual(r2["record"]["supersedes_id"], r1["record"]["id"])

        db = SessionLocal()
        # 现在：仍按旧版本 10.0
        now_rows = svc.effective_records(db, batch_id=bid, as_of=datetime.utcnow())
        self.assertEqual([r.feed_quantity for r in now_rows], [10.0])
        # 生效时点之后：新版本 12.0
        later_rows = svc.effective_records(db, batch_id=bid, as_of=future + timedelta(minutes=1))
        self.assertEqual([r.feed_quantity for r in later_rows], [12.0])
        db.close()

        # 更正幂等：相同内容再来一次不追加版本
        rev_again = sync_payload(bid, 12.0, device="devV", seq=1,
                                 sync_mode="revision")
        r3 = client.post("/api/feeding-records/", json=rev_again).json()
        self.assertEqual(r3["record"]["version"], 2)

    def test_06_revocation_removes_from_totals_after_effective_time(self):
        bid = make_batch()
        r1 = client.post("/api/feeding-records/",
                         json=sync_payload(bid, 8.0, device="devX", seq=1,
                                           fdate=date(2026, 9, 11))).json()
        future = datetime.utcnow() + timedelta(hours=1)
        rev = sync_payload(bid, 8.0, device="devX", seq=1,
                           sync_mode="revocation",
                           effective_at=future.isoformat())
        client.post("/api/feeding-records/", json=rev)

        db = SessionLocal()
        before = svc.daily_totals(db, batch_id=bid,
                                  business_date=date(2026, 9, 11),
                                  as_of=datetime.utcnow())
        after = svc.daily_totals(db, batch_id=bid,
                                 business_date=date(2026, 9, 11),
                                 as_of=future + timedelta(minutes=1))
        self.assertEqual(before["total_quantity"], 8.0)
        self.assertEqual(after["total_quantity"], 0)
        self.assertEqual(after["feeding_count"], 0)
        db.close()

    def test_07_cursor_pagination_stable_under_concurrent_backfill(self):
        bid = make_batch()
        n = 60

        def worker(start):
            db = SessionLocal()
            try:
                for i in range(start, n, 4):
                    svc.sync_feeding(db, FeedingRecordCreate(
                        batch_id=bid, feeding_date=date(2026, 9, 12),
                        feed_type="料", feed_quantity=1.0,
                        device_id=f"devp{i % 7}", device_seq=1000 + i,
                    ))
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(s,)) for s in range(4)]
        walks_with_dupes = []

        def paginate_during():
            db = SessionLocal()
            try:
                cursor, seen = None, []
                for _ in range(50):
                    page = svc.paginate_records(db, limit=7, cursor=cursor,
                                                batch_id=bid)
                    ids = [r.id for r in page["items"]]
                    if len(ids) != len(set(ids)):
                        walks_with_dupes.append(True)
                    seen.extend(ids)
                    cursor = page["next_cursor"]
                    if not cursor:
                        break
                if len(seen) != len(set(seen)):
                    walks_with_dupes.append(True)
            finally:
                db.close()

        for t in threads:
            t.start()
        pagers = [threading.Thread(target=paginate_during) for _ in range(3)]
        for t in pagers:
            t.start()
        for t in threads + pagers:
            t.join()
        self.assertEqual(walks_with_dupes, [])

        # 写入全部完成后：完整翻页不重不漏
        db = SessionLocal()
        cursor, seen = None, []
        while True:
            page = svc.paginate_records(db, limit=11, cursor=cursor, batch_id=bid)
            seen.extend(r.id for r in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), n)
        self.assertEqual(len(set(seen)), n)
        db.close()

    def test_08_bad_cursor_rejected(self):
        r = client.get("/api/feeding-records/?cursor=not-base64!!")
        self.assertEqual(r.status_code, 400)

    def test_09_late_report_into_closed_batch_is_pending_until_approved(self):
        bid = make_batch(status="closed")
        r = client.post("/api/feeding-records/",
                        json=sync_payload(bid, 6.0, device="devL", seq=1,
                                          fdate=date(2026, 9, 13))).json()
        self.assertEqual(r["result"], "pending")
        self.assertEqual(r["record"]["review_status"], "pending")
        self.assertTrue(r["record"]["is_late"])

        db = SessionLocal()
        totals = svc.daily_totals(db, batch_id=bid,
                                  business_date=date(2026, 9, 13))
        self.assertEqual(totals["total_quantity"], 0)
        self.assertEqual(totals["pending_count"], 1)
        db.close()

        # 批准后才计入
        ap = client.post(f"/api/feeding-records/{r['record']['id']}/approve/").json()
        self.assertEqual(ap["record"]["review_status"], "approved")
        db = SessionLocal()
        totals = svc.daily_totals(db, batch_id=bid,
                                  business_date=date(2026, 9, 13))
        self.assertEqual(totals["total_quantity"], 6.0)
        db.close()

        # 驳回的迟报始终不计
        r2 = client.post("/api/feeding-records/",
                         json=sync_payload(bid, 99.0, device="devL", seq=2,
                                           fdate=date(2026, 9, 13))).json()
        client.post(f"/api/feeding-records/{r2['record']['id']}/reject/")
        db = SessionLocal()
        totals = svc.daily_totals(db, batch_id=bid,
                                  business_date=date(2026, 9, 13))
        self.assertEqual(totals["total_quantity"], 6.0)
        db.close()

    def test_10_cross_midnight_business_date_differs_from_upload_day(self):
        bid = make_batch()
        yesterday = date(2026, 9, 9)
        r = client.post("/api/feeding-records/",
                        json=sync_payload(bid, 4.0, device="devM", seq=1,
                                          fdate=yesterday)).json()
        self.assertEqual(r["result"], "created")
        db = SessionLocal()
        self.assertEqual(svc.daily_totals(db, batch_id=bid,
                                          business_date=yesterday)["total_quantity"], 4.0)
        self.assertEqual(svc.daily_totals(db, batch_id=bid,
                                          business_date=date(2026, 9, 10))["total_quantity"], 0)
        db.close()

    def test_11_signed_daily_report_replays_at_then_version(self):
        bid = make_batch()
        d = date(2026, 9, 14)
        client.post("/api/feeding-records/",
                    json=sync_payload(bid, 10.0, device="devS", seq=1, fdate=d))
        signed = client.post(f"/api/feeding-daily-reports/sign/?batch_id={bid}&business_date={d}").json()
        self.assertEqual(signed["total_quantity"], 10.0)
        self.assertFalse(signed["changed_since_sign"])

        # 重复签署被拒
        again = client.post(f"/api/feeding-daily-reports/sign/?batch_id={bid}&business_date={d}")
        self.assertEqual(again.status_code, 409)

        # 签署后更正为 15.0（立即生效）
        rev = sync_payload(bid, 15.0, device="devS", seq=1, fdate=d,
                           sync_mode="revision")
        client.post("/api/feeding-records/", json=rev)

        # 当前汇总已变
        db = SessionLocal()
        self.assertEqual(svc.daily_totals(db, batch_id=bid, business_date=d)["total_quantity"], 15.0)
        db.close()

        # 重放已签日报：仍是签署当时的 10.0，但标注签署后有变化
        replay = client.get(f"/api/feeding-daily-reports/{signed['id']}/replay/").json()
        self.assertEqual(replay["total_quantity"], 10.0)
        self.assertTrue(replay["changed_since_sign"])
        self.assertEqual(replay["current_totals"]["total_quantity"], 15.0)

        # 分析接口支持按签署时点重放
        cycle_then = client.get(
            f"/api/analysis/cycle/{bid}/?as_of={signed['as_of']}"
        ).json()
        self.assertEqual(cycle_then["feed_total"], 10.0)
        cycle_now = client.get(f"/api/analysis/cycle/{bid}/").json()
        self.assertEqual(cycle_now["feed_total"], 15.0)

    def test_13_late_correction_pending_then_reject_restores_previous(self):
        # 批次在活跃期已收到 6.0，随后关闭
        bid = make_batch()
        client.post("/api/feeding-records/",
                    json=sync_payload(bid, 6.0, device="devLC", seq=1,
                                      fdate=date(2026, 9, 16)))
        with SessionLocal() as db:
            b = db.query(Batch).filter_by(id=bid).first()
            b.status = "closed"
            db.commit()

        # 关闭后发来更正 8.0：新版本待审，旧值仍计入
        rev = sync_payload(bid, 8.0, device="devLC", seq=1,
                           fdate=date(2026, 9, 16), sync_mode="revision")
        r = client.post("/api/feeding-records/", json=rev).json()
        self.assertEqual(r["record"]["review_status"], "pending")
        db = SessionLocal()
        self.assertEqual(svc.daily_totals(db, batch_id=bid,
                                          business_date=date(2026, 9, 16))["total_quantity"], 6.0)
        pending_id = r["record"]["id"]
        db.close()

        # 驳回更正：恢复旧版本为当前，仍按 6.0
        client.post(f"/api/feeding-records/{pending_id}/reject/")
        db = SessionLocal()
        self.assertEqual(svc.daily_totals(db, batch_id=bid,
                                          business_date=date(2026, 9, 16))["total_quantity"], 6.0)
        current = db.query(FeedingRecord).filter(
            FeedingRecord.batch_id == bid,
            FeedingRecord.is_current.is_(True),
        ).all()
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].feed_quantity, 6.0)
        self.assertEqual(current[0].version, 1)
        db.close()

    def test_12_idempotency_survives_restart(self):
        bid = make_batch()
        p = sync_payload(bid, 2.0, device="devReboot", seq=77,
                         fdate=date(2026, 9, 15))
        r1 = client.post("/api/feeding-records/", json=p).json()

        # 模拟服务重启：释放连接池并重新执行建表/迁移（台账在磁盘上）
        engine.dispose()
        from app import main as main_mod
        main_mod.Base.metadata.create_all(bind=engine)
        main_mod.run_startup_migrations(engine)

        with TestClient(app) as c2:
            r2 = c2.post("/api/feeding-records/", json=p).json()
        self.assertEqual(r2["result"], "duplicate")
        self.assertEqual(r2["record"]["id"], r1["record"]["id"])


class StartupMigrationTests(unittest.TestCase):
    def test_old_schema_database_gets_version_columns(self):
        old_db_path = Path(_tmpdir) / "old.db"
        eng = create_engine(
            f"sqlite:///{old_db_path}",
            connect_args={"check_same_thread": False},
        )
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE feeding_records ("
                "id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL, "
                "feeding_date DATE NOT NULL, feed_type VARCHAR(100) NOT NULL, "
                "feed_quantity FLOAT NOT NULL, feeding_time VARCHAR(20), "
                "weather VARCHAR(50), water_temperature FLOAT, notes TEXT, "
                "created_at DATETIME)"
            ))
            conn.execute(text(
                "INSERT INTO feeding_records (id, batch_id, feeding_date, feed_type, "
                "feed_quantity, created_at) VALUES "
                "(1, 1, '2026-09-01', '料', 3.5, '2026-09-01 00:00:00')"
            ))
            # 其余被外键/查询引用的旧表也补一个空壳
            for t in ("ponds", "batches", "stocking_records", "water_quality_records",
                      "medication_records", "cost_records", "harvest_sales"):
                conn.execute(text(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY)"))

        from app.migrations import run_startup_migrations
        # create_all 会补建新表，但跳过已存在的 feeding_records
        import app.database as database
        old_base = database.Base
        old_base.metadata.create_all(bind=eng)
        run_startup_migrations(eng)

        cols = {c["name"] for c in inspect(eng).get_columns("feeding_records")}
        for needed in ("device_id", "device_seq", "logical_id", "version",
                       "is_current", "status", "effective_at", "review_status",
                       "is_late", "content_hash"):
            self.assertIn(needed, cols)
        with eng.begin() as conn:
            row = conn.execute(text(
                "SELECT logical_id, version, is_current, status, review_status, "
                "effective_at FROM feeding_records WHERE id=1"
            )).fetchone()
        self.assertEqual(row[0], 1)          # 旧记录自成版本链根
        self.assertEqual(row[1], 1)
        self.assertEqual(row[2], 1)
        self.assertEqual(row[3], "active")
        self.assertEqual(row[4], "approved")
        self.assertIsNotNone(row[5])
        eng.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
