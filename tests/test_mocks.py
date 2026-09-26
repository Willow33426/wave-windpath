"""mock 응답이 docs/api.md 계약과 어긋나지 않는지 검사한다.

프론트엔드는 실제 서버가 뜨기 전까지 이 파일만 보고 개발한다.
mock 안에서 앞뒤가 안 맞으면 화면이 잘못 만들어지므로 자동으로 막는다.
표준 라이브러리만 쓴다.
"""
import json
import pathlib
import unittest
from datetime import datetime, timedelta

MOCK_DIR = pathlib.Path(__file__).resolve().parent.parent / "docs" / "mock"

DIRECTIONS = ["북", "북북동", "북동", "동북동", "동", "동남동", "남동", "남남동",
              "남", "남남서", "남서", "서남서", "서", "서북서", "북서", "북북서"]
FORECAST_KEYS = {"forecast_time", "pm25_predicted", "air_quality", "wind_direction",
                 "wind_direction_label", "wind_speed_mps", "industrial_influence",
                 "confidence", "is_fallback"}


def load(name):
    return json.loads((MOCK_DIR / name).read_text(encoding="utf-8"))


def grade(pm25):
    """docs/api.md의 경계와 같아야 한다. 소수도 분류돼야 한다."""
    if pm25 is None:
        return None
    if pm25 <= 15:
        return "좋음"
    if pm25 <= 35:
        return "보통"
    if pm25 <= 75:
        return "나쁨"
    return "매우나쁨"


def label(degree):
    if degree is None:
        return None
    return DIRECTIONS[int((degree % 360) / 22.5 + 0.5) % 16]


class ForecastShapeTest(unittest.TestCase):
    def setUp(self):
        self.doc = load("citizen_forecast.json")

    def test_every_item_has_contract_keys(self):
        for item in self.doc["forecast"]:
            self.assertEqual(FORECAST_KEYS - set(item), set(), item["forecast_time"])

    def test_times_are_hourly_and_increasing(self):
        times = [datetime.fromisoformat(i["forecast_time"]) for i in self.doc["forecast"]]
        gaps = {b - a for a, b in zip(times, times[1:])}
        self.assertEqual(gaps, {timedelta(hours=1)})

    def test_grade_matches_documented_boundaries(self):
        current = self.doc["current"]
        self.assertEqual(current["air_quality"], grade(current["pm25"]))
        for item in self.doc["forecast"]:
            self.assertEqual(item["air_quality"], grade(item["pm25_predicted"]),
                             item["forecast_time"])

    def test_wind_label_matches_degree(self):
        current = self.doc["current"]
        self.assertEqual(current["wind_direction_label"], label(current["wind_direction"]))
        for item in self.doc["forecast"]:
            self.assertEqual(item["wind_direction_label"], label(item["wind_direction"]),
                             item["forecast_time"])


class RecommendationTest(unittest.TestCase):
    """추천 문구가 예보와 모순되면 안 된다. 실제로 어긋난 적이 있다."""

    def setUp(self):
        self.doc = load("citizen_forecast.json")
        self.by_time = {i["forecast_time"]: i for i in self.doc["forecast"]}

    def test_windows_are_inside_the_forecast_range(self):
        times = sorted(self.by_time)
        for key in ("ventilation", "outdoor"):
            for window in self.doc["recommendation"][key]["windows"]:
                self.assertGreaterEqual(window["start"], times[0], key)
                self.assertLessEqual(window["end"],
                                     (datetime.fromisoformat(times[-1])
                                      + timedelta(hours=1)).isoformat(), key)

    def test_text_does_not_claim_a_wind_shift_that_is_not_forecast(self):
        text = self.doc["recommendation"]["ventilation"]["text"]
        for window in self.doc["recommendation"]["ventilation"]["windows"]:
            item = self.by_time.get(window["start"])
            if item is None:
                continue
            spoken = item["wind_direction_label"]
            others = [d for d in DIRECTIONS if d != spoken and len(d) >= 2]
            claimed = [d for d in others if f"{d}로 바뀌는" in text or f"{d}풍으로 바뀌는" in text]
            self.assertEqual(claimed, [],
                             f"{window['start']}의 예보는 {spoken}인데 문구는 {claimed}라고 한다")


class FallbackMockTest(unittest.TestCase):
    def test_fallback_marks_itself(self):
        doc = load("citizen_forecast_fallback.json")
        self.assertTrue(doc["is_fallback"])
        self.assertTrue(any(s.get("note") for s in doc["data_sources"]))
        self.assertTrue(all(i["confidence"] is None for i in doc["forecast"]))

    def test_error_mock_has_documented_fields(self):
        error = load("error_upstream.json")["error"]
        for key in ("code", "message", "retry_after_sec"):
            self.assertIn(key, error)

    def test_health_mock_has_documented_fields(self):
        health = load("health.json")
        for key in ("status", "version", "time", "db", "last_collected_at", "is_fallback"):
            self.assertIn(key, health)


if __name__ == "__main__":
    unittest.main()
