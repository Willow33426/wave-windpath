"""수집 실행기. 외부 API가 죽어도 fixture로 시연이 되도록 폴백을 둔다."""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from app import db
from app.config import APP_DIR, Settings, Station, load_settings
from app.sources.parse import (
    KST,
    Record,
    UpstreamError,
    parse_airkorea,
    parse_kma_forecast,
    parse_kma_observation,
)

logger = logging.getLogger(__name__)

FIXTURE_DIR = APP_DIR / "fixtures"
META_LAST_COLLECTED = "last_collected_at"
META_LAST_FALLBACK = "last_run_used_fallback"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_fetch(settings: Settings):
    """키가 있을 때만 호출 모듈을 불러온다. 키·httpx가 없으면 None(=fixture 경로)."""
    if not settings.has_service_key:
        logger.info("서비스 키가 없어 fixture로 동작합니다")
        return None
    try:
        from app.sources import fetch  # httpx는 실제 수집 때만 필요하다
    except ModuleNotFoundError:
        logger.warning("httpx가 없어 fixture로 동작합니다 (pip install -r requirements.txt)")
        return None
    return fetch


def _rebase(records: list[Record], now: datetime) -> list[Record]:
    """fixture의 고정 시각을 실행 시각으로 평행 이동한다.

    고정 시각을 그대로 저장하면 며칠 뒤에는 `/api/observations`의 최근 24시간
    조회에서 전부 빠져 시연 화면이 비어 버린다. 레코드 사이의 간격은 그대로 두고
    가장 늦은 발표 시각이 현재 정시가 되도록 통째로 옮긴다.
    """
    if not records:
        return records
    delta = now.replace(minute=0, second=0, microsecond=0) - max(r.base_time for r in records)
    if not delta:
        return records
    return [replace(r, base_time=r.base_time + delta, target_time=r.target_time + delta)
            for r in records]


def _collect_station(station: Station, settings: Settings,
                     now: datetime | None = None) -> tuple[list[Record], bool]:
    """한 관측소의 대기질·실황·예보를 모은다. (레코드, 폴백여부)"""
    fetch = _load_fetch(settings)
    now = now or datetime.now(KST)
    records: list[Record] = []
    used_fallback = False

    for fixture_name, parser, caller in (
        ("airkorea_sample.json", parse_airkorea, "fetch_airkorea"),
        ("kma_nowcast_sample.json", parse_kma_observation, "fetch_kma_nowcast"),
        ("kma_forecast_sample.json", parse_kma_forecast, "fetch_kma_forecast"),
    ):
        try:
            if fetch is None:
                raise UpstreamError("서비스 키 또는 httpx 없음")
            payload = getattr(fetch, caller)(station, settings)
            records += parser(payload, station.key)
        except UpstreamError as exc:
            logger.warning("%s 수집 실패(%s) → fixture 사용: %s", caller, station.key, exc)
            fixture_records = parser(load_fixture(fixture_name), station.key)
            fixture_records = [replace(record, data_origin="fixture") for record in fixture_records]
            records += _rebase(fixture_records, now)
            used_fallback = True

    return records, used_fallback


def collect_once(settings: Settings | None = None, conn=None) -> dict:
    """모든 관측소를 한 번 수집해 저장하고 요약을 돌려준다."""
    settings = settings or load_settings()
    own_conn = conn is None
    conn = conn or db.connect(settings.db_path)
    collected_at = datetime.now(KST)
    total, fallback = 0, False

    try:
        for station in settings.stations:
            records, station_fallback = _collect_station(station, settings, collected_at)
            total += db.upsert_records(conn, records, collected_at)
            fallback = fallback or station_fallback
        db.set_meta(conn, META_LAST_COLLECTED, collected_at.isoformat())
        db.set_meta(conn, META_LAST_FALLBACK, "1" if fallback else "0")
    finally:
        if own_conn:
            conn.close()

    summary = {"collected_at": collected_at.isoformat(), "records": total, "is_fallback": fallback}
    logger.info("수집 완료: %s건, 폴백 %s", total, fallback)
    return summary


if __name__ == "__main__":  # 수동 실행: python -m app.collector
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(collect_once(), ensure_ascii=False, indent=2))
