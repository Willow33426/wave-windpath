"""설정된 LLM으로 합성 관측값 1건을 보내는 단발 검증 도구.

실제 키와 응답 본문은 출력하지 않는다. 외부 API 사용량이 1회 발생한다.
"""
from __future__ import annotations

import asyncio
import sys

import httpx

from app.briefing import _call_llm, _valid_answer, template_briefing
from app.config import load_settings


SAMPLE = {
    "observed_at": "2026-09-26T13:00:00+09:00",
    "current": {"pm25": 20.0, "pm25_observed_at": "2026-09-26T13:00:00+09:00",
                "air_quality": "보통", "wind_direction_label": "동남동"},
    "forecast": [{"forecast_time": "2026-09-26T14:00:00+09:00",
                  "pm25_predicted": 24.0, "air_quality": "보통"}],
    "recommendation": {"summary": "짧게 환기하세요"},
    "profile": "general",
}


def main() -> int:
    settings = load_settings()
    if not (settings.llm_api_key and settings.llm_model and settings.llm_provider in ("openai", "gemini")):
        print("LLM_PROVIDER, LLM_MODEL, LLM_API_KEY가 설정되지 않았습니다.")
        return 2
    template = template_briefing(SAMPLE)
    try:
        answer = asyncio.run(_call_llm(template, settings))
    except httpx.HTTPStatusError as exc:
        print(f"LLM 요청 실패: HTTP {exc.response.status_code}")
        return 1
    except Exception as exc:  # noqa: BLE001 - URL·키·응답 본문은 출력하지 않는다.
        print(f"LLM 요청 실패: {type(exc).__name__}")
        return 1
    if not isinstance(answer, str) or not _valid_answer(answer, template, SAMPLE):
        print("LLM 응답 수신, 브리핑 검증 실패: 서비스에서는 템플릿으로 폴백합니다.")
        return 1
    print("LLM 응답·브리핑 검증 성공")
    return 0


if __name__ == "__main__":
    sys.exit(main())
