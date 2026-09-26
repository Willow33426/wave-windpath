"""브리핑 사실 표기와 외부 LLM 장애 시 폴백을 확인한다."""
import asyncio
import importlib.util
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.briefing import CallBudget, _call_llm, build_briefing, template_briefing
from app.config import Settings


SAMPLE = {
    "observed_at": "2026-09-26T13:00:00+09:00",
    "current": {"pm25": 20.0, "air_quality": "보통", "wind_direction_label": "동남동"},
    "forecast": [{"forecast_time": "2026-09-26T14:00:00+09:00",
                  "pm25_predicted": 24.0, "air_quality": "보통"}],
    "recommendation": {"summary": "짧게 환기하세요"},
    "profile": "general",
}


class BriefingTest(unittest.TestCase):
    def test_template_uses_observation_forecast_and_wind(self):
        text = template_briefing(SAMPLE)
        self.assertIn("13시", text)
        self.assertIn("관측값은 20", text)
        self.assertIn("14시 예측값은 24", text)
        self.assertIn("동남동", text)
        self.assertIn("참고용", text)
        self.assertEqual(text.count("."), 4)  # PM2.5의 점 + 3문장

    def test_missing_key_returns_template_without_call(self):
        with patch("app.briefing._call_llm", new_callable=AsyncMock) as call:
            result = asyncio.run(build_briefing(SAMPLE, Settings(), CallBudget()))
        self.assertEqual(result["source"], "template")
        call.assert_not_awaited()

    def test_timeout_and_invalid_output_fall_back(self):
        settings = Settings(llm_provider="openai", llm_model="test-model", llm_api_key="test-only")
        for reply in (TimeoutError(), "<script>alert(1)</script>", "산단이 원인입니다. 확실합니다."):
            with self.subTest(reply=type(reply).__name__):
                with patch("app.briefing._call_llm", new_callable=AsyncMock, side_effect=reply if isinstance(reply, Exception) else None, return_value=reply if isinstance(reply, str) else None):
                    result = asyncio.run(build_briefing(SAMPLE, settings, CallBudget()))
                self.assertEqual(result["source"], "template")

    def test_budget_blocks_excess_calls(self):
        ticks = [0.0]
        budget = CallBudget(limit=1, period_sec=60, clock=lambda: ticks[0])
        settings = Settings(llm_provider="openai", llm_model="test-model", llm_api_key="test-only")
        with patch("app.briefing._call_llm", new_callable=AsyncMock, return_value="9월 26일 13시 순천 PM2.5 관측값은 20㎍/㎥(보통)입니다. 9월 26일 14시 예측값은 24㎍/㎥(보통)이며 동남동 바람이 붑니다. 예측은 참고용입니다.") as call:
            first = asyncio.run(build_briefing(SAMPLE, settings, budget))
            second = asyncio.run(build_briefing(SAMPLE, settings, budget))
            ticks[0] = 60.0
            third = asyncio.run(build_briefing(SAMPLE, settings, budget))
        self.assertEqual(first["source"], "llm")
        self.assertEqual(second["source"], "template")
        self.assertEqual(third["source"], "llm")
        self.assertEqual(call.await_count, 2)

    @unittest.skipUnless(importlib.util.find_spec("httpx"), "httpx 미설치")
    def test_provider_requests_keep_key_in_header(self):
        import httpx

        requests = []

        def respond(request):
            requests.append(request)
            if "openai.com" in request.url.host:
                return httpx.Response(200, json={"choices": [{"message": {"content": "OpenAI 응답"}}]})
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Gemini 응답"}]}}]})

        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(respond)
        with patch("app.briefing.httpx.AsyncClient", side_effect=lambda **kw: client_type(transport=transport, **kw)):
            openai = asyncio.run(_call_llm("관측 20", Settings(llm_provider="openai", llm_model="test-model", llm_api_key="test-only")))
            gemini = asyncio.run(_call_llm("관측 20", Settings(llm_provider="gemini", llm_model="test-model", llm_api_key="test-only")))
        self.assertEqual((openai, gemini), ("OpenAI 응답", "Gemini 응답"))
        self.assertTrue(all("test-only" not in str(request.url) for request in requests))
        self.assertEqual(requests[0].headers["authorization"], "Bearer test-only")
        self.assertEqual(requests[1].headers["x-goog-api-key"], "test-only")
        self.assertEqual(json.loads(requests[0].content)["model"], "test-model")


if __name__ == "__main__":
    unittest.main()
