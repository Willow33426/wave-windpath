"""실측 결측과 운영 폴백 경로를 확인한다."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app.forecast.baseline import Observation, WeatherPoint
from app.forecast.model import predict, train_models


START = datetime(2026, 9, 1, tzinfo=timezone(timedelta(hours=9)))


class ModelTest(unittest.TestCase):
    def test_training_skips_missing_targets_and_accepts_missing_weather(self):
        rows = []
        for i in range(80):
            rows.append({
                "time": (START + timedelta(hours=i)).isoformat(),
                "pm25": None if i in (12, 28) else 20.0 + i % 6,
                "upwind_pm25": None if i % 11 == 0 else 30.0,
                "wind_direction": None if i % 9 == 0 else 105.0,
                "wind_speed": None if i % 7 == 0 else 3.0,
            })
        models = train_models(rows, hours=2)
        self.assertEqual(set(models), {1, 2})

    def test_missing_model_uses_wind_rule_then_persistence(self):
        history = [Observation(START + timedelta(hours=i), 20.0, 101.0, 3.0)
                   for i in range(4)]
        upwind = [Observation(history[-1].time, 60.0)]
        weather = [WeatherPoint(history[-1].time + timedelta(hours=i), 101.0, 3.0)
                   for i in range(1, 5)]
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.joblib"
            wind = predict({"target_history": history, "upwind_history": upwind,
                            "weather": weather}, hours=4, model_path=missing)
            plain = predict({"target_history": history}, hours=4, model_path=missing)
        self.assertEqual(wind[2]["model"], "baseline-wind-rule")
        self.assertGreater(wind[2]["pm25_predicted"], 20.0)
        self.assertEqual(plain[0]["model"], "baseline-persistence")


if __name__ == "__main__":
    unittest.main()
