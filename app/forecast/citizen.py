"""#1 JSON 계약에 맞춰 #3 예측 결과를 시민 응답으로 묶는다."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from app import db
from app.collector import META_LAST_FALLBACK
from app.forecast import baseline, model
from app.forecast.inputs import build_features
from app.sources.parse import KST


LOCATION = {"id": "suncheon", "name": "순천시", "lat": 34.9506,
            "lon": 127.4872, "air_station": "순천 연향동",
            "weather_station": "순천 기상청 격자(70, 70)"}
WIND_LABELS = ("북", "북북동", "북동", "동북동", "동", "동남동", "남동", "남남동",
               "남", "남남서", "남서", "서남서", "서", "서북서", "북서", "북북서")


def _latest(conn: sqlite3.Connection, station: str, metric: str,
            now: datetime, include_fixture: bool = False) -> sqlite3.Row | None:
    has_origin = any(row["name"] == "data_origin"
                     for row in conn.execute("PRAGMA table_info(measurements)"))
    fixture_clause = "" if include_fixture or not has_origin else "AND data_origin != 'fixture' "
    return conn.execute(
        "SELECT * FROM measurements WHERE station=? AND metric=? "
        "AND kind='observation' AND target_time<=? AND collected_at<=? " +
        fixture_clause + "ORDER BY target_time DESC LIMIT 1",
        (station, metric, now.isoformat(), now.isoformat()),
    ).fetchone()


def _label(direction: float | None) -> str | None:
    if direction is None:
        return None
    return WIND_LABELS[int((direction % 360 + 11.25) // 22.5) % 16]


def _source(name: str, provider: str, row: sqlite3.Row | None,
            fallback: bool) -> dict:
    return {"name": name, "provider": provider,
            "observed_at": row["target_time"] if row else None,
            "fetched_at": row["collected_at"] if row else None,
            "note": "샘플 또는 캐시 데이터가 사용됐습니다." if fallback else None}


def _influence(direction: float | None, speed: float | None,
               has_upwind: bool) -> dict:
    if not has_upwind or direction is None or speed is None:
        result = baseline._influence(None, [])
    else:
        score, matched = baseline.influence_score(direction, speed)
        result = baseline._influence(score, matched)
    result["note"] = ("산단 방향 바람과 상류 관측을 비교한 참고 지표입니다."
                      if result["level"] != "unknown" else None)
    return result


def _recommendation(forecast: list[dict]) -> dict:
    """#4 브리핑 전까지 쓸 보수적인 기본 문구."""
    usable = [item for item in forecast if item["pm25_predicted"] is not None]
    if not usable:
        status, text = "caution", "예측에 필요한 관측값이 부족합니다."
    elif any(item["air_quality"] in ("나쁨", "매우나쁨") for item in usable):
        status, text = "caution", "대기질이 나쁜 시간대가 예상됩니다. 시간별 예측을 확인하세요."
    else:
        status, text = "good", "시간별 대기질 예측을 확인하고 활동 시간을 정하세요."
    return {"ventilation": {"status": status, "windows": [], "text": text},
            "outdoor": {"status": status, "windows": [], "text": text},
            "summary": text}


def build_response(conn: sqlite3.Connection, hours: int = 12,
                   now: datetime | None = None) -> dict:
    now = now or datetime.now(KST)
    fixture = db.get_meta(conn, META_LAST_FALLBACK, "0") == "1"
    features = build_features(conn, hours=hours, now=now, include_fixture=fixture)
    pm25 = _latest(conn, "suncheon", "pm25", now, fixture)
    pm10 = _latest(conn, "suncheon", "pm10", now, fixture)
    direction = _latest(conn, "suncheon", "wind_direction", now, fixture)
    speed = _latest(conn, "suncheon", "wind_speed", now, fixture)
    temperature = _latest(conn, "suncheon", "temperature", now, fixture)

    direction_value = direction["value"] if direction else None
    speed_value = speed["value"] if speed else None
    current = None
    if pm25 or pm10 or direction or speed or temperature:
        current = {
            "pm25": pm25["value"] if pm25 else None,
            "pm10": pm10["value"] if pm10 else None,
            "air_quality": baseline._grade(pm25["value"] if pm25 else None),
            "wind_direction": round(direction_value) if direction else None,
            "wind_direction_label": _label(direction_value),
            "wind_speed_mps": speed_value,
            "temperature_c": temperature["value"] if temperature else None,
            "industrial_influence": _influence(
                direction_value, speed_value, bool(features["upwind_history"])),
        }

    weather = {point.time.isoformat(): point for point in features["weather"]}
    predicted = model.predict(features, hours=hours) if pm25 else []
    forecast = []
    for item in predicted:
        if datetime.fromisoformat(item["forecast_time"]) <= now:
            continue
        point = weather.get(item["forecast_time"])
        wind_direction = point.wind_direction if point else None
        wind_speed = point.wind_speed if point else None
        influence = dict(item["industrial_influence"])
        influence["note"] = ("산단 방향 바람과 상류 관측을 비교한 참고 지표입니다."
                             if influence["level"] != "unknown" else None)
        forecast.append({
            "forecast_time": item["forecast_time"],
            "pm25_predicted": item["pm25_predicted"],
            "air_quality": item["air_quality"],
            "wind_direction": round(wind_direction) if wind_direction is not None else None,
            "wind_direction_label": _label(wind_direction),
            "wind_speed_mps": wind_speed,
            "industrial_influence": influence,
            "confidence": None if fixture else item["confidence"],
            "is_fallback": fixture or item["model"] != model.MODEL_NAME,
        })

    observed_rows = [row for row in (pm25, pm10, direction, speed, temperature) if row]
    observed_at = max((row["target_time"] for row in observed_rows), default=None)
    air_source = pm25 or pm10
    weather_source = direction or speed or temperature
    name = predicted[0]["model"] if predicted else baseline.MODEL_NAME_PERSISTENCE
    return {
        "location": dict(LOCATION), "updated_at": now.isoformat(),
        "observed_at": observed_at, "is_fallback": fixture,
        "data_sources": [
            _source("대기오염정보 조회서비스", "한국환경공단 에어코리아", air_source, fixture),
            _source("기상청 단기예보·초단기실황", "기상청", weather_source, fixture),
        ],
        "current": current, "forecast": forecast,
        "recommendation": _recommendation(forecast),
        "reason": ("실측 PM2.5와 풍향·풍속을 바탕으로 계산한 참고 예측입니다."
                   if forecast else "예측에 필요한 순천 PM2.5 관측값이 없습니다."),
        "model": baseline.describe(name),
    }
