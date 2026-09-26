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

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from app import db
from app.collector import META_LAST_COLLECTED, META_LAST_FALLBACK
from app.citizen import build_citizen_forecast
from app.config import load_settings
from app.scheduler import run_collector_loop
from app.sources.parse import KST

VERSION = "0.1.0"
STALE_AFTER = timedelta(hours=2)

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


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC_INDEX, media_type="text/html; charset=utf-8")


@app.get("/health", include_in_schema=False)
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


@app.get("/observations", include_in_schema=False)
@app.get("/api/observations")
async def observations(
    station: str | None = Query(None, description="관측소 키 (suncheon·gwangyang·yeosu)"),
    metric: str | None = Query(None, description="지표 (pm25·pm10·wind_direction·wind_speed 등)"),
    kind: str | None = Query(None, pattern="^(observation|forecast)$"),
    hours: int = Query(24, ge=1, le=720, description="최근 몇 시간"),
    limit: int = Query(200, ge=1, le=2000),
    include_fixture: bool = Query(True, description="fixture 샘플 포함 여부"),
) -> dict:
    """수집·정규화된 값을 그대로 돌려준다. 시민 모드 응답 조립은 #3·#6에서 한다."""
    known = {s.key for s in settings.stations}
    if station and station not in known:
        raise HTTPException(status_code=400, detail={
            "error": {"code": "INVALID_PARAMETER", "message": "지원하지 않는 관측소입니다", "field": "station"}
        })

    conn = app.state.conn
    since = datetime.now(KST) - timedelta(hours=hours)
    rows = db.query_measurements(
        conn, station=station, metric=metric, kind=kind, since=since,
        limit=limit, include_fixture=include_fixture,
    )
    return {
        "count": len(rows),
        "is_fallback": db.get_meta(conn, META_LAST_FALLBACK, "0") == "1",
        "last_collected_at": db.get_meta(conn, META_LAST_COLLECTED),
        "items": db.rows_to_dicts(rows),
    }


@app.get("/citizen/forecast", include_in_schema=False)
@app.get("/api/citizen/forecast")
async def citizen_forecast(
    location: str = Query("suncheon"),
    hours: int = Query(12),
) -> dict:
    """시민 화면에 필요한 현재 상태·12시간 예측·권고를 한 번에 반환한다."""
    try:
        return build_citizen_forecast(app.state.conn, settings, location, hours)
    except ValueError as exc:
        field = "location" if location != "suncheon" else "hours"
        raise HTTPException(status_code=400, detail={
            "error": {"code": "INVALID_PARAMETER", "message": str(exc), "field": field}
        }) from exc


if __name__ == "__main__":
    uvicorn.run(app, host=settings.internal_host, port=settings.internal_port)
