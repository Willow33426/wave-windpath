"""정규화 파서 테스트. 외부 패키지 없이 python -m unittest 로 돌아간다."""
import json
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.sources.parse import (  # noqa: E402
    KST,
    UpstreamError,
    air_quality_grade,
    direction_label,
    parse_airkorea,
    parse_airkorea_time,
    parse_kma_forecast,
    parse_kma_observation,
)

FIXTURES = ROOT / "app" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class AirKoreaParseTest(unittest.TestCase):
    def test_fixture_returns_pm_records(self):
        records = parse_airkorea(load("airkorea_sample.json"), "suncheon")
        metrics = {r.metric: r.value for r in records}
        self.assertEqual(metrics["pm25"], 23.0)
        self.assertEqual(metrics["pm10"], 38.0)
        self.assertTrue(all(r.kind == "observation" for r in records))
        self.assertTrue(all(r.station == "suncheon" for r in records))
        self.assertTrue(all(r.target_time.tzinfo is not None for r in records))

    def test_missing_values_are_skipped(self):
        payload = {"response": {"header": {"resultCode": "00"},
                                "body": {"items": [{"dataTime": "2026-09-24 15:00",
                                                    "pm10Value": "-", "pm25Value": "", "no2Value": "0.012"}]}}}
        metrics = {r.metric for r in parse_airkorea(payload, "suncheon")}
        self.assertEqual(metrics, {"no2"})

    def test_error_code_raises(self):
        payload = {"response": {"header": {"resultCode": "30", "resultMsg": "SERVICE KEY IS NOT REGISTERED"},
                                "body": {}}}
        with self.assertRaises(UpstreamError):
            parse_airkorea(payload, "suncheon")

    def test_midnight_24h_notation(self):
        parsed = parse_airkorea_time("2026-09-24 24:00")
        self.assertEqual(parsed, datetime(2026, 9, 25, 0, 0, tzinfo=KST))

    def test_broken_timestamp_returns_none(self):
        self.assertIsNone(parse_airkorea_time("어제"))
        self.assertIsNone(parse_airkorea_time(None))


class KmaParseTest(unittest.TestCase):
    def test_forecast_records(self):
        records = parse_kma_forecast(load("kma_forecast_sample.json"), "suncheon")
        self.assertEqual(len(records), 60)  # 12시간 × 5개 카테고리
        winds = [r for r in records if r.metric == "wind_direction"]
        self.assertEqual(len(winds), 12)
        self.assertTrue(all(r.kind == "forecast" for r in records))
        first = min(winds, key=lambda r: r.target_time)
        self.assertEqual(first.target_time, datetime(2026, 9, 24, 15, 0, tzinfo=KST))
        self.assertEqual(first.value, 115.0)
        self.assertEqual(first.unit, "deg")
        self.assertTrue(first.base_time < first.target_time)

    def test_unknown_category_is_ignored(self):
        payload = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [
            {"baseDate": "20260924", "baseTime": "1400", "category": "SKY",
             "fcstDate": "20260924", "fcstTime": "1500", "fcstValue": "1"}]}}}}
        self.assertEqual(parse_kma_forecast(payload, "suncheon"), [])

    def test_nowcast_observation(self):
        payload = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [
            {"baseDate": "20260924", "baseTime": "1400", "category": "T1H", "obsrValue": "24.6"},
            {"baseDate": "20260924", "baseTime": "1400", "category": "VEC", "obsrValue": "115"}]}}}}
        records = parse_kma_observation(payload, "suncheon")
        self.assertEqual({r.metric for r in records}, {"temperature", "wind_direction"})
        self.assertTrue(all(r.kind == "observation" for r in records))


class HelperTest(unittest.TestCase):
    def test_direction_label(self):
        self.assertEqual(direction_label(0), "북")
        self.assertEqual(direction_label(115), "동남동")
        self.assertEqual(direction_label(359), "북")
        self.assertIsNone(direction_label(None))

    def test_air_quality_grade(self):
        self.assertEqual(air_quality_grade(10), "좋음")
        self.assertEqual(air_quality_grade(23), "보통")
        self.assertEqual(air_quality_grade(50), "나쁨")
        self.assertEqual(air_quality_grade(120), "매우나쁨")
        self.assertIsNone(air_quality_grade(None))


if __name__ == "__main__":
    unittest.main()
