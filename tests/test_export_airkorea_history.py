"""과거 PM2.5 내보내기에서 시각을 맞추고 키를 출력하지 않는지 확인한다."""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.config import STATIONS, Settings
from app.sources.parse import KST
import importlib.util

# 수집 모듈이 httpx를 쓴다. 설치 없는 CI 단위 잡에서는 건너뛰고, 의존성을 설치하는 smoke 잡에서 실행한다.
HTTPX_AVAILABLE = importlib.util.find_spec("httpx") is not None
if HTTPX_AVAILABLE:
    from scripts.export_airkorea_history import fetch_pm25, rows_for_csv


@unittest.skipUnless(HTTPX_AVAILABLE, "httpx 미설치 (CI smoke 잡에서 실행)")
class ExportHistoryTest(unittest.TestCase):
    def test_reads_pm25_from_api_response(self):
        fixture = Path(__file__).resolve().parent.parent / "app/fixtures/airkorea_sample.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        with patch("scripts.export_airkorea_history._get_json", return_value=payload) as get:
            values = fetch_pm25(STATIONS[0], Settings(service_key="test-only"))
        self.assertEqual(len(values), 1)
        self.assertEqual(next(iter(values.values())), 23)
        self.assertEqual(get.call_args.args[1]["dataTerm"], "MONTH")

    def test_uses_gwangyang_and_falls_back_to_yeosu(self):
        first = datetime(2026, 9, 24, 14, tzinfo=KST)
        second = first + timedelta(hours=1)
        rows = rows_for_csv({
            "suncheon": {first: 20, second: 22},
            "gwangyang": {first: 40},
            "yeosu": {first: 60, second: 50},
        })
        self.assertEqual([row[2] for row in rows], [40, 50])
        self.assertEqual([row[3:] for row in rows], [["", ""], ["", ""]])


if __name__ == "__main__":
    unittest.main()
