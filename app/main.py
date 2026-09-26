"""Wave 바람길 FastAPI 앱.

`python app/main.py`로도 실행되도록 저장소 루트를 sys.path에 넣는다.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from sqlite3 import Row

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from app import db
from app.collector import META_LAST_COLLECTED, META_LAST_FALLBACK
from app.config import load_settings
from app.forecast.baseline import Observation, WeatherPoint, describe, influence_score, predict
from app.scheduler import run_collector_loop
from app.sources.parse import KST, air_quality_grade, direction_label

VERSION = "0.1.0"
STALE_AFTER = timedelta(hours=2)
SUNCHEON_LOCATION = {
    "id": "suncheon",
    "name": "순천시",
    "lat": 34.9506,
    "lon": 127.4872,
    "air_station": "순천 연향동",
    "weather_station": "순천(ASOS 174)",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
settings = load_settings()
STATIC_INDEX = Path(__file__).with_name("static") / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.conn = db.connect(settings.db_path)
    app.state.collector = asyncio.create_task(run_collector_loop(settings, app.state.conn))
    logger.info("수집 스케줄러 시작 (%s분 주기)", settings.collect_interval_minutes)
    try:
        yield
    finally:
        app.state.collector.cancel()
        try:
            await app.state.collector
        except asyncio.CancelledError:
            pass
        app.state.conn.close()


app = FastAPI(title="Wave 바람길", version=VERSION, lifespan=lifespan)


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _num(row: Row | None) -> float | None:
    return None if row is None else float(row["value"])


def _latest(conn, station: str, metric: str) -> Row | None:
    return db.latest_observation(conn, station, metric)


def _history(conn, station: str, metric: str, since: datetime) -> list[Observation]:
    rows = db.query_measurements(
        conn,
        station=station,
        metric=metric,
        kind="observation",
        since=since,
        limit=500,
    )
    result = []
    for row in rows:
        when = _dt(row["target_time"])
        if when is not None:
            result.append(Observation(time=when, pm25=float(row["value"])))
    return sorted(result, key=lambda item: item.time)


def _weather(conn, station: str, since: datetime) -> tuple[list[WeatherPoint], dict[str, dict]]:
    rows = db.query_measurements(conn, station=station, kind="forecast", since=since, limit=1000)
    by_time: dict[str, dict] = {}
    for row in rows:
        target = _dt(row["target_time"])
        if target is None:
            continue
        key = target.replace(minute=0, second=0, microsecond=0).isoformat()
        bucket = by_time.setdefault(key, {"time": target.replace(minute=0, second=0, microsecond=0)})
        bucket[row["metric"]] = float(row["value"])
    points = [
        WeatherPoint(
            time=item["time"],
            wind_direction=item.get("wind_direction"),
            wind_speed=item.get("wind_speed"),
        )
        for item in by_time.values()
        if "wind_direction" in item or "wind_speed" in item
    ]
    return sorted(points, key=lambda item: item.time), by_time


def _influence_block(wind_direction: float | None, wind_speed: float | None) -> dict:
    score, facilities = influence_score(wind_direction, wind_speed)
    if wind_direction is None:
        return {
            "level": "unknown",
            "score": None,
            "upwind_facilities": [],
            "note": "풍향 자료가 없어 산단 방향 영향을 판단하지 못했습니다.",
        }
    if not facilities:
        label = direction_label(wind_direction) or "해당 방향"
        return {
            "level": "low",
            "score": score,
            "upwind_facilities": [],
            "note": f"{label}풍이라 산단 방향 바람으로 보지 않습니다.",
        }
    level = "high" if score >= 0.6 else "medium" if score >= 0.3 else "low"
    label = direction_label(wind_direction) or "해당 방향"
    names = "·".join(facilities)
    return {
        "level": level,
        "score": score,
        "upwind_facilities": facilities,
        "note": f"{label}풍 {wind_speed or 0:g} m/s. {names} 방향에서 바람이 오는 참고 상황입니다.",
    }


def _window(items: list[dict], status: str) -> list[dict]:
    if status == "avoid":
        return []
    candidates = [
        item for item in items
        if item.get("pm25_predicted") is not None
        and item["pm25_predicted"] <= 35
        and item["industrial_influence"]["level"] in ("low", "unknown")
    ]
    if not candidates:
        return []
    start = candidates[0]["forecast_time"]
    end_time = _dt(candidates[0]["forecast_time"])
    if end_time is None:
        return []
    return [{"start": start, "end": (end_time + timedelta(hours=1)).isoformat()}]


def _recommendation(items: list[dict], is_fallback: bool) -> dict:
    values = [item["pm25_predicted"] for item in items if item.get("pm25_predicted") is not None]
    worst = max(values) if values else None
    has_facility_wind = any(item["industrial_influence"]["level"] in ("medium", "high") for item in items)

    if is_fallback:
        ventilation_status = outdoor_status = "caution"
        ventilation_text = "최신 자료가 지연되어 참고용으로만 확인하고, 복구 후 다시 판단하세요."
        outdoor_text = "민감군은 실시간 대기질 정보를 함께 확인한 뒤 외출을 결정하세요."
        summary = "자료 지연 상태라 예측 신뢰도가 낮습니다."
    elif worst is None:
        ventilation_status = outdoor_status = "caution"
        ventilation_text = "예측 농도가 없어 환기 권고를 보류합니다."
        outdoor_text = "관측값이 들어오면 외출 권고를 다시 확인하세요."
        summary = "예측 데이터가 부족합니다."
    elif worst > 75:
        ventilation_status = outdoor_status = "avoid"
        ventilation_text = "미세먼지 농도가 높아질 수 있어 환기를 피하세요."
        outdoor_text = "장시간 외출은 피하고 마스크 착용을 검토하세요."
        summary = "대기질 악화 가능성이 큽니다."
    elif worst > 35 or has_facility_wind:
        ventilation_status = outdoor_status = "caution"
        ventilation_text = "농도가 낮고 산단 방향 바람이 약한 시간대에 짧게 환기하세요."
        outdoor_text = "민감군은 농도가 낮아지는 시간대를 선택하세요."
        summary = "일부 시간대는 주의가 필요합니다."
    else:
        ventilation_status = outdoor_status = "good"
        ventilation_text = "짧은 환기가 가능한 상태입니다."
        outdoor_text = "일상적인 야외 활동이 가능한 수준입니다."
        summary = "전반적으로 보통 이하의 대기질입니다."

    return {
        "ventilation": {
            "status": ventilation_status,
            "windows": _window(items, ventilation_status),
            "text": ventilation_text,
        },
        "outdoor": {
            "status": outdoor_status,
            "windows": _window(items, outdoor_status),
            "text": outdoor_text,
        },
        "summary": summary,
    }


def _source(row: Row | None, name: str, provider: str, fallback: bool) -> dict | None:
    if row is None:
        return None
    note = "외부 API 지연으로 fixture 또는 캐시를 사용했습니다." if fallback else None
    return {
        "name": name,
        "provider": provider,
        "observed_at": row["target_time"],
        "fetched_at": row["collected_at"],
        "note": note,
    }


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC_INDEX, media_type="text/html; charset=utf-8")


@app.get("/api/health")
async def health() -> dict:
    conn = app.state.conn
    now = datetime.now(KST)
    try:
        last_collected = db.get_meta(conn, META_LAST_COLLECTED)
        is_fallback = db.get_meta(conn, META_LAST_FALLBACK, "0") == "1"
        db_status = "ok"
    except Exception:  # noqa: BLE001 - 상태 확인은 실패해도 응답해야 한다
        last_collected, is_fallback, db_status = None, False, "error"

    status = "ok"
    if db_status != "ok" or last_collected is None:
        status = "degraded"
    else:
        try:
            if now - datetime.fromisoformat(last_collected) > STALE_AFTER:
                status = "degraded"
        except ValueError:
            status = "degraded"

    return {
        "status": status,
        "version": VERSION,
        "time": now.isoformat(),
        "db": db_status,
        "last_collected_at": last_collected,
        "is_fallback": is_fallback,
    }


@app.get("/api/citizen/forecast")
@app.get("/citizen/forecast")
async def citizen_forecast(
    location: str = Query("suncheon", description="지역 코드. v0.1은 suncheon만 지원"),
    hours: int = Query(12, ge=1, le=24, description="예측 시간 수"),
) -> dict:
    if location != "suncheon":
        raise HTTPException(status_code=400, detail={
            "error": {"code": "INVALID_PARAMETER", "message": "지원하지 않는 지역입니다", "field": "location"}
        })

    conn = app.state.conn
    now = datetime.now(KST)
    since = now - timedelta(hours=72)
    is_fallback = db.get_meta(conn, META_LAST_FALLBACK, "0") == "1"

    pm25_row = _latest(conn, "suncheon", "pm25")
    if pm25_row is None:
        raise HTTPException(status_code=503, detail={
            "error": {
                "code": "UPSTREAM_UNAVAILABLE",
                "message": "대기질 데이터를 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.",
                "retry_after_sec": 300,
            }
        })

    pm10_row = _latest(conn, "suncheon", "pm10")
    wind_direction_row = _latest(conn, "suncheon", "wind_direction")
    wind_speed_row = _latest(conn, "suncheon", "wind_speed")
    temperature_row = _latest(conn, "suncheon", "temperature")

    target_history = _history(conn, "suncheon", "pm25", since)
    upwind_history: list[Observation] = []
    for station in ("gwangyang", "yeosu"):
        upwind_history.extend(_history(conn, station, "pm25", since))
    weather, weather_by_time = _weather(conn, "suncheon", now - timedelta(hours=1))

    forecast_items = predict({
        "target_history": target_history,
        "upwind_history": upwind_history,
        "weather": weather,
    }, hours=hours)

    forecast = []
    for item in forecast_items:
        weather_item = weather_by_time.get(item["forecast_time"], {})
        wind_direction = weather_item.get("wind_direction")
        wind_speed = weather_item.get("wind_speed")
        influence = _influence_block(wind_direction, wind_speed)
        forecast.append({
            "forecast_time": item["forecast_time"],
            "pm25_predicted": item["pm25_predicted"],
            "air_quality": item["air_quality"],
            "wind_direction": None if wind_direction is None else round(wind_direction),
            "wind_direction_label": direction_label(wind_direction),
            "wind_speed_mps": wind_speed,
            "industrial_influence": influence,
            "confidence": item["confidence"],
            "is_fallback": is_fallback or item.get("model") == "baseline-persistence",
        })

    wind_direction = _num(wind_direction_row)
    wind_speed = _num(wind_speed_row)
    current_influence = _influence_block(wind_direction, wind_speed)
    model_name = forecast_items[0]["model"] if forecast_items else "baseline-persistence"
    observed_times = [
        _dt(row["target_time"])
        for row in (pm25_row, pm10_row, wind_direction_row, wind_speed_row, temperature_row)
        if row is not None
    ]
    observed_at = max((item for item in observed_times if item is not None), default=None)

    sources = [
        _source(pm25_row, "대기오염정보 조회서비스", "한국환경공단 에어코리아", is_fallback),
        _source(wind_direction_row or wind_speed_row, "단기예보·초단기실황 조회서비스", "기상청", is_fallback),
    ]
    sources = [source for source in sources if source is not None]

    return {
        "location": SUNCHEON_LOCATION,
        "updated_at": now.isoformat(),
        "observed_at": observed_at.isoformat() if observed_at else None,
        "is_fallback": is_fallback,
        "data_sources": sources,
        "current": {
            "pm25": _num(pm25_row),
            "pm10": _num(pm10_row),
            "air_quality": air_quality_grade(_num(pm25_row)),
            "wind_direction": None if wind_direction is None else round(wind_direction),
            "wind_direction_label": direction_label(wind_direction),
            "wind_speed_mps": wind_speed,
            "temperature_c": _num(temperature_row),
            "industrial_influence": current_influence,
        },
        "forecast": forecast,
        "recommendation": _recommendation(forecast, is_fallback),
        "reason": (
            "풍향, 상류 측정소 PM2.5, 순천 관측값을 함께 참고한 기준선 예측입니다. "
            "특정 오염원을 원인으로 단정하지 않습니다."
        ),
        "model": describe(model_name),
    }


@app.get("/api/observations")
async def observations(
    station: str | None = Query(None, description="관측소 키 (suncheon·gwangyang·yeosu)"),
    metric: str | None = Query(None, description="지표 (pm25·pm10·wind_direction·wind_speed 등)"),
    kind: str | None = Query(None, pattern="^(observation|forecast)$"),
    hours: int = Query(24, ge=1, le=720, description="최근 몇 시간"),
    limit: int = Query(200, ge=1, le=2000),
) -> dict:
    """수집·정규화된 값을 그대로 돌려준다. 시민 모드 응답 조립은 #3·#6에서 한다."""
    known = {s.key for s in settings.stations}
    if station and station not in known:
        raise HTTPException(status_code=400, detail={
            "error": {"code": "INVALID_PARAMETER", "message": "지원하지 않는 관측소입니다", "field": "station"}
        })

    conn = app.state.conn
    since = datetime.now(KST) - timedelta(hours=hours)
    rows = db.query_measurements(conn, station=station, metric=metric, kind=kind, since=since, limit=limit)
    return {
        "count": len(rows),
        "is_fallback": db.get_meta(conn, META_LAST_FALLBACK, "0") == "1",
        "last_collected_at": db.get_meta(conn, META_LAST_COLLECTED),
        "items": db.rows_to_dicts(rows),
    }


if __name__ == "__main__":
    uvicorn.run(app, host=settings.internal_host, port=settings.internal_port)
