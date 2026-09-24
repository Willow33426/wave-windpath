"""기준선 예측 테스트."""
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.forecast.baseline import (  # noqa: E402
    Facility,
    Observation,
    WeatherPoint,
    angle_diff,
    bearing_deg,
    influence_score,
    persistence_forecast,
    predict,
    transport_hours,
    wind_rule_forecast,
)

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 9, 24, 15, 0, tzinfo=KST)
GWANGYANG = Facility("광양제철소", 101.0, 24.0)


def history(values, start=T0 - timedelta(hours=3)):
    return [Observation(start + timedelta(hours=i), v) for i, v in enumerate(values)]


def weather(direction, speed, hours=12, start=T0):
    return [WeatherPoint(start + timedelta(hours=i + 1), direction, speed) for i in range(hours)]


class GeometryTest(unittest.TestCase):
    def test_bearing_suncheon_to_gwangyang(self):
        # 순천 시청 → 광양제철소: 동남동(약 100도)
        deg = bearing_deg(34.9506, 127.4872, 34.9070, 127.7550)
        self.assertAlmostEqual(deg, 101, delta=4)

    def test_angle_diff_wraps(self):
        self.assertEqual(angle_diff(350, 10), 20)
        self.assertEqual(angle_diff(10, 350), 20)
        self.assertEqual(angle_diff(101, 101), 0)

    def test_transport_hours(self):
        self.assertAlmostEqual(transport_hours(24, 3.0), 2.22, delta=0.05)
        self.assertIsNone(transport_hours(24, 0.0))
        self.assertIsNone(transport_hours(24, None))


class InfluenceTest(unittest.TestCase):
    def test_wind_from_facility_scores_high(self):
        score, matched = influence_score(101, 3.5, (GWANGYANG,))
        self.assertGreater(score, 0.9)
        self.assertEqual(matched, ["광양제철소"])

    def test_wind_from_other_direction_scores_zero(self):
        score, matched = influence_score(300, 3.5, (GWANGYANG,))
        self.assertEqual(score, 0.0)
        self.assertEqual(matched, [])

    def test_weak_wind_lowers_score(self):
        strong, _ = influence_score(101, 5.0, (GWANGYANG,))
        weak, _ = influence_score(101, 0.5, (GWANGYANG,))
        self.assertLess(weak, strong)

    def test_missing_direction(self):
        self.assertEqual(influence_score(None, 3.0, (GWANGYANG,)), (0.0, []))


class PersistenceTest(unittest.TestCase):
    def test_length_and_value(self):
        items = persistence_forecast(history([18, 20, 23, 25]), hours=12)
        self.assertEqual(len(items), 12)
        self.assertTrue(all(i["pm25_predicted"] == 25.0 for i in items))
        self.assertEqual(items[0]["air_quality"], "보통")

    def test_hourly_times_increase(self):
        items = persistence_forecast(history([20]), hours=3, start=T0)
        times = [i["forecast_time"] for i in items]
        self.assertEqual(times[0], (T0 + timedelta(hours=1)).isoformat())
        self.assertEqual(len(set(times)), 3)

    def test_empty_history(self):
        self.assertEqual(persistence_forecast([], hours=12), [])

    def test_all_none_values(self):
        items = persistence_forecast([Observation(T0, None)], hours=2)
        self.assertTrue(all(i["pm25_predicted"] is None for i in items))


class WindRuleTest(unittest.TestCase):
    def test_upwind_higher_and_wind_from_facility_raises_prediction(self):
        items = wind_rule_forecast(history([20, 20, 20, 20]), history([45, 45, 45, 45]),
                                   weather(101, 4.0), hours=12, facilities=(GWANGYANG,), start=T0)
        later = items[3]["pm25_predicted"]      # 도달 시간(약 2시간) 이후
        self.assertGreater(later, 20.0)
        self.assertLess(later, 45.0)            # 상류 값을 넘지 않는다
        self.assertEqual(items[3]["industrial_influence"]["upwind_facilities"], ["광양제철소"])

    def test_wind_from_other_direction_keeps_persistence(self):
        items = wind_rule_forecast(history([20, 20, 20, 20]), history([45, 45, 45, 45]),
                                   weather(300, 4.0), hours=6, facilities=(GWANGYANG,), start=T0)
        self.assertTrue(all(i["pm25_predicted"] == 20.0 for i in items))
        self.assertEqual(items[0]["industrial_influence"]["level"], "unknown")

    def test_upwind_lower_does_not_lower_prediction(self):
        items = wind_rule_forecast(history([40, 40]), history([10, 10]),
                                   weather(101, 4.0), hours=6, facilities=(GWANGYANG,), start=T0)
        self.assertTrue(all(i["pm25_predicted"] == 40.0 for i in items))

    def test_correction_decays_with_horizon(self):
        items = wind_rule_forecast(history([20, 20]), history([60, 60]),
                                   weather(101, 4.0), hours=12, facilities=(GWANGYANG,), start=T0)
        values = [i["pm25_predicted"] for i in items]
        self.assertGreater(values[2], values[-1])   # 멀어질수록 보정이 줄어든다


class PredictInterfaceTest(unittest.TestCase):
    def test_returns_12_items_with_contract_keys(self):
        items = predict({
            "target_history": history([20, 22, 24]),
            "upwind_history": history([30, 32, 35]),
            "weather": weather(101, 3.0),
            "facilities": (GWANGYANG,),
        })
        self.assertEqual(len(items), 12)
        for key in ("forecast_time", "pm25_predicted", "confidence"):
            self.assertIn(key, items[0])

    def test_falls_back_to_persistence_without_weather(self):
        items = predict({"target_history": history([20, 22, 24]), "upwind_history": history([40])})
        self.assertEqual(items[0]["model"], "baseline-persistence")

    def test_empty_features(self):
        self.assertEqual(predict({}), [])


class EvaluateScriptTest(unittest.TestCase):
    """평가 스크립트의 MAE 계산이 시간 순서를 지키는지."""

    def test_evaluate_reports_both_models(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from evaluate_baseline import evaluate  # noqa: E402

        rows = []
        for i in range(60):
            t = T0 + timedelta(hours=i)
            rows.append({"time": t, "pm25": 20 + (i % 5), "upwind_pm25": 40 + (i % 3),
                         "wind_direction": 101 if i % 2 == 0 else 300, "wind_speed": 3.0})
        result = evaluate(rows, hours=6, split=0.8)
        self.assertGreater(result["origins"], 0)
        self.assertIn("baseline-persistence", result["overall"])
        self.assertIn("baseline-wind-rule", result["overall"])
        self.assertTrue(all(v is None or v >= 0 for v in result["overall"].values()))


class ContractShapeTest(unittest.TestCase):
    """폴백이든 아니든 forecast[] 항목 구조가 같아야 한다."""

    KEYS = ("forecast_time", "pm25_predicted", "air_quality", "confidence",
            "industrial_influence", "model")

    def test_persistence_items_carry_influence_block(self):
        items = persistence_forecast(history([18, 20, 23]), hours=3)
        for key in self.KEYS:
            self.assertIn(key, items[0])
        self.assertEqual(items[0]["industrial_influence"]["level"], "unknown")
        self.assertIsNone(items[0]["industrial_influence"]["score"])

    def test_fallback_path_of_predict_keeps_same_keys(self):
        items = predict({"target_history": history([20, 22, 24])})
        self.assertEqual(items[0]["model"], "baseline-persistence")
        for key in self.KEYS:
            self.assertIn(key, items[0])


class WeakWindTest(unittest.TestCase):
    """풍속을 모르면 도달했다고 보지 않는다."""

    def test_no_correction_without_usable_wind_speed(self):
        for speed in (None, 0.0, 0.3):
            with self.subTest(speed=speed):
                items = wind_rule_forecast(history([20, 20]), history([60, 60]),
                                           weather(101, speed), hours=6,
                                           facilities=(GWANGYANG,), start=T0)
                self.assertTrue(all(i["pm25_predicted"] == 20.0 for i in items),
                                "바람이 없는데 농도를 올리면 안 된다")

    def test_usable_wind_still_corrects(self):
        items = wind_rule_forecast(history([20, 20]), history([60, 60]),
                                   weather(101, 4.0), hours=6,
                                   facilities=(GWANGYANG,), start=T0)
        self.assertGreater(items[-1]["pm25_predicted"], 20.0)


class WeatherGapTest(unittest.TestCase):
    """예보가 비는 시각은 persistence로 표시한다."""

    def test_missing_hours_are_labelled_persistence(self):
        partial = weather(101, 4.0, hours=3)          # 12시간 중 앞 3시간만 있다
        items = wind_rule_forecast(history([20, 20]), history([60, 60]), partial,
                                   hours=12, facilities=(GWANGYANG,), start=T0)
        covered, gaps = items[:3], items[3:]
        self.assertTrue(all(i["model"] == "baseline-wind-rule" for i in covered))
        self.assertTrue(all(i["model"] == "baseline-persistence" for i in gaps))
        self.assertTrue(all(i["pm25_predicted"] == 20.0 for i in gaps))
        self.assertLess(gaps[0]["confidence"], covered[-1]["confidence"])


if __name__ == "__main__":
    unittest.main()
