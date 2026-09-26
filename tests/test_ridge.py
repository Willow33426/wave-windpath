"""AI 예측(릿지) 모델과 시민 응답 연결 테스트."""
import math
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
from app.forecast import ridge  # noqa: E402
from app.sources.parse import KST, Record  # noqa: E402


class RidgeMathTest(unittest.TestCase):
    def test_fit_recovers_linear_relation(self):
        raw = [[i / 10, ((i * 7) % 13) / 5] for i in range(60)]
        y = [3 + 2 * a - b for a, b in raw]
        means, stds = ridge.fit_scaler(raw)
        x = [ridge.scale(row, means, stds) for row in raw]
        intercept, coef = ridge.fit_ridge(x, y, alpha=1e-9)
        # 표준화 공간의 계수를 원래 단위로 되돌리면 2, -1이어야 한다
        self.assertAlmostEqual(coef[0] / stds[0], 2, places=4)
        self.assertAlmostEqual(coef[1] / stds[1], -1, places=4)
        for row, target in zip(x, y):
            self.assertAlmostEqual(intercept + sum(w * v for w, v in zip(coef, row)), target, places=4)

    def test_features_need_recent_pm25(self):
        at = datetime(2026, 9, 26, 12, tzinfo=KST)
        series = {"pm": {at: 12.0}}
        self.assertIsNone(ridge.make_features(series, at))
        series["pm"].update({at - timedelta(hours=1): 11.0, at - timedelta(hours=3): 10.0})
        row = ridge.make_features(series, at)
        self.assertEqual(len(row), len(ridge.FEATURES))
        self.assertEqual(row[0], 12.0)

    def test_industrial_wind_sector(self):
        self.assertTrue(ridge.is_industrial_wind(110))
        self.assertFalse(ridge.is_industrial_wind(55))
        self.assertFalse(ridge.is_industrial_wind(None))


class ShippedModelTest(unittest.TestCase):
    def setUp(self):
        self.model = ridge.load_model()

    def test_model_file_is_complete(self):
        self.assertIsNotNone(self.model)
        self.assertEqual(self.model["name"], ridge.MODEL_NAME)
        self.assertEqual(self.model["features"], list(ridge.FEATURES))
        self.assertEqual([h["h"] for h in self.model["horizons"]], list(range(1, 15)))
        for item in self.model["horizons"]:
            self.assertEqual(len(item["coef"]), len(ridge.FEATURES))

    def test_validation_beats_persistence(self):
        # 학습 자료나 특징을 바꿨을 때 기준선보다 나빠지면 여기서 잡는다
        for result in self.model["evaluation"]:
            self.assertLess(result["mae_model"], result["mae_persistence"])

    def test_prediction_is_finite(self):
        at = datetime(2026, 9, 26, 12, tzinfo=KST)
        series = {"pm": {at - timedelta(hours=k): 12.0 + k % 3 for k in range(26)},
                  "gy": {at: 9.0}, "wd": {at: 110.0}, "ws": {at: 3.0}}
        result = ridge.predict(self.model, series, at)
        self.assertEqual(len(result), 14)
        self.assertEqual(result[0][0], at + timedelta(hours=1))
        self.assertTrue(all(math.isfinite(v) and v >= 0 for _, v in result))


class CitizenAiForecastTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = db.connect(pathlib.Path(self.tmp.name) / "ai.db")
        self.now = datetime(2026, 9, 26, 12, 20, tzinfo=KST)
        observed = self.now.replace(minute=0)
        records = []
        for k in range(30):
            t = observed - timedelta(hours=k)
            records += [
                Record("airkorea", "suncheon", "observation", t, t, "pm25", 10 + k % 5, "ug/m3"),
                Record("airkorea", "gwangyang", "observation", t, t, "pm25", 8 + k % 4, "ug/m3"),
                Record("kma", "suncheon", "observation", t, t, "wind_direction", 60, "deg"),
                Record("kma", "suncheon", "observation", t, t, "wind_speed", 2.5, "m/s"),
            ]
        for h in range(1, 15):
            t = observed + timedelta(hours=h)
            direction = 110 if 3 <= h <= 5 else 60   # 3~5시간 뒤에만 산단 쪽 바람
            records += [
                Record("kma", "suncheon", "forecast", observed, t, "wind_direction", direction, "deg"),
                Record("kma", "suncheon", "forecast", observed, t, "wind_speed", 3.0, "m/s"),
            ]
        db.upsert_records(self.conn, records, self.now)
        db.set_meta(self.conn, META_LAST_FALLBACK, "0")
        self.settings = Settings(db_path=pathlib.Path(self.tmp.name) / "ai.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_uses_ai_model_with_enough_history(self):
        result = build_citizen_forecast(self.conn, self.settings, now=self.now)
        self.assertEqual(result["model"]["name"], ridge.MODEL_NAME)
        self.assertIsNotNone(result["model"]["mae_validation"])
        self.assertEqual(len(result["forecast"]), 12)
        self.assertTrue(all(item["model"] == ridge.MODEL_NAME for item in result["forecast"]))
        self.assertTrue(all(datetime.fromisoformat(item["forecast_time"]) > self.now for item in result["forecast"]))
        self.assertIn("sectors", result["evidence"])

    def test_ai_input_ignores_leftover_fixture_in_live_mode(self):
        # 실데이터 수집 중에는 남은 fixture(99㎍/㎥)가 AI 입력(현재값)으로 들어가지 않는다
        leftover = self.now.replace(minute=15)
        db.upsert_records(self.conn, [
            Record("airkorea", "suncheon", "observation", leftover, leftover, "pm25", 99, "ug/m3", "fixture"),
        ], self.now)
        result = build_citizen_forecast(self.conn, self.settings, now=self.now)
        self.assertEqual(result["model"]["name"], ridge.MODEL_NAME)
        self.assertLess(result["current"]["pm25"], 20)
        self.assertTrue(all(item["pm25_predicted"] < 40 for item in result["forecast"]))

    def test_windows_skip_industrial_wind_hours(self):
        result = build_citizen_forecast(self.conn, self.settings, now=self.now)
        windows = result["recommendation"]["ventilation"]["windows"]
        self.assertTrue(windows)
        industrial = [item["forecast_time"] for item in result["forecast"]
                      if item["industrial_influence"]["level"] in ("medium", "high")]
        self.assertTrue(industrial)
        for start_text in industrial:
            start = datetime.fromisoformat(start_text)
            for window in windows:
                self.assertFalse(datetime.fromisoformat(window["start"]) <= start < datetime.fromisoformat(window["end"]))


if __name__ == "__main__":
    unittest.main()
