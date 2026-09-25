"""저장된 실측과 예보가 예측 입력으로 안전하게 변환되는지 확인한다."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app import db
from app.forecast.inputs import build_features
from app.forecast.model import predict
from app.sources.parse import KST, Record


NOW = datetime(2026, 9, 25, 15, 30, tzinfo=KST)


def record(station, metric, value, target, kind="observation", base=None):
    return Record("airkorea" if metric == "pm25" else "kma", station, kind,
                  base or target, target, metric, value, "")


class ForecastInputsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = db.connect(Path(self.temp.name) / "measurements.db")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_joins_observations_and_only_available_forecasts(self):
        origin = NOW.replace(minute=0)
        items = []
        for offset in range(-3, 1):
            time = origin + timedelta(hours=offset)
            items.extend([
                record("suncheon", "pm25", 20 + offset, time),
                record("suncheon", "wind_direction", 101, time),
                record("suncheon", "wind_speed", 3, time),
                record("gwangyang", "pm25", 40, time),
                record("yeosu", "pm25", 60, time),
            ])
        for offset in (1, 2):
            time = origin + timedelta(hours=offset)
            items.extend([
                record("suncheon", "wind_direction", 101, time, "forecast", origin),
                record("suncheon", "wind_speed", 3, time, "forecast", origin),
            ])
        db.upsert_records(self.conn, items, NOW)
        # 나중에 수집된 관측은 조회 시점에 사용하지 않는다.
        db.upsert_records(self.conn,
                          [record("suncheon", "pm25", 99, origin + timedelta(hours=1))],
                          NOW + timedelta(hours=1))

        features = build_features(self.conn, hours=2, now=NOW)
        self.assertEqual(len(features["target_history"]), 4)
        self.assertEqual(features["target_history"][-1].pm25, 20)
        self.assertEqual(features["upwind_history"][-1].pm25, 40)
        self.assertEqual([point.time for point in features["weather"]],
                         [origin + timedelta(hours=1), origin + timedelta(hours=2)])
        with tempfile.TemporaryDirectory() as directory:
            result = predict(features, hours=2,
                             model_path=Path(directory) / "missing.joblib")
        self.assertEqual(len(result), 2)

    def test_empty_database_returns_empty_histories(self):
        features = build_features(self.conn, now=NOW)
        self.assertEqual(features, {"target_history": [], "upwind_history": [],
                                    "weather": []})


if __name__ == "__main__":
    unittest.main()
