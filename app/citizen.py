"""시민 모드 화면용 응답 조립.

수집 DB의 최신 관측·예보를 기준선 모델 입력으로 바꾸고, 화면이 한 번에
사용할 수 있는 API 계약(docs/api.md) 형태로 반환한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from app import db
from app.collector import META_LAST_FALLBACK
from app.config import Settings
from app.forecast.baseline import (
    MODEL_NAME_PERSISTENCE,
    Observation,
    WeatherPoint,
    describe,
    influence_score,
    predict,
)
from app.sources.parse import KST, direction_label

LOCATION = {
    "id": "suncheon",
    "name": "순천시",
    "lat": 34.9506,
    "lon": 127.4872,
    "air_station": "순천 연향동",
    "weather_station": "기상청 단기예보 순천 격자",
}


def _time(row, field: str = "target_time") -> datetime:
    return datetime.fromisoformat(row[field])


def _latest(rows: Iterable, *, station: str, kind: str, metric: str):
    candidates = [
        row for row in rows
        if row["station"] == station and row["kind"] == kind and row["metric"] == metric
    ]
    return max(candidates, key=_time, default=None)


def _history(rows: Iterable, station: str) -> list[Observation]:
    return [
        Observation(_time(row), pm25=float(row["value"]))
        for row in rows
        if row["station"] == station and row["kind"] == "observation" and row["metric"] == "pm25"
    ]


def _weather(rows: Iterable, now: datetime) -> list[WeatherPoint]:
    by_time: dict[datetime, dict[str, float]] = {}
    for row in rows:
        if row["station"] != "suncheon" or row["kind"] != "forecast":
            continue
        target = _time(row).replace(minute=0, second=0, microsecond=0)
        if target < now.replace(minute=0, second=0, microsecond=0):
            continue
        by_time.setdefault(target, {})[row["metric"]] = float(row["value"])
    return [
        WeatherPoint(time, values.get("wind_direction"), values.get("wind_speed"))
        for time, values in sorted(by_time.items())
    ]


def _quality(pm25: float | None) -> str | None:
    if pm25 is None:
        return None
    if pm25 <= 15:
        return "좋음"
    if pm25 <= 35:
        return "보통"
    if pm25 <= 75:
        return "나쁨"
    return "매우나쁨"


def _influence(score: float, facilities: list[str], wind_label: str | None,
               wind_speed: float | None) -> dict:
    if not facilities:
        return {
            "level": "low" if wind_label else "unknown",
            "score": score if wind_label else None,
            "upwind_facilities": [],
            "note": "지금은 산단 방향(동~동남동)에서 부는 바람이 아닙니다." if wind_label else "풍향 자료가 없어 판단하지 못했습니다.",
        }
    level = "high" if score >= 0.6 else "medium" if score >= 0.3 else "low"
    speed = "" if wind_speed is None else f" {wind_speed:g}m/s"
    # 바람의 방향만 말하고, 시설이 대기질의 원인이라고 말하지 않는다.
    return {
        "level": level,
        "score": score,
        "upwind_facilities": facilities,
        "note": f"{wind_label or '해당'}풍{speed}: 산단 방향에서 바람이 불어옵니다. 오염 원인을 뜻하지는 않습니다.",
    }


def _recommendation(current_pm25: float | None, forecast: list[dict]) -> dict:
    values = [item["pm25_predicted"] for item in forecast if item.get("pm25_predicted") is not None]
    peak = max(([current_pm25] if current_pm25 is not None else []) + values, default=None)
    if peak is None:
        status, text = "caution", "자료가 부족합니다. 최신 정보가 들어오면 다시 확인하세요."
    elif peak > 35:
        status, text = "avoid", "미세먼지 농도가 높아질 수 있어 긴 환기는 피하고 짧게 환기하세요."
    elif peak > 15:
        status, text = "caution", "농도 변화를 확인하며 비교적 낮은 시간대에 짧게 환기하세요."
    else:
        status, text = "good", "현재 예측 범위에서는 환기하기 무난합니다."
    outdoor = "민감군은 농도 변화를 확인하고 장시간 야외 활동을 조절하세요." if peak and peak > 35 else "일반적인 야외 활동이 가능하지만 최신 수치를 함께 확인하세요."
    return {
        "ventilation": {"status": status, "windows": [], "text": text},
        "outdoor": {"status": "caution" if peak and peak > 35 else "good", "windows": [], "text": outdoor},
        "summary": text,
    }


def build_citizen_forecast(conn, settings: Settings, location: str = "suncheon",
                           hours: int = 12, now: datetime | None = None) -> dict:
    """최신 DB 상태를 시민 모드 API 계약으로 조립한다."""
    if location != "suncheon":
        raise ValueError("지원하지 않는 지역입니다")
    if not 1 <= hours <= 24:
        raise ValueError("hours는 1~24여야 합니다")

    now = now or datetime.now(KST)
    rows = db.query_measurements(
        conn, since=now - timedelta(hours=24), limit=2000, include_fixture=True,
    )
    target_history = _history(rows, "suncheon")
    upwind_history = _history(rows, "gwangyang") + _history(rows, "yeosu")
    weather = _weather(rows, now)
    raw_forecast = predict({
        "target_history": target_history,
        "upwind_history": upwind_history,
        "weather": weather,
    }, hours + 24)
    # 관측이 한두 시간 늦게 들어와도 이미 지난 예측 시각을 화면에 내보내지 않는다.
    forecast = [
        item for item in raw_forecast
        if datetime.fromisoformat(item["forecast_time"]) > now
    ][:hours]

    weather_by_time = {point.time: point for point in weather}
    # 과거 fixture 행이 DB에 남아 있어도 가장 최근 수집이 실데이터면 폴백으로
    # 표시하지 않는다. collector가 매 수집마다 기록하는 meta가 현재 상태의 기준이다.
    is_fallback = db.get_meta(conn, META_LAST_FALLBACK, "0") == "1"
    for item in forecast:
        target = datetime.fromisoformat(item["forecast_time"])
        point = weather_by_time.get(target)
        direction = point.wind_direction if point else None
        speed = point.wind_speed if point else None
        score, facilities = influence_score(direction, speed)
        item.update({
            "wind_direction": direction,
            "wind_direction_label": direction_label(direction),
            "wind_speed_mps": speed,
            "industrial_influence": _influence(score, facilities, direction_label(direction), speed),
            "is_fallback": is_fallback,
        })

    latest = {
        metric: _latest(rows, station="suncheon", kind="observation", metric=metric)
        for metric in ("pm25", "pm10", "wind_direction", "wind_speed", "temperature")
    }
    values = {key: (float(row["value"]) if row else None) for key, row in latest.items()}
    observed_rows = [row for row in latest.values() if row]
    observed_at = max((_time(row) for row in observed_rows), default=None)
    wind_score, wind_facilities = influence_score(values["wind_direction"], values["wind_speed"])
    current = None
    if observed_rows:
        current = {
            "pm25": values["pm25"],
            "pm10": values["pm10"],
            "air_quality": _quality(values["pm25"]),
            "wind_direction": values["wind_direction"],
            "wind_direction_label": direction_label(values["wind_direction"]),
            "wind_speed_mps": values["wind_speed"],
            "temperature_c": values["temperature"],
            "industrial_influence": _influence(
                wind_score, wind_facilities, direction_label(values["wind_direction"]), values["wind_speed"],
            ),
        }

    sources = []
    for source, name, provider in (
        ("kma", "단기예보 조회서비스", "기상청"),
        ("airkorea", "대기오염정보 조회서비스", "한국환경공단 에어코리아"),
    ):
        source_rows = [row for row in rows if row["source"] == source]
        if not source_rows:
            continue
        observed = [row for row in source_rows if row["kind"] == "observation"]
        sources.append({
            "name": name,
            "provider": provider,
            "observed_at": max((_time(row) for row in observed), default=None).isoformat() if observed else None,
            "fetched_at": max(datetime.fromisoformat(row["collected_at"]) for row in source_rows).isoformat(),
            "note": "대체 자료가 포함되었습니다." if is_fallback and any(row["data_origin"] == "fixture" for row in source_rows) else None,
        })

    model_name = forecast[0].get("model", MODEL_NAME_PERSISTENCE) if forecast else MODEL_NAME_PERSISTENCE
    recommendation = _recommendation(values["pm25"], forecast)
    return {
        "location": LOCATION,
        "updated_at": now.isoformat(),
        "observed_at": observed_at.isoformat() if observed_at else None,
        "is_fallback": is_fallback,
        "data_sources": sources,
        "current": current,
        "forecast": forecast,
        "recommendation": recommendation,
        "reason": "기상청 풍향·풍속 예보와 순천·인근 측정소 PM2.5를 함께 본 참고 예측입니다. 특정 시설과의 인과관계를 뜻하지 않습니다.",
        "model": describe(model_name),
    }
