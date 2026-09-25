"""#1 시민 응답 구조와 실제 예측 입력 연결을 확인한다."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.forecast.citizen import build_response
from app.main import app
from app.sources.parse import KST, Record


NOW = datetime(2026, 9, 25, 15, 30, tzinfo=KST)


def record(station, kind, metric, value, target, base=None):
    source = "airkorea" if metric.startswith("pm") else "kma"
    return Record(source, station, kind, base or target, target, metric, value, "")


class CitizenForecastTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = db.connect(Path(self.temp.name) / "citizen.db")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_response_matches_required_fields_and_times(self):
        origin = NOW.replace(minute=0)
        records = []
        for offset in range(-3, 1):
            time = origin + timedelta(hours=offset)
            records += [record("suncheon", "observation", "pm25", 20, time),
                        record("gwangyang", "observation", "pm25", 40, time),
                        record("suncheon", "observation", "wind_direction", 101, time),
                        record("suncheon", "observation", "wind_speed", 3, time)]
        for offset in range(1, 13):
            time = origin + timedelta(hours=offset)
            records += [record("suncheon", "forecast", "wind_direction", 101, time, origin),
                        record("suncheon", "forecast", "wind_speed", 3, time, origin)]
        db.upsert_records(self.conn, records, NOW)

        result = build_response(self.conn, now=NOW)
        self.assertEqual(set(result), {"location", "updated_at", "observed_at",
                                       "is_fallback", "data_sources", "current",
                                       "forecast", "recommendation", "reason", "model"})
        self.assertEqual(result["current"]["pm25"], 20)
        self.assertEqual(len(result["forecast"]), 12)
        self.assertTrue(all(datetime.fromisoformat(item["forecast_time"]) > NOW
                            for item in result["forecast"]))
        self.assertTrue(all("is_fallback" in item and "wind_speed_mps" in item
                            for item in result["forecast"]))
        self.assertEqual(result["model"]["name"], "baseline-wind-rule")

    def test_http_invalid_parameters_and_no_data(self):
        app.state.conn = self.conn
        client = TestClient(app)
        self.assertEqual(client.get("/api/citizen/forecast?location=other").status_code, 400)
        self.assertEqual(client.get("/api/citizen/forecast?hours=25").status_code, 400)
        response = client.get("/api/citizen/forecast")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "UPSTREAM_UNAVAILABLE")

    def test_http_returns_forecast_from_stored_values(self):
        origin = datetime.now(KST).replace(minute=0, second=0, microsecond=0)
        records = [
            record("suncheon", "observation", "pm25", 20, origin),
            record("gwangyang", "observation", "pm25", 40, origin),
            record("suncheon", "forecast", "wind_direction", 101,
                   origin + timedelta(hours=1), origin),
            record("suncheon", "forecast", "wind_speed", 3,
                   origin + timedelta(hours=1), origin),
        ]
        db.upsert_records(self.conn, records, datetime.now(KST))
        app.state.conn = self.conn
        response = TestClient(app).get("/api/citizen/forecast?hours=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["current"]["pm25"], 20)
        self.assertEqual(len(response.json()["forecast"]), 1)


if __name__ == "__main__":
    unittest.main()
