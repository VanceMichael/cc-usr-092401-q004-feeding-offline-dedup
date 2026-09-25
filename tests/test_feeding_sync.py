"""离线投喂重复计量修复:接收、查询、分析、日报链路端到端测试。

覆盖:
* 重复同步返回原记录、不重复累计(含服务重启后)
* 同键内容相同 vs 同键内容冲突结果不同
* 更正/撤销以新版本实现,按生效时点影响汇总
* 已签署日报可按当时版本重放
* 游标分页在并发补传下不重不漏
* 批次关闭后迟报进入待审;跨午夜补传按业务日期计入
"""
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

# 允许从仓库根目录直接 `python3 -m unittest discover -s tests`
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND))

# 必须在导入 app 前指定独立的测试数据库文件
_fd, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.remove(_DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import engine, Base, SessionLocal, run_lightweight_migrations  # noqa: E402
from app.models import FeedingRecord  # noqa: E402
from app.services import feeding_service  # noqa: E402
from sqlalchemy import text  # noqa: E402


def _today():
    return date.today()


class FeedingSyncTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            p = _DB_PATH + suffix
            if os.path.exists(p):
                os.remove(p)

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self._pond_seq = 0
        self.bid = self._create_batch("B-" + self.id().split(".")[-1])

    def _create_pond(self):
        self._pond_seq += 1
        r = self.client.post("/api/ponds/", json={
            "name": f"塘-{self.id().split('.')[-1]}-{self._pond_seq}",
            "area": 10, "water_depth": 1.5, "species": "草鱼",
        })
        return r.json()["id"]

    def _create_batch(self, number, status="active"):
        pid = self._create_pond()
        r = self.client.post("/api/batches/", json={
            "batch_number": number,
            "pond_id": pid,
            "species": "草鱼",
            "stocking_date": (_today() - timedelta(days=30)).isoformat(),
            "status": status,
        })
        self.assertEqual(r.status_code, 200)
        return r.json()["id"]

    def _sync(self, **overrides):
        payload = {
            "batch_id": self.bid,
            "feeding_date": _today().isoformat(),
            "feed_type": "配合饲料",
            "feed_quantity": 10.0,
            "device_id": "DEV-1",
            "client_seq": "1001",
        }
        payload.update(overrides)
        return self.client.post("/api/feeding-records/", json=payload)

    # ------------------------------------------------------------------
    def test_duplicate_sync_returns_original_and_counts_once(self):
        r1 = self._sync()
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r1.json()["outcome"], "created")
        original_id = r1.json()["record"]["id"]

        r2 = self._sync()  # 网络恢复后再次同步同一条
        self.assertEqual(r2.status_code, 200)
        body = r2.json()
        self.assertEqual(body["outcome"], "duplicate")
        self.assertEqual(body["record"]["id"], original_id)

        r3 = self._sync()  # 第三次仍然返回原记录
        self.assertEqual(r3.json()["record"]["id"], original_id)

        analysis = self.client.get(f"/api/analysis/cycle/{self.bid}/").json()
        self.assertEqual(analysis["feed_total"], 10.0)
        self.assertEqual(analysis["feeding_summary"]["feeding_count"], 1)

        page = self.client.get("/api/feeding-records/").json()
        self.assertEqual(len(page["items"]), 1)

    def test_same_key_different_content_is_conflict(self):
        r1 = self._sync(feed_quantity=10.0)
        rid = r1.json()["record"]["id"]

        conflict = self._sync(feed_quantity=12.5)  # 同键同版本,内容不同
        self.assertEqual(conflict.status_code, 409)
        body = conflict.json()
        self.assertEqual(body["outcome"], "conflict")
        self.assertIsNotNone(body["conflict_id"])
        self.assertEqual(body["record"]["id"], rid)  # 保留原记录

        # 冲突列表可查,原记录打冲突标记
        conflicts = self.client.get("/api/feeding-records/conflicts/list/").json()
        self.assertEqual(len(conflicts), 1)
        self.assertFalse(conflicts[0]["resolved"])
        original = self.client.get(f"/api/feeding-records/{rid}/").json()
        self.assertTrue(original["conflict_flag"])

        # 原内容仍是生效口径,未被覆盖
        analysis = self.client.get(f"/api/analysis/cycle/{self.bid}/").json()
        self.assertEqual(analysis["feed_total"], 10.0)

    def test_conflict_resolution_applied_creates_new_version(self):
        self._sync(feed_quantity=10.0)
        conflict = self._sync(feed_quantity=12.5)
        cid = conflict.json()["conflict_id"]

        resolved = self.client.post(
            f"/api/feeding-records/conflicts/{cid}/resolve/",
            json={"action": "resolve_conflict", "resolution": "applied"},
        )
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["resolution"], "applied")

        analysis = self.client.get(f"/api/analysis/cycle/{self.bid}/").json()
        self.assertEqual(analysis["feed_total"], 12.5)
        self.assertEqual(analysis["feeding_summary"]["feeding_count"], 1)

    def test_correction_is_new_version_and_effective_at_specified_time(self):
        r1 = self._sync(
            feed_quantity=10.0,
            effective_at="2026-09-20T08:00:00",
        )
        root = r1.json()["record"]["root_id"]

        corrected = self._sync(
            feed_quantity=14.0,
            version=2,
            effective_at="2026-09-21T08:00:00",
        )
        self.assertEqual(corrected.status_code, 201)
        self.assertEqual(corrected.json()["outcome"], "revised")
        self.assertEqual(corrected.json()["record"]["version"], 2)
        self.assertEqual(corrected.json()["record"]["root_id"], root)

        # 更正生效时点之前仍是旧值,之后是新值,全程只算一条逻辑记录
        before = self.client.get(
            f"/api/analysis/cycle/{self.bid}/?as_of=2026-09-21T07:59:59"
        ).json()
        after = self.client.get(
            f"/api/analysis/cycle/{self.bid}/?as_of=2026-09-21T08:00:00"
        ).json()
        self.assertEqual(before["feed_total"], 10.0)
        self.assertEqual(after["feed_total"], 14.0)
        self.assertEqual(after["feeding_summary"]["feeding_count"], 1)

    def test_revoke_is_versioned_and_time_bound(self):
        r1 = self._sync(
            feed_quantity=10.0,
            effective_at="2026-09-20T08:00:00",
        )
        rid = r1.json()["record"]["id"]

        rv = self.client.post(
            f"/api/feeding-records/{rid}/revoke/",
            json={"effective_at": "2026-09-22T00:00:00"},
        )
        self.assertEqual(rv.status_code, 201)
        self.assertEqual(rv.json()["outcome"], "revoked")
        self.assertEqual(rv.json()["record"]["action"], "revoke")
        self.assertEqual(rv.json()["record"]["version"], 2)

        before = self.client.get(
            f"/api/analysis/cycle/{self.bid}/?as_of=2026-09-21T23:59:59").json()
        after = self.client.get(
            f"/api/analysis/cycle/{self.bid}/?as_of=2026-09-22T00:00:00").json()
        self.assertEqual(before["feed_total"], 10.0)
        self.assertEqual(after["feed_total"], 0.0)

        # 列表中原版本被标记为已撤销
        page = self.client.get("/api/feeding-records/").json()
        statuses = {(i["version"], i["is_revoked"]) for i in page["items"]}
        self.assertIn((1, True), statuses)
        self.assertIn((2, True), statuses)

    def test_signed_daily_report_replays_as_of_signing_time(self):
        biz = _today()
        self._sync(feed_quantity=10.0,
                   effective_at=(datetime.combine(biz, datetime.min.time()) + timedelta(hours=8)).isoformat())
        rid = self.client.get("/api/feeding-records/").json()["items"][0]["id"]

        sign = self.client.post(f"/api/feeding-reports/{self.bid}/{biz.isoformat()}/")
        self.assertEqual(sign.status_code, 200)
        self.assertEqual(sign.json()["total_quantity"], 10.0)
        self.assertEqual(sign.json()["record_count"], 1)

        # 签署之后才撤销:已签日报重放不变
        self.client.post(f"/api/feeding-records/{rid}/revoke/")
        replay = self.client.get(f"/api/feeding-reports/{self.bid}/{biz.isoformat()}/")
        self.assertEqual(replay.json()["total_quantity"], 10.0)
        self.assertEqual(replay.json()["record_count"], 1)

        # 重复签署返回原报(幂等)
        sign_again = self.client.post(f"/api/feeding-reports/{self.bid}/{biz.isoformat()}/")
        self.assertEqual(sign_again.json()["id"], sign.json()["id"])

    def test_cross_midnight_backfill_counts_by_business_date(self):
        yesterday = (_today() - timedelta(days=1)).isoformat()
        r = self._sync(feeding_date=yesterday, client_seq="2002")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["record"]["review_status"], "approved")
        self.assertTrue(r.json()["record"]["is_late"])  # 上传晚于业务日期,标出迟报

        analysis = self.client.get(f"/api/analysis/cycle/{self.bid}/").json()
        self.assertEqual(analysis["feed_total"], 10.0)  # 仍按业务日期计入

    def test_late_arrival_after_batch_close_goes_to_pending(self):
        closed_bid = self._create_batch("B-CLOSED")
        cr = self.client.put(f"/api/batches/{closed_bid}/", json={"status": "closed"})
        self.assertEqual(cr.status_code, 200)

        r = self.client.post("/api/feeding-records/", json={
            "batch_id": closed_bid,
            "feeding_date": _today().isoformat(),
            "feed_type": "配合饲料",
            "feed_quantity": 88.0,
            "device_id": "DEV-9", "client_seq": "1",
        })
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["outcome"], "pending")
        self.assertEqual(r.json()["record"]["review_status"], "pending")
        self.assertEqual(r.json()["record"]["pending_reason"], "late_closed")

        # 待审不计汇总
        analysis = self.client.get(f"/api/analysis/cycle/{closed_bid}/").json()
        self.assertEqual(analysis["feed_total"], 0.0)

        # 待审队列可见
        pending = self.client.get("/api/feeding-records/?review_status=pending").json()
        self.assertEqual(len(pending["items"]), 1)

        # 批准后才计入;重复同步仍幂等
        pid = r.json()["record"]["id"]
        approve = self.client.post(
            f"/api/feeding-records/{pid}/review/", json={"action": "approve"})
        self.assertEqual(approve.json()["review_status"], "approved")
        dup = self.client.post("/api/feeding-records/", json={
            "batch_id": closed_bid, "feeding_date": _today().isoformat(),
            "feed_type": "配合饲料", "feed_quantity": 88.0,
            "device_id": "DEV-9", "client_seq": "1",
        })
        self.assertEqual(dup.json()["outcome"], "duplicate")
        analysis2 = self.client.get(f"/api/analysis/cycle/{closed_bid}/").json()
        self.assertEqual(analysis2["feed_total"], 88.0)

    def test_late_window_beyond_days_goes_to_pending(self):
        old_date = (_today() - timedelta(days=feeding_service.LATE_WINDOW_DAYS + 2)).isoformat()
        r = self._sync(feeding_date=old_date, client_seq="3003")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["record"]["pending_reason"], "late_window")

    def test_cursor_pagination_stable_under_concurrent_inserts(self):
        # 10 条基础记录,业务日期铺开
        for i in range(10):
            self._sync(
                feeding_date=(_today() - timedelta(days=10 - i)).isoformat(),
                client_seq=str(5000 + i),
            )

        first = self.client.get("/api/feeding-records/?limit=4").json()
        self.assertTrue(first["has_more"])
        seen = [i["id"] for i in first["items"]]
        cursor = first["next_cursor"]

        # 翻页过程中并发补传:业务日期跨越未来数天,验证按接收轴翻页不重不漏
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = []
            for i in range(10):
                futures.append(pool.submit(
                    self._sync,
                    feeding_date=(_today() + timedelta(days=i + 1)).isoformat(),
                    client_seq=str(6000 + i),
                ))
            for f in futures:
                self.assertIn(f.result().status_code, (201, 200))

        # 沿旧游标继续翻完,不重不漏
        while cursor:
            page = self.client.get(f"/api/feeding-records/?limit=4&cursor={cursor}").json()
            ids = [i["id"] for i in page["items"]]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertFalse(set(ids) & set(seen))
            seen.extend(ids)
            cursor = page["next_cursor"]

        all_records = self.client.get("/api/feeding-records/?limit=200").json()
        self.assertEqual(len(seen), len(all_records["items"]))
        self.assertEqual(set(seen), {i["id"] for i in all_records["items"]})

    def test_concurrent_same_key_sync_creates_exactly_one_record(self):
        def worker():
            db = SessionLocal()
            try:
                return feeding_service.ingest_feeding(db, {
                    "batch_id": self.bid,
                    "feeding_date": _today(),
                    "feed_type": "配合饲料",
                    "feed_quantity": 10.0,
                    "device_id": "DEV-RACE",
                    "client_seq": "777",
                }).outcome
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: worker(), range(8)))

        self.assertEqual(outcomes.count("created"), 1)
        self.assertEqual(outcomes.count("duplicate"), 7)

        db = SessionLocal()
        count = db.query(FeedingRecord).filter(
            FeedingRecord.device_id == "DEV-RACE",
            FeedingRecord.client_seq == "777",
        ).count()
        db.close()
        self.assertEqual(count, 1)

    def test_idempotency_holds_after_restart(self):
        r1 = self._sync(client_seq="8888")
        rid = r1.json()["record"]["id"]

        # 模拟服务重启:释放连接池、重放启动迁移/建表,再来一次同键同步
        engine.dispose()
        run_lightweight_migrations()
        Base.metadata.create_all(bind=engine)

        fresh = TestClient(app)
        r2 = fresh.post("/api/feeding-records/", json={
            "batch_id": self.bid,
            "feeding_date": _today().isoformat(),
            "feed_type": "配合饲料",
            "feed_quantity": 10.0,
            "device_id": "DEV-1", "client_seq": "8888",
        })
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["outcome"], "duplicate")
        self.assertEqual(r2.json()["record"]["id"], rid)

    def test_traceability_carries_flags(self):
        yesterday = (_today() - timedelta(days=1)).isoformat()
        self._sync(feeding_date=yesterday, client_seq="9001")
        self._sync(feeding_date=yesterday, feed_quantity=99.0, client_seq="9001")  # 冲突

        trace = self.client.get(f"/api/analysis/traceability/{self.bid}/").json()
        feeding = trace["feeding_records"]
        self.assertEqual(len(feeding), 1)  # 生效口径仍是一条
        self.assertTrue(feeding[0]["is_late"])
        self.assertTrue(feeding[0]["conflict_flag"])


class LegacyMigrationTestCase(unittest.TestCase):
    """旧版数据库(无任何幂等/版本列)启动迁移后仍可工作。"""

    def test_migration_adds_columns_and_backfills_versions(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE feeding_records"))
            conn.execute(text(
                "CREATE TABLE feeding_records ("
                "id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL, "
                "feeding_date DATE NOT NULL, feed_type VARCHAR NOT NULL, "
                "feed_quantity FLOAT NOT NULL, feeding_time VARCHAR, "
                "weather VARCHAR, water_temperature FLOAT, notes TEXT, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
            ))
            conn.execute(text(
                "INSERT INTO feeding_records (batch_id, feeding_date, feed_type, feed_quantity) "
                "VALUES (1, '2026-09-01', '旧料', 3.5)"
            ))

        run_lightweight_migrations()

        db = SessionLocal()
        try:
            row = db.query(FeedingRecord).one()
            self.assertEqual(row.version, 1)
            self.assertEqual(row.review_status, "approved")
            self.assertEqual(row.root_id, row.id)  # 历史行自成逻辑根
            self.assertIsNotNone(row.effective_at)
            self.assertIsNotNone(row.received_at)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
