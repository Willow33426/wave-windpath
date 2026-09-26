"""시민 모드 API 응답 조립 테스트."""
import asyncio
import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None

from app import db  # noqa: E402
from app.collector import collect_once  # noqa: E402
from app.config import STATIONS, Settings  # noqa: E402


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi 미설치 (CI에서 실행)")
class CitizenForecastApiTest(unittest.TestCase):
    def setUp(self):
        from app.main import app

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        settings = Settings(service_key="", db_path=pathlib.Path(self.tmp.name) / "citizen.db",
                            stations=STATIONS)
        self.conn = db.connect(settings.db_path)
        collect_once(settings, self.conn)
        self.previous = getattr(app.state, "conn", None)
        app.state.conn = self.conn
        self.app = app

    def tearDown(self):
        self.app.state.conn = self.previous
        self.conn.close()
        self.tmp.cleanup()

    def test_forecast_returns_contract_shape(self):
        from app.main import citizen_forecast

        result = asyncio.run(citizen_forecast(location="suncheon", hours=12))

        self.assertEqual(result["location"]["id"], "suncheon")
        self.assertIn("current", result)
        self.assertIn("forecast", result)
        self.assertEqual(len(result["forecast"]), 12)
        self.assertIn("recommendation", result)
        self.assertIn("data_sources", result)
        pm_rows = db.query_measurements(self.conn, station="suncheon", metric="pm25",
                                        kind="observation", include_fixture=True)
        self.assertEqual(result["current"]["pm25_observed_at"],
                         max(row["target_time"] for row in pm_rows))

        first = result["forecast"][0]
        for key in ("forecast_time", "pm25_predicted", "air_quality", "wind_direction",
                    "wind_direction_label", "wind_speed_mps", "industrial_influence",
                    "confidence", "is_fallback"):
            self.assertIn(key, first)

    def test_api_prefix_and_plain_path_are_both_available(self):
        from fastapi.testclient import TestClient

        client = TestClient(self.app)

        prefixed = client.get("/api/citizen/forecast?location=suncheon&hours=3")
        plain = client.get("/citizen/forecast?location=suncheon&hours=3")

        self.assertEqual(prefixed.status_code, 200)
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(len(prefixed.json()["forecast"]), 3)
        self.assertEqual(prefixed.json()["location"], plain.json()["location"])

    def test_unknown_location_returns_contract_error(self):
        from fastapi.testclient import TestClient

        client = TestClient(self.app)

        response = client.get("/api/citizen/forecast?location=other")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["error"]["code"], "INVALID_PARAMETER")

    def test_briefing_returns_template_without_llm_key(self):
        from fastapi.testclient import TestClient

        with patch("app.main.settings", Settings()), patch("app.briefing._call_llm") as call:
            client = TestClient(self.app)
            public = client.get("/api/citizen/briefing?location=suncheon&hours=12")
            internal = client.get("/citizen/briefing?location=suncheon&hours=12")
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()["source"], "template")
        self.assertIn("참고용", public.json()["text"])
        self.assertEqual(public.json(), internal.json())
        call.assert_not_called()

    def test_briefing_cache_separates_sensitive_profile(self):
        from fastapi.testclient import TestClient
        from app.cache import ResponseCache

        answer = {"text": "검증용 브리핑", "source": "llm", "observed_at": None}
        with patch("app.main.briefing_cache", ResponseCache(ttl_sec=300)), \
             patch("app.main.build_briefing", new_callable=AsyncMock, return_value=answer) as build:
            client = TestClient(self.app)
            first = client.get("/api/citizen/briefing")
            second = client.get("/api/citizen/briefing")
            sensitive = client.get("/api/citizen/briefing?sensitive=true")
        self.assertEqual([first.status_code, second.status_code, sensitive.status_code], [200, 200, 200])
        self.assertEqual(build.await_count, 2)


if __name__ == "__main__":
    unittest.main()
