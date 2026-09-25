"""SQLite 관측·예보를 #3 예측 함수의 입력으로 변환한다."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from app.forecast.baseline import Observation, WeatherPoint
from app.sources.parse import KST


def _values(conn: sqlite3.Connection, station: str, metric: str,
            kind: str, start: datetime, end: datetime,
            available_at: datetime, include_fixture: bool = False) -> dict[datetime, float]:
    """조회 시점까지 수집된 시간별 값만 가져온다."""
    has_origin = any(row["name"] == "data_origin"
                     for row in conn.execute("PRAGMA table_info(measurements)"))
    fixture_clause = "" if include_fixture or not has_origin else "AND data_origin != 'fixture' "
    rows = conn.execute(
        "SELECT target_time, value FROM measurements "
        "WHERE station=? AND metric=? AND kind=? "
        "AND target_time>=? AND target_time<=? "
        "AND base_time<=? AND collected_at<=? " + fixture_clause +
        "ORDER BY target_time",
        (station, metric, kind, start.isoformat(), end.isoformat(),
         available_at.isoformat(), available_at.isoformat()),
    ).fetchall()
    return {datetime.fromisoformat(row["target_time"]): float(row["value"])
            for row in rows}


def build_features(conn: sqlite3.Connection, hours: int = 12,
                   now: datetime | None = None,
                   history_hours: int = 72,
                   include_fixture: bool = False) -> dict:
    """순천 실측과 광양·여수 상류 실측, 순천 기상 예보를 묶는다.

    학습·평가 CSV와 같이 광양 값을 우선 사용하고, 없으면 여수 값을 쓴다. 예보는 현재까지
    수집된 값만 사용한다. 오래된 예보를 재현하는 백테스트에는 별도 예보
    이력 저장이 필요하다(현재 DB는 같은 target_time을 덮어쓴다).
    """
    if hours < 1 or history_hours < 4:
        raise ValueError("hours는 1 이상, history_hours는 4 이상이어야 합니다.")
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        raise ValueError("now에는 시간대가 필요합니다.")

    start = now - timedelta(hours=history_hours)
    pm25 = _values(conn, "suncheon", "pm25", "observation", start, now, now,
                   include_fixture)
    direction = _values(conn, "suncheon", "wind_direction", "observation",
                        start, now, now, include_fixture)
    speed = _values(conn, "suncheon", "wind_speed", "observation", start, now, now,
                    include_fixture)
    target_history = [Observation(time, value, direction.get(time), speed.get(time))
                      for time, value in sorted(pm25.items())]

    gwangyang = _values(conn, "gwangyang", "pm25", "observation", start, now, now,
                        include_fixture)
    yeosu = _values(conn, "yeosu", "pm25", "observation", start, now, now,
                    include_fixture)
    upwind = {**yeosu, **gwangyang}
    upwind_history = [Observation(time, value)
                      for time, value in sorted(upwind.items())]

    weather = []
    if target_history:
        origin = target_history[-1].time
        end = origin + timedelta(hours=hours)
        future_direction = _values(conn, "suncheon", "wind_direction", "forecast",
                                   origin + timedelta(microseconds=1), end, now,
                                   include_fixture)
        future_speed = _values(conn, "suncheon", "wind_speed", "forecast",
                               origin + timedelta(microseconds=1), end, now,
                               include_fixture)
        weather = [WeatherPoint(time, future_direction.get(time), future_speed.get(time))
                   for time in sorted(future_direction.keys() | future_speed.keys())]

    return {"target_history": target_history,
            "upwind_history": upwind_history,
            "weather": weather}
