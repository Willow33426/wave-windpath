"""SQLite 저장·조회 테스트."""
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.sources.parse import KST, Record  # noqa: E402


def record(metric="pm25", value=23.0, hours=0, station="suncheon", kind="observation"):
    t = datetime(2026, 9, 24, 15, 0, tzinfo=KST) + timedelta(hours=hours)
    return Record("airkorea", station, kind, t, t, metric, value, "ug/m3")


class DbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # 윈도우는 WAL 파일이 잠깐 남는다
        self.conn = db.connect(pathlib.Path(self.tmp.name) / "test.db")
        self.now = datetime(2026, 9, 24, 15, 20, tzinfo=KST)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_upsert_is_idempotent(self):
        db.upsert_records(self.conn, [record()], self.now)
        db.upsert_records(self.conn, [record()], self.now)
        rows = db.query_measurements(self.conn)
        self.assertEqual(len(rows), 1)

    def test_upsert_updates_value(self):
        db.upsert_records(self.conn, [record(value=23.0)], self.now)
        db.upsert_records(self.conn, [record(value=31.0)], self.now)
        row = db.latest_observation(self.conn, "suncheon", "pm25")
        self.assertEqual(row["value"], 31.0)

    def test_latest_observation_picks_newest(self):
        db.upsert_records(self.conn, [record(hours=0, value=20.0), record(hours=3, value=28.0)], self.now)
        row = db.latest_observation(self.conn, "suncheon", "pm25")
        self.assertEqual(row["value"], 28.0)

    def test_query_filters(self):
        db.upsert_records(self.conn, [
            record(metric="pm25", value=20.0),
            record(metric="pm10", value=40.0),
            record(metric="pm25", value=18.0, station="gwangyang"),
            record(metric="wind_direction", value=115.0, kind="forecast", hours=1),
        ], self.now)
        self.assertEqual(len(db.query_measurements(self.conn, station="suncheon")), 3)
        self.assertEqual(len(db.query_measurements(self.conn, metric="pm25")), 2)
        self.assertEqual(len(db.query_measurements(self.conn, kind="forecast")), 1)
        since = datetime(2026, 9, 24, 15, 30, tzinfo=KST)
        self.assertEqual(len(db.query_measurements(self.conn, since=since)), 1)

    def test_meta_round_trip(self):
        self.assertIsNone(db.get_meta(self.conn, "last_collected_at"))
        db.set_meta(self.conn, "last_collected_at", self.now.isoformat())
        db.set_meta(self.conn, "last_collected_at", "2026-09-24T16:00:00+09:00")
        self.assertEqual(db.get_meta(self.conn, "last_collected_at"), "2026-09-24T16:00:00+09:00")
        self.assertEqual(db.get_meta(self.conn, "없는키", "0"), "0")


class CollectorFallbackTest(unittest.TestCase):
    """키가 없으면 fixture로 폴백하고 is_fallback이 남는지."""

    def test_collect_once_uses_fixture_without_key(self):
        from app.collector import META_LAST_FALLBACK, collect_once
        from app.config import Settings, STATIONS

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = pathlib.Path(tmp) / "fallback.db"
            settings = Settings(service_key="", db_path=db_path, stations=STATIONS[:1])
            conn = db.connect(db_path)
            summary = collect_once(settings, conn)
            self.assertTrue(summary["is_fallback"])
            self.assertGreater(summary["records"], 0)
            self.assertEqual(db.get_meta(conn, META_LAST_FALLBACK), "1")
            self.assertIsNotNone(db.latest_observation(conn, "suncheon", "pm25"))
            conn.close()


if __name__ == "__main__":
    unittest.main()
