"""기준선 예측.

#3의 학습 모델과 비교할 대상이자, 모델이 없을 때 쓰는 폴백이다.
외부 패키지를 쓰지 않는다(표준 라이브러리만). 서버 메모리 한도를 생각해 가볍게 둔다.

두 가지를 제공한다.
1. persistence: 마지막 관측값을 그대로 유지 — 대기질 예측의 표준 기준선
2. wind_rule: 바람이 산단 쪽에서 불어올 때만 상류 측정소 농도 쪽으로 보정

wind_rule은 인과 모형이 아니라 풍향·관측값으로 만든 참고 규칙이다.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

MODEL_NAME_PERSISTENCE = "baseline-persistence"
MODEL_NAME_WIND_RULE = "baseline-wind-rule"
VERSION = "0.1.0"

DEFAULT_TOLERANCE_DEG = 25.0   # 시설 방위 ±25도면 "산단 쪽 바람"
DEFAULT_ALPHA = 0.6            # 상류 농도를 반영하는 최대 비율
DEFAULT_DECAY_HOURS = 8.0      # 예측이 멀어질수록 보정을 줄인다
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Facility:
    """오염원 후보. bearing은 우리 동네에서 시설을 바라본 방위(도)."""

    name: str
    bearing_deg: float
    distance_km: float


# 순천 시청 기준 (docs/api.md와 같은 값)
SUNCHEON_FACILITIES: tuple[Facility, ...] = (
    Facility("광양제철소", 101.0, 24.0),
    Facility("여수국가산단", 117.0, 23.0),
)


@dataclass(frozen=True)
class Observation:
    time: datetime
    pm25: float | None = None
    wind_direction: float | None = None
    wind_speed: float | None = None


@dataclass(frozen=True)
class WeatherPoint:
    """예보 입력 한 시각."""

    time: datetime
    wind_direction: float | None = None
    wind_speed: float | None = None


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """1번 지점에서 2번 지점을 바라본 방위(도, 북 0 기준)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def angle_diff(a: float, b: float) -> float:
    """두 방위 사이의 최소 각도 차이(0~180)."""
    return abs((a - b + 180) % 360 - 180)


def transport_hours(distance_km: float, wind_speed_mps: float | None) -> float | None:
    """바람이 시설에서 우리 동네까지 오는 데 걸리는 대략 시간."""
    if not wind_speed_mps or wind_speed_mps <= 0.3:
        return None
    return distance_km / (wind_speed_mps * 3.6)


def influence_score(wind_direction: float | None, wind_speed: float | None,
                    facilities=SUNCHEON_FACILITIES, tolerance_deg: float = DEFAULT_TOLERANCE_DEG):
    """산단 쪽에서 바람이 오는 정도. (0~1 점수, 해당 시설 목록)

    풍향은 '바람이 불어오는 방향'(기상 관례)이라 시설 방위와 직접 비교한다.
    """
    if wind_direction is None:
        return 0.0, []
    matched = []
    best = 0.0
    for facility in facilities:
        diff = angle_diff(wind_direction, facility.bearing_deg)
        if diff > tolerance_deg:
            continue
        direction_term = 1.0 - diff / tolerance_deg          # 정확히 맞으면 1
        speed_term = min((wind_speed or 0.0) / 3.0, 1.0)     # 3 m/s 이상이면 1
        score = direction_term * max(speed_term, 0.3)        # 약한 바람도 조금은 반영
        matched.append(facility.name)
        best = max(best, score)
    return round(min(best, 1.0), 3), matched


def _last_value(history: list[Observation], attr: str = "pm25") -> float | None:
    for obs in sorted(history, key=lambda o: o.time, reverse=True):
        value = getattr(obs, attr)
        if value is not None:
            return float(value)
    return None


def _grade(pm25: float | None) -> str | None:
    if pm25 is None:
        return None
    if pm25 <= 15:
        return "좋음"
    if pm25 <= 35:
        return "보통"
    if pm25 <= 75:
        return "나쁨"
    return "매우나쁨"


def _influence(score: float | None, matched: list[str]) -> dict:
    """forecast[] 항목의 공통 구조. 판단 근거가 없으면 unknown으로 채운다."""
    if score is None:
        return {"level": "unknown", "score": None, "upwind_facilities": matched}
    level = "high" if score >= 0.6 else "medium" if score >= 0.3 else "low" if matched else "unknown"
    return {"level": level, "score": score, "upwind_facilities": matched}


def _hours(start: datetime, hours: int) -> list[datetime]:
    first = (start + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return [first + timedelta(hours=i) for i in range(hours)]


def persistence_forecast(target_history: list[Observation], hours: int = 12,
                         start: datetime | None = None) -> list[dict]:
    """마지막 관측값을 그대로 유지하는 기준선."""
    if not target_history:
        return []
    start = start or max(o.time for o in target_history)
    last = _last_value(target_history)
    result = []
    for i, t in enumerate(_hours(start, hours)):
        result.append({
            "forecast_time": t.isoformat(),
            "pm25_predicted": None if last is None else round(last, 1),
            "air_quality": _grade(last),
            "confidence": None if last is None else round(max(0.25, 0.6 - 0.03 * i), 2),
            "industrial_influence": _influence(None, []),
            "model": MODEL_NAME_PERSISTENCE,
        })
    return result


def wind_rule_forecast(target_history: list[Observation], upwind_history: list[Observation],
                       weather: list[WeatherPoint], hours: int = 12,
                       facilities=SUNCHEON_FACILITIES, alpha: float = DEFAULT_ALPHA,
                       start: datetime | None = None) -> list[dict]:
    """산단 쪽 바람이 불 때 상류 관측값 쪽으로 보정하는 기준선."""
    if not target_history:
        return []
    start = start or max(o.time for o in target_history)
    base = _last_value(target_history)
    upwind = _last_value(upwind_history) if upwind_history else None
    weather_by_time = {w.time.replace(minute=0, second=0, microsecond=0): w for w in weather}

    result = []
    for i, t in enumerate(_hours(start, hours)):
        point = weather_by_time.get(t)
        score, matched = influence_score(
            point.wind_direction if point else None,
            point.wind_speed if point else None,
            facilities,
        )
        value = base
        if base is not None and upwind is not None and score > 0 and upwind > base:
            lag = transport_hours(min(f.distance_km for f in facilities), point.wind_speed if point else None)
            # 바람이 없거나 너무 약하면 도달 시간을 알 수 없다. 모르는 것을 도달로 보지 않는다.
            arrived = lag is not None and (i + 1) >= lag               # 도달 시간 전에는 보정하지 않는다
            decay = math.exp(-(i + 1) / DEFAULT_DECAY_HOURS)          # 멀어질수록 보정 축소
            if arrived:
                value = base + alpha * score * decay * (upwind - base)
        # 예보가 비는 시각은 사실상 persistence다. 이름과 신뢰도를 낮춰 표시한다.
        ceiling, floor = (0.7, 0.3) if point else (0.6, 0.25)
        result.append({
            "forecast_time": t.isoformat(),
            "pm25_predicted": None if value is None else round(value, 1),
            "air_quality": _grade(value),
            "confidence": None if value is None else round(max(floor, ceiling - 0.03 * i), 2),
            "industrial_influence": _influence(score if point else None, matched),
            "model": MODEL_NAME_WIND_RULE if point else MODEL_NAME_PERSISTENCE,
        })
    return result


def predict(features: dict, hours: int = 12) -> list[dict]:
    """#1 명세가 요구하는 인터페이스.

    features = {
        "target_history": [Observation, ...],     # 우리 동네 관측
        "upwind_history": [Observation, ...],     # 상류(광양·여수) 관측
        "weather": [WeatherPoint, ...],           # 시간별 예보
        "facilities": [Facility, ...],            # 생략하면 순천 기본값
    }
    자료가 모자라면 persistence로 폴백한다. 실패해도 예외를 던지지 않는다.
    """
    target_history = features.get("target_history") or []
    if not target_history:
        return []
    upwind_history = features.get("upwind_history") or []
    weather = features.get("weather") or []
    facilities = features.get("facilities") or SUNCHEON_FACILITIES

    if not upwind_history or not weather:
        return persistence_forecast(target_history, hours)
    try:
        return wind_rule_forecast(target_history, upwind_history, weather, hours, facilities)
    except Exception:  # noqa: BLE001 - 예측 실패로 서비스가 죽으면 안 된다
        logger.exception("풍향 규칙 예측 실패; persistence 기준선으로 폴백합니다")
        return persistence_forecast(target_history, hours)


def describe(model_name: str, mae: float | None = None) -> dict:
    """응답 `model` 필드용 정보."""
    return {"name": model_name, "version": VERSION, "mae_validation": mae, "trained_at": None}
