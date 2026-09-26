"""시민 모드 화면용 응답 조립.

수집 DB의 최신 관측으로 AI 예측(시간별 릿지 회귀, app/forecast/ridge.py)을 돌리고,
입력이 모자라면 기준선(persistence·풍향 규칙)으로 폴백한다. 화면이 한 번에
사용할 수 있는 API 계약(docs/api.md) 형태로 반환한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from typing import Iterable

from app import db
from app.collector import META_LAST_FALLBACK
from app.config import Settings
from app.forecast import ridge
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
GOOD_PM25 = 15   # '좋음' 상한 (㎍/㎥)
FAIR_PM25 = 35   # '보통' 상한
HISTORY_HOURS = 26  # AI 입력에 24시간 전 값과 24시간 평균이 들어간다

# DB (관측소, 지표) → AI 입력 시계열 키
SERIES_KEYS = {
    ("suncheon", "pm25"): "pm",
    ("gwangyang", "pm25"): "gy",
    ("yeosu", "pm25"): "ys",
    ("suncheon", "wind_direction"): "wd",
    ("suncheon", "wind_speed"): "ws",
    ("suncheon", "temperature"): "tp",
    ("suncheon", "humidity"): "hu",
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


def _series(rows: Iterable) -> ridge.Series:
    series: ridge.Series = {key: {} for key in SERIES_KEYS.values()}
    for row in rows:
        key = SERIES_KEYS.get((row["station"], row["metric"]))
        if key and row["kind"] == "observation":
            series[key][ridge.hour_of(_time(row))] = float(row["value"])
    return series


def _quality(pm25: float | None) -> str | None:
    if pm25 is None:
        return None
    if pm25 <= GOOD_PM25:
        return "좋음"
    if pm25 <= FAIR_PM25:
        return "보통"
    if pm25 <= 75:
        return "나쁨"
    return "매우나쁨"


def _influence(score: float, facilities: list[str], wind_label: str | None,
               wind_speed: float | None) -> dict:
    # 바람의 방향만 말하고, 시설이 대기질의 원인이라고 말하지 않는다.
    if not facilities:
        return {
            "level": "low" if wind_label else "unknown",
            "score": score if wind_label else None,
            "upwind_facilities": [],
            "note": "지금은 산단 방향(동~동남동)에서 부는 바람이 아닙니다." if wind_label else "풍향 자료가 없어 판단하지 못했습니다.",
        }
    level = "high" if score >= 0.6 else "medium" if score >= 0.3 else "low"
    # 화면이 풍향·풍속을 따로 보여 주므로 문구에는 판단만 담는다.
    if level != "low":
        note = "산단 방향에서 바람이 불어옵니다. 오염 원인을 뜻하지는 않습니다."
    elif wind_speed is not None and wind_speed < 1.5:
        note = "산단 방향이지만 바람이 약합니다."
    else:
        note = "산단 방향에서 조금 비껴 부는 바람입니다."
    return {"level": level, "score": score, "upwind_facilities": facilities, "note": note}


@lru_cache(maxsize=1)
def _ridge_model() -> dict | None:
    return ridge.load_model()


def _ridge_forecast(rows: Iterable) -> list[dict] | None:
    """가장 최근 순천 PM2.5 관측 시각을 기준으로 1~14시간 뒤를 예측한다."""
    model = _ridge_model()
    if model is None:
        return None
    series = _series(rows)
    if not series["pm"]:
        return None
    predicted = ridge.predict(model, series, max(series["pm"]))
    if not predicted:
        return None
    return [{
        "forecast_time": time.isoformat(),
        "pm25_predicted": value,
        "air_quality": _quality(value),
        "confidence": None,
        "model": ridge.MODEL_NAME,
    } for time, value in predicted]


def _model_info(name: str) -> dict:
    model = _ridge_model()
    if name != ridge.MODEL_NAME or model is None:
        return describe(name)
    evaluation = model["evaluation"][0]  # 가장 긴 검증 구간(보수적인 값)을 대표로 쓴다
    return {
        "name": name,
        "version": model["version"],
        "trained_at": model["trained_at"],
        "mae_validation": evaluation["mae_model"],
        "mae_persistence": evaluation["mae_persistence"],
        "improvement_pct": evaluation["improvement_pct"],
        "validation_from": evaluation["validation_from"],
        "validation_to": evaluation["validation_to"],
    }


def _windows(forecast: list[dict]) -> list[dict]:
    """환기하기 좋은 연속 시간대. '좋음'이면서 산단 쪽 바람이 아닌 시간을 묶고,
    그런 시간이 없으면 가장 낮은 농도(+2㎍/㎥ 이내) 시간을 고른다."""
    usable = [
        item for item in forecast
        if item.get("pm25_predicted") is not None
        and (item.get("industrial_influence") or {}).get("level") not in ("medium", "high")
    ]
    picked = [item for item in usable if item["pm25_predicted"] <= GOOD_PM25]
    if not picked and usable:
        lowest = min(item["pm25_predicted"] for item in usable)
        picked = [item for item in usable if item["pm25_predicted"] <= lowest + 2]
    windows: list[dict] = []
    for item in picked:
        start = datetime.fromisoformat(item["forecast_time"])
        if windows and windows[-1]["end"] == start:
            windows[-1]["end"] = start + timedelta(hours=1)
        else:
            windows.append({"start": start, "end": start + timedelta(hours=1)})
    return windows[:2]


def _span(windows: list[dict]) -> str:
    """예: 13~18시, 21시~새벽 2시"""
    parts = []
    for w in windows:
        start, end = w["start"], w["end"]
        if end.hour == 0:
            parts.append(f"{start.hour}~24시")
        elif end.date() > start.date():
            parts.append(f"{start.hour}시~{'새벽 ' if end.hour < 6 else '다음 날 '}{end.hour}시")
        else:
            parts.append(f"{start.hour}~{end.hour}시")
    return ", ".join(parts)


def _recommendation(current: dict | None, forecast: list[dict]) -> dict:
    pm_now = (current or {}).get("pm25")
    industrial_now = ((current or {}).get("industrial_influence") or {}).get("level") in ("medium", "high")
    windows = _windows(forecast)
    span = _span(windows)
    all_day = (len(windows) == 1 and len(forecast) > 1
               and windows[0]["start"] == datetime.fromisoformat(forecast[0]["forecast_time"])
               and windows[0]["end"] == datetime.fromisoformat(forecast[-1]["forecast_time"]) + timedelta(hours=1))
    tail = "" if not windows else (f" 앞으로 {len(forecast)}시간 내내 괜찮아요." if all_day else f" 추천 시간: {span}")

    if pm_now is None:
        status, summary, text = "caution", "자료를 기다리는 중이에요", "최신 관측이 들어오면 다시 확인하세요."
    elif pm_now > FAIR_PM25:
        status, summary = "avoid", "지금은 창문을 닫아 두세요"
        text = "미세먼지가 '나쁨' 수준입니다." + (f" {span}에 짧게 환기하세요." if windows else " 환기는 아주 짧게만 하세요.")
    elif industrial_now:
        status, summary = "caution", "산단 쪽 바람, 짧게만 환기하세요"
        text = "산단 방향(동~동남동)에서 바람이 불고 있어요." + tail
    elif pm_now > GOOD_PM25:
        status, summary = "caution", "짧게 환기하세요"
        text = "미세먼지 '보통' 수준입니다. 10분 정도 짧게 환기하세요." + tail
    else:
        status, summary = "good", "지금 환기하기 좋아요"
        text = "미세먼지 '좋음'이고 산단 쪽 바람이 아닙니다." + tail

    values = [v for v in [pm_now] + [item.get("pm25_predicted") for item in forecast] if v is not None]
    peak = max(values, default=None)
    if peak is not None and peak > 75:
        outdoor_status, outdoor = "avoid", "민감군은 외출을 줄이고, 나갈 때는 마스크를 쓰세요."
    elif peak is not None and peak > FAIR_PM25:
        outdoor_status, outdoor = "caution", "민감군은 긴 야외 활동을 줄이세요."
    else:
        outdoor_status, outdoor = "good", "야외 활동하기 무난해요."

    serialized = [{"start": w["start"].isoformat(), "end": w["end"].isoformat()} for w in windows]
    return {
        "ventilation": {"status": status, "windows": serialized, "text": text},
        "outdoor": {"status": outdoor_status, "windows": serialized, "text": outdoor},
        "summary": summary,
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
        conn, since=now - timedelta(hours=HISTORY_HOURS), limit=2000, include_fixture=True,
    )
    weather = _weather(rows, now)
    raw_forecast = _ridge_forecast(rows)
    if raw_forecast is None:
        raw_forecast = predict({
            "target_history": _history(rows, "suncheon"),
            "upwind_history": _history(rows, "gwangyang") + _history(rows, "yeosu"),
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
    uses_ai = model_name == ridge.MODEL_NAME
    return {
        "location": LOCATION,
        "updated_at": now.isoformat(),
        "observed_at": observed_at.isoformat() if observed_at else None,
        "is_fallback": is_fallback,
        "data_sources": sources,
        "current": current,
        "forecast": forecast,
        "recommendation": _recommendation(current, forecast),
        "reason": (
            "순천·광양·여수 측정소 PM2.5와 순천 바람·기온·습도로 학습한 AI 예측입니다. "
            if uses_ai else
            "기상청 풍향·풍속 예보와 순천·인근 측정소 PM2.5를 함께 본 기준선 예측입니다. "
        ) + "특정 시설과의 인과관계를 뜻하지 않습니다.",
        "model": _model_info(model_name),
        "evidence": (_ridge_model() or {}).get("evidence"),
    }
