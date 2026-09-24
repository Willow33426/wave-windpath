"""SQLite 저장·조회 테스트."""
import importlib.util
import os
import pathlib
import sqlite3
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


def _fallback_conn(tmp, name="fallback.db", stations=1):
    """키 없이 한 번 수집한 뒤의 연결을 돌려준다."""
    from app.collector import collect_once
    from app.config import STATIONS, Settings

    db_path = pathlib.Path(tmp) / name
    settings = Settings(service_key="", db_path=db_path, stations=STATIONS[:stations])
    conn = db.connect(db_path)
    collect_once(settings, conn)
    return conn


class FixtureRebaseTest(unittest.TestCase):
    """fixture의 고정 시각이 실행 시각으로 옮겨지는지."""

    def test_fallback_records_stay_in_recent_window(self):
        # 고정 시각을 그대로 저장하면 며칠 뒤 최근 24시간 조회가 비어 시연이 깨진다.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            conn = _fallback_conn(tmp, "rebase.db")
            rows = db.query_measurements(conn, station="suncheon",
                                         since=datetime.now(KST) - timedelta(hours=24))
            self.assertGreater(len(rows), 0)
            conn.close()

    def test_rebase_keeps_intervals(self):
        from app.collector import _rebase

        base = datetime(2020, 1, 1, tzinfo=KST)
        items = [Record("kma", "suncheon", "forecast", base, base + timedelta(hours=i),
                        "wind_speed", 3.0, "m/s") for i in range(3)]
        now = datetime(2026, 9, 24, 15, 30, tzinfo=KST)
        moved = _rebase(items, now)

        self.assertEqual(moved[0].base_time, now.replace(minute=0))
        self.assertEqual([r.target_time - r.base_time for r in moved],
                         [r.target_time - r.base_time for r in items])

    def test_rebase_of_empty_list(self):
        from app.collector import _rebase
        self.assertEqual(_rebase([], datetime.now(KST)), [])


class NowcastCollectedTest(unittest.TestCase):
    """초단기실황이 수집 루프에 실제로 포함되는지."""

    def test_kma_observation_is_stored(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            conn = _fallback_conn(tmp, "nowcast.db")
            rows = db.query_measurements(conn, station="suncheon", kind="observation",
                                         metric="wind_speed")
            self.assertTrue(any(r["source"] == "kma" for r in rows))
            conn.close()


class DbPathTest(unittest.TestCase):
    """상대 DB_PATH는 작업 디렉터리가 아니라 프로젝트 루트 기준이어야 한다."""

    def test_relative_path_resolves_under_root(self):
        from app.config import ROOT_DIR, load_settings

        previous = os.environ.get("DB_PATH")
        os.environ["DB_PATH"] = "data/wave.db"
        try:
            self.assertEqual(load_settings().db_path, ROOT_DIR / "data" / "wave.db")
        finally:
            if previous is None:
                os.environ.pop("DB_PATH", None)
            else:
                os.environ["DB_PATH"] = previous

    def test_absolute_path_is_kept(self):
        from app.config import load_settings

        previous = os.environ.get("DB_PATH")
        absolute = str(pathlib.Path(tempfile.gettempdir()) / "wave-abs.db")
        os.environ["DB_PATH"] = absolute
        try:
            self.assertEqual(str(load_settings().db_path), absolute)
        finally:
            if previous is None:
                os.environ.pop("DB_PATH", None)
            else:
                os.environ["DB_PATH"] = previous


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "fastapi 미설치 (CI에서 실행)")
class HealthResilienceTest(unittest.TestCase):
    """DB가 깨져도 상태 응답 자체는 나와야 한다."""

    def test_health_returns_error_status_instead_of_raising(self):
        import asyncio

        from app import main as app_main

        class BrokenConn:
            def execute(self, *args, **kwargs):
                raise sqlite3.DatabaseError("깨진 DB")

        previous = getattr(app_main.app.state, "conn", None)
        app_main.app.state.conn = BrokenConn()
        try:
            result = asyncio.run(app_main.health())
        finally:
            app_main.app.state.conn = previous

        self.assertEqual(result["db"], "error")
        self.assertEqual(result["status"], "degraded")
        self.assertFalse(result["is_fallback"])


if __name__ == "__main__":
    unittest.main()
