"""공공 API 호출. 타임아웃·재시도만 담당하고 정규화는 parse.py가 한다.

키는 Settings에서만 받아 쓰고, 로그·예외 메시지에 남기지 않는다.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import httpx

from app.config import Settings, Station
from app.sources.parse import KST, UpstreamError

logger = logging.getLogger(__name__)
# httpx의 INFO 요청 로그에는 serviceKey가 포함된 전체 URL이 기록된다.
# 수집기와 운영 로그에서 인증키가 노출되지 않도록 요청 요약 로그를 차단한다.
logging.getLogger("httpx").setLevel(logging.WARNING)

AIRKOREA_URL = ("https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/"
                "getMsrstnAcctoRltmMesureDnsty")
KMA_FORECAST_URL = ("https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/"
                    "getVilageFcst")
KMA_NOWCAST_URL = ("https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/"
                   "getUltraSrtNcst")


def _get_json(url: str, params: dict, settings: Settings) -> dict:
    """재시도 포함 GET. 실패하면 UpstreamError."""
    last_error = "원인 미상"
    for attempt in range(settings.request_retries + 1):
        try:
            with httpx.Client(timeout=settings.request_timeout_sec) as client:
                response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:  # ValueError: JSON 아님
            last_error = type(exc).__name__
            logger.warning("공공 API 호출 실패 (%s) 시도 %s/%s",
                           last_error, attempt + 1, settings.request_retries + 1)
            if attempt < settings.request_retries:
                time.sleep(1.5 * (attempt + 1))
    raise UpstreamError(f"공공 API 호출 실패: {last_error}")


def fetch_airkorea(station: Station, settings: Settings) -> dict:
    if not station.air_station:
        raise UpstreamError("에어코리아 측정소가 지정되지 않았습니다")
    params = {
        "serviceKey": settings.service_key,
        "returnType": "json",
        "numOfRows": 1,
        "pageNo": 1,
        "stationName": station.air_station,
        "dataTerm": "DAILY",
        "ver": "1.3",
    }
    return _get_json(AIRKOREA_URL, params, settings)


def _kma_base_time(now: datetime) -> tuple[str, str]:
    """단기예보 발표 시각(02·05·08·11·14·17·20·23시) 중 직전 회차."""
    slots = [2, 5, 8, 11, 14, 17, 20, 23]
    target = now - timedelta(minutes=45)  # 발표 후 제공 지연 고려
    hour = max([h for h in slots if h <= target.hour], default=None)
    if hour is None:
        target = target - timedelta(days=1)
        hour = 23
    return target.strftime("%Y%m%d"), f"{hour:02d}00"


def fetch_kma_forecast(station: Station, settings: Settings, now: datetime | None = None) -> dict:
    if station.nx is None or station.ny is None:
        raise UpstreamError("기상청 격자 좌표가 없습니다")
    base_date, base_time = _kma_base_time(now or datetime.now(KST))
    params = {
        "serviceKey": settings.service_key,
        "dataType": "JSON",
        "numOfRows": 300,
        "pageNo": 1,
        "base_date": base_date,
        "base_time": base_time,
        "nx": station.nx,
        "ny": station.ny,
    }
    return _get_json(KMA_FORECAST_URL, params, settings)


def fetch_kma_nowcast(station: Station, settings: Settings, now: datetime | None = None) -> dict:
    if station.nx is None or station.ny is None:
        raise UpstreamError("기상청 격자 좌표가 없습니다")
    moment = (now or datetime.now(KST)) - timedelta(minutes=40)
    params = {
        "serviceKey": settings.service_key,
        "dataType": "JSON",
        "numOfRows": 20,
        "pageNo": 1,
        "base_date": moment.strftime("%Y%m%d"),
        "base_time": moment.strftime("%H00"),
        "nx": station.nx,
        "ny": station.ny,
    }
    return _get_json(KMA_NOWCAST_URL, params, settings)
