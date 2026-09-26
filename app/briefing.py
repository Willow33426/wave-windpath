"""예측 수치 기반 시민 브리핑과 외부 LLM 실패 시 템플릿 폴백."""
from __future__ import annotations

import re
import time
from collections import deque
from datetime import datetime
from typing import Callable

try:
    import httpx
except ModuleNotFoundError:  # 설치 없는 CI 단위 잡에서도 템플릿을 검증한다.
    httpx = None

from app.config import Settings

MAX_TEXT = 300
MODEL_NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
SYSTEM_PROMPT = (
    "당신은 순천시 대기질 안내 문구 편집자입니다. 제공된 문장만 자연스럽게 다듬어 "
    "한국어 2~3문장으로 답하세요. 수치·시각·등급·풍향을 바꾸거나 새로운 사실을 "
    "만들지 마세요. 산업단지를 오염 원인으로 단정하지 말고, 예측은 참고용이라고 밝히세요. "
    "HTML·마크다운·링크는 쓰지 마세요."
)


def _when(stamp: str | None) -> str | None:
    if not stamp:
        return None
    try:
        value = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return f"{value.month}월 {value.day}일 {value.hour}시"


def template_briefing(data: dict) -> str:
    """관측과 예측을 구분해 시각·수치·풍향·행동을 3문장으로 설명한다."""
    current = data.get("current") or {}
    forecast = next((item for item in data.get("forecast", []) if item.get("pm25_predicted") is not None), None)
    observed = _when(current.get("pm25_observed_at") or data.get("observed_at"))
    pm = current.get("pm25")
    if pm is None or observed is None:
        first = "최신 순천 PM2.5 관측값을 확인하지 못했습니다."
    else:
        first = f"{observed} 순천 PM2.5 관측값은 {pm:g}㎍/㎥({current.get('air_quality') or '등급 미확인'})입니다."

    if forecast:
        when = _when(forecast.get("forecast_time")) or "다음 예측 시각"
        value = forecast["pm25_predicted"]
        second = f"{when} 예측값은 {value:g}㎍/㎥({forecast.get('air_quality') or '등급 미확인'})로 예상됩니다"
    else:
        second = "향후 예측 자료가 아직 없습니다"
    wind = current.get("wind_direction_label")
    if wind:
        second += f"; 현재 바람은 {wind}에서 불어옵니다."
    else:
        second += "; 현재 풍향은 확인되지 않았습니다."

    recommendation = (data.get("recommendation") or {}).get("summary") or "새 자료가 들어오면 다시 확인하세요"
    profile = "민감군은 " if data.get("profile") == "sensitive" else ""
    third = f"{profile}{recommendation.rstrip('.')}—예측은 참고용으로 확인하세요."
    return f"{first} {second} {third}"


class CallBudget:
    """단일 워커의 외부 LLM 호출 수를 제한한다. 캐시 적중은 사용하지 않는다."""

    def __init__(self, limit: int = 10, period_sec: float = 60,
                 clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.period_sec = period_sec
        self.clock = clock
        self.calls: deque[float] = deque()

    def allow(self) -> bool:
        now = self.clock()
        while self.calls and now - self.calls[0] >= self.period_sec:
            self.calls.popleft()
        if len(self.calls) >= self.limit:
            return False
        self.calls.append(now)
        return True


def _valid_answer(answer: str, template: str, data: dict) -> bool:
    text = answer.strip()
    if not 20 <= len(text) <= MAX_TEXT or any(x in text for x in ("<", ">", "`", "http://", "https://")):
        return False
    if not 2 <= len(re.findall(r"[.!?。](?:\s|$)", text)) <= 3:
        return False
    if "참고" not in text or not any(label in text for label in ("PM2.5", "초미세먼지")):
        return False
    if any(word in text for word in ("확실", "반드시", "보장")):
        return False
    causal_claim = re.search(r"산단[^.!?。]{0,24}(?:원인|때문|유발|오염원|배출)", text)
    if causal_claim:
        claim_context = text[causal_claim.start():causal_claim.end() + 24]
        if not any(negation in claim_context for negation in ("아니", "않", "뜻하지", "단정하지")):
            return False
    current = data.get("current") or {}
    forecast = next((item for item in data.get("forecast", []) if item.get("pm25_predicted") is not None), None)
    required = []
    if current.get("pm25") is not None:
        required += [f"{current['pm25']:g}", current.get("air_quality") or "",
                     _when(current.get("pm25_observed_at") or data.get("observed_at")) or ""]
    if current.get("wind_direction_label"):
        required.append(current["wind_direction_label"])
    if forecast:
        required += ["예측", f"{forecast['pm25_predicted']:g}", forecast.get("air_quality") or ""]
        try:
            forecast_hour = f"{datetime.fromisoformat(forecast['forecast_time']).hour}시"
        except (KeyError, TypeError, ValueError):
            forecast_hour = ""
        required.append(forecast_hour)
    if any(value and value not in text for value in required):
        return False
    # 템플릿 밖의 숫자·시간을 추가한 답은 사용하지 않는다.
    return set(re.findall(r"\d+(?:\.\d+)?", text)) <= set(re.findall(r"\d+(?:\.\d+)?", template))


async def _call_llm(template: str, settings: Settings,
                    on_response: Callable[[int], None] | None = None) -> str:
    if httpx is None:
        raise ValueError("HTTP 클라이언트가 없습니다")
    provider = settings.llm_provider
    model = settings.llm_model
    if not MODEL_NAME.fullmatch(model):
        raise ValueError("지원하지 않는 모델 이름")
    if provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
        body = {"model": model, "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": template},
        ], "max_completion_tokens": 220, "store": False}
    elif provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"x-goog-api-key": settings.llm_api_key}
        body = {"systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [{"parts": [{"text": template}]}],
                # Gemini 3의 출력 한도에는 내부 사고 토큰도 포함된다. 짧은 한도만 두면
                # HTTP 200이어도 최종 문장이 비거나 잘릴 수 있으므로 사고 수준을 최소로
                # 고정하고 2~3문장을 마칠 여유를 둔다.
                "generationConfig": {
                    "maxOutputTokens": 512,
                    "thinkingConfig": {"thinkingLevel": "minimal"},
                }}
    else:
        raise ValueError("지원하지 않는 제공자")

    timeout = min(max(settings.llm_timeout_sec, 0.5), 8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        if on_response is not None:
            on_response(response.status_code)
        payload = response.json()
    if provider == "openai":
        return payload["choices"][0]["message"]["content"]
    return "".join(part.get("text", "") for part in payload["candidates"][0]["content"]["parts"])


async def build_briefing(data: dict, settings: Settings, budget: CallBudget) -> dict:
    template = template_briefing(data)
    observed_at = (data.get("current") or {}).get("pm25_observed_at")
    result = {"text": template, "source": "template", "observed_at": observed_at}
    if not (settings.llm_api_key and settings.llm_model and settings.llm_provider in ("openai", "gemini")):
        return result
    if len(template) > 600:
        return result
    if not budget.allow():
        return result
    try:
        answer = await _call_llm(template, settings)
        if isinstance(answer, str) and _valid_answer(answer, template, data):
            return {"text": answer.strip(), "source": "llm", "observed_at": observed_at}
    except Exception:  # noqa: BLE001 - 외부 응답 오류는 모두 템플릿으로 복구한다.
        # 외부 응답·예외 본문에는 키나 요청 URL이 있을 수 있으므로 기록하지 않는다.
        pass
    return result
