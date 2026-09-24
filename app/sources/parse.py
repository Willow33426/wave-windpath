"""공공 API 응답을 공통 스키마로 정규화한다.

외부 라이브러리를 쓰지 않는다. 그래야 패키지 설치 없이 테스트할 수 있다.
공통 레코드: source, station, kind, base_time, target_time, metric, value, unit
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# 기상청 단기예보 카테고리 → 공통 지표 이름
KMA_FORECAST_METRICS = {
    "VEC": ("wind_direction", "deg"),
    "WSD": ("wind_speed", "m/s"),
    "TMP": ("temperature", "C"),
    "REH": ("humidity", "%"),
    "POP": ("precipitation_prob", "%"),
}
# 기상청 초단기실황 카테고리
KMA_OBSERVED_METRICS = {
    "VEC": ("wind_direction", "deg"),
    "WSD": ("wind_speed", "m/s"),
    "T1H": ("temperature", "C"),
    "REH": ("humidity", "%"),
}
AIRKOREA_METRICS = {
    "pm10Value": ("pm10", "ug/m3"),
    "pm25Value": ("pm25", "ug/m3"),
    "so2Value": ("so2", "ppm"),
    "no2Value": ("no2", "ppm"),
    "o3Value": ("o3", "ppm"),
}


class UpstreamError(RuntimeError):
    """외부 API가 정상 코드를 주지 않았을 때."""


@dataclass(frozen=True)
class Record:
    source: str          # "kma" | "airkorea"
    station: str         # Station.key
    kind: str            # "observation" | "forecast"
    base_time: datetime  # 관측 시각 또는 발표 시각
    target_time: datetime
    metric: str
    value: float
    unit: str

    def as_dict(self) -> dict:
        d = asdict(self)
        d["base_time"] = self.base_time.isoformat()
        d["target_time"] = self.target_time.isoformat()
        return d


def _to_float(raw) -> float | None:
    """'-', '', None, 통신 이상값을 걸러낸다."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text in ("", "-", "통신장애", "점검중"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _check_header(payload: dict) -> dict:
    """공공데이터포털 공통 헤더를 확인하고 body를 돌려준다."""
    if not isinstance(payload, dict):
        raise UpstreamError("응답 형식이 올바르지 않습니다")
    response = payload.get("response")
    if not isinstance(response, dict):
        raise UpstreamError("response 필드가 없습니다")
    header = response.get("header") or {}
    code = str(header.get("resultCode", "")).strip()
    if code not in ("00", "0"):
        # 메시지에 키가 들어갈 수 있으므로 코드만 남긴다
        raise UpstreamError(f"공공 API 오류 코드 {code or 'unknown'}")
    body = response.get("body") or {}
    if not isinstance(body, dict):
        raise UpstreamError("body 필드가 없습니다")
    return body


def _kma_items(body: dict) -> list[dict]:
    items = body.get("items")
    if isinstance(items, dict):
        items = items.get("item", [])
    return [i for i in (items or []) if isinstance(i, dict)]


def parse_airkorea(payload: dict, station: str, collected_at: datetime | None = None) -> list[Record]:
    """에어코리아 측정소별 실시간 측정정보 → Record 목록."""
    body = _check_header(payload)
    records: list[Record] = []
    for item in _kma_items(body):
        stamp = item.get("dataTime")
        observed = parse_airkorea_time(stamp)
        if observed is None:
            continue
        for field_name, (metric, unit) in AIRKOREA_METRICS.items():
            value = _to_float(item.get(field_name))
            if value is None:
                continue
            records.append(Record("airkorea", station, "observation", observed, observed, metric, value, unit))
    return records


def parse_airkorea_time(stamp: str | None) -> datetime | None:
    """'2026-09-24 15:00' 또는 '2026-09-24 24:00'(자정)을 KST datetime으로."""
    if not stamp:
        return None
    text = str(stamp).strip()
    try:
        date_part, time_part = text.split(" ")
        hour, minute = (int(x) for x in time_part.split(":")[:2])
    except ValueError:
        return None
    try:
        base = datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=KST)
    except ValueError:
        return None
    if hour == 24:  # 에어코리아는 자정을 24:00으로 표기한다
        return base + timedelta(days=1, minutes=minute)
    return base + timedelta(hours=hour, minutes=minute)


def _kma_time(date_text: str | None, time_text: str | None) -> datetime | None:
    """'20260924' + '1600' → KST datetime."""
    if not date_text or time_text is None:
        return None
    text = f"{str(date_text).strip()}{str(time_text).strip().zfill(4)}"
    try:
        return datetime.strptime(text, "%Y%m%d%H%M").replace(tzinfo=KST)
    except ValueError:
        return None


def parse_kma_forecast(payload: dict, station: str) -> list[Record]:
    """기상청 단기예보(getVilageFcst) → Record 목록."""
    body = _check_header(payload)
    records: list[Record] = []
    for item in _kma_items(body):
        mapping = KMA_FORECAST_METRICS.get(str(item.get("category", "")).strip())
        if not mapping:
            continue
        metric, unit = mapping
        base = _kma_time(item.get("baseDate"), item.get("baseTime"))
        target = _kma_time(item.get("fcstDate"), item.get("fcstTime"))
        value = _to_float(item.get("fcstValue"))
        if base is None or target is None or value is None:
            continue
        records.append(Record("kma", station, "forecast", base, target, metric, value, unit))
    return records


def parse_kma_observation(payload: dict, station: str) -> list[Record]:
    """기상청 초단기실황(getUltraSrtNcst) → Record 목록."""
    body = _check_header(payload)
    records: list[Record] = []
    for item in _kma_items(body):
        mapping = KMA_OBSERVED_METRICS.get(str(item.get("category", "")).strip())
        if not mapping:
            continue
        metric, unit = mapping
        observed = _kma_time(item.get("baseDate"), item.get("baseTime"))
        value = _to_float(item.get("obsrValue"))
        if observed is None or value is None:
            continue
        records.append(Record("kma", station, "observation", observed, observed, metric, value, unit))
    return records


def direction_label(degree: float | None) -> str | None:
    """풍향(도) → 16방위 한글. 바람이 불어오는 방향 기준."""
    if degree is None:
        return None
    names = ["북", "북북동", "북동", "동북동", "동", "동남동", "남동", "남남동",
             "남", "남남서", "남서", "서남서", "서", "서북서", "북서", "북북서"]
    return names[int((degree % 360) / 22.5 + 0.5) % 16]


def air_quality_grade(pm25: float | None) -> str | None:
    """PM2.5 농도 → 4단계 등급 (에어코리아 기준)."""
    if pm25 is None:
        return None
    if pm25 <= 15:
        return "좋음"
    if pm25 <= 35:
        return "보통"
    if pm25 <= 75:
        return "나쁨"
    return "매우나쁨"
