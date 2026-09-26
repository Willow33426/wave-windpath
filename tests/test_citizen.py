"""시민 모드 API 응답 조립 테스트."""
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.citizen import build_citizen_forecast  # noqa: E402
from app.collector import META_LAST_FALLBACK  # noqa: E402
from app.config import Settings  # noqa: E402
from app.sources.parse import KST, Record  # noqa: E402


class CitizenForecastTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = db.connect(pathlib.Path(self.tmp.name) / "citizen.db")
        self.now = datetime(2026, 9, 26, 12, 20, tzinfo=KST)
        observed = self.now.replace(minute=0, second=0, microsecond=0)
        records = [
            Record("airkorea", "suncheon", "observation", observed, observed, "pm25", 23, "ug/m3"),
            Record("airkorea", "suncheon", "observation", observed, observed, "pm10", 38, "ug/m3"),
            Record("airkorea", "gwangyang", "observation", observed, observed, "pm25", 41, "ug/m3"),
            Record("kma", "suncheon", "observation", observed, observed, "wind_direction", 115, "deg"),
            Record("kma", "suncheon", "observation", observed, observed, "wind_speed", 3.4, "m/s"),
            Record("kma", "suncheon", "observation", observed, observed, "temperature", 24.6, "C"),
        ]
        for hour in range(1, 13):
            target = observed + timedelta(hours=hour)
            records.extend([
                Record("kma", "suncheon", "forecast", observed, target, "wind_direction", 112, "deg"),
                Record("kma", "suncheon", "forecast", observed, target, "wind_speed", 3.2, "m/s"),
            ])
        db.upsert_records(self.conn, records, self.now)
        db.set_meta(self.conn, META_LAST_FALLBACK, "0")
        self.settings = Settings(db_path=pathlib.Path(self.tmp.name) / "citizen.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_live_response_matches_frontend_contract(self):
        result = build_citizen_forecast(self.conn, self.settings, now=self.now)

        self.assertEqual(result["location"]["id"], "suncheon")
        self.assertFalse(result["is_fallback"])
        self.assertEqual(result["current"]["pm25"], 23)
        self.assertEqual(result["current"]["wind_direction_label"], "동남동")
        self.assertEqual(len(result["forecast"]), 12)
        self.assertTrue(all("industrial_influence" in item for item in result["forecast"]))
        self.assertTrue(all(item["is_fallback"] is False for item in result["forecast"]))
        self.assertEqual({source["provider"] for source in result["data_sources"]}, {
            "기상청", "한국환경공단 에어코리아",
        })

    def test_old_fixture_does_not_mark_latest_live_collection_as_fallback(self):
        old = self.now - timedelta(hours=2)
        db.upsert_records(self.conn, [
            Record("airkorea", "yeosu", "observation", old, old, "pm25", 19, "ug/m3", "fixture")
        ], old)

        result = build_citizen_forecast(self.conn, self.settings, now=self.now)

        self.assertFalse(result["is_fallback"])

    def test_live_collection_ignores_leftover_fixture_rows(self):
        # 폴백 때 들어온 fixture가 실측보다 늦은 시각으로 남아 있어도,
        # 가장 최근 수집이 실데이터면 현재값·예측에 쓰지 않는다.
        leftover = self.now.replace(minute=15)
        db.upsert_records(self.conn, [
            Record("airkorea", "suncheon", "observation", leftover, leftover, "pm25", 99, "ug/m3", "fixture"),
        ], self.now)

        live = build_citizen_forecast(self.conn, self.settings, now=self.now)
        self.assertFalse(live["is_fallback"])
        self.assertEqual(live["current"]["pm25"], 23)
        self.assertTrue(all(item["pm25_predicted"] < 60 for item in live["forecast"]))

        # 수집이 폴백 중이면 fixture를 쓰되 화면에 폴백으로 알린다.
        db.set_meta(self.conn, META_LAST_FALLBACK, "1")
        fallback = build_citizen_forecast(self.conn, self.settings, now=self.now)
        self.assertTrue(fallback["is_fallback"])
        self.assertEqual(fallback["current"]["pm25"], 99)

    def test_sensitive_mode_closes_windows_on_industrial_wind(self):
        # setUp의 현재 바람은 115°(산단 방향) 3.4m/s
        general = build_citizen_forecast(self.conn, self.settings, now=self.now)
        sensitive = build_citizen_forecast(self.conn, self.settings, now=self.now, sensitive=True)
        self.assertEqual(general["recommendation"]["ventilation"]["status"], "caution")
        self.assertEqual(sensitive["recommendation"]["ventilation"]["status"], "avoid")
        self.assertEqual(sensitive["profile"], "sensitive")
        self.assertEqual(general["profile"], "general")

    def test_sensitive_mode_waits_for_good_hours_when_fair(self):
        observed = self.now.replace(minute=0, second=0, microsecond=0)
        db.upsert_records(self.conn, [
            Record("kma", "suncheon", "observation", observed, observed, "wind_direction", 250, "deg"),
        ], self.now)
        general = build_citizen_forecast(self.conn, self.settings, now=self.now)
        sensitive = build_citizen_forecast(self.conn, self.settings, now=self.now, sensitive=True)
        # 현재 23㎍/㎥(보통), 산단 쪽 바람 아님
        self.assertEqual(general["recommendation"]["summary"], "짧게 환기하세요")
        self.assertEqual(sensitive["recommendation"]["summary"], "추천 시간에만 환기하세요")
        self.assertEqual(general["recommendation"]["outdoor"]["status"], "good")
        self.assertEqual(sensitive["recommendation"]["outdoor"]["status"], "caution")
        # 대기질 등급과 예측값은 기준에 따라 바뀌지 않는다
        self.assertEqual(general["current"]["air_quality"], sensitive["current"]["air_quality"])
        self.assertEqual([i["pm25_predicted"] for i in general["forecast"]],
                         [i["pm25_predicted"] for i in sensitive["forecast"]])

    def test_invalid_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            build_citizen_forecast(self.conn, self.settings, location="gwangyang", now=self.now)
        with self.assertRaises(ValueError):
            build_citizen_forecast(self.conn, self.settings, hours=25, now=self.now)


if __name__ == "__main__":
    unittest.main()
