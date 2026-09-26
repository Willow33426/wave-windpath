"""SQLite 저장소. 같은 관측값을 여러 번 수집해도 행이 늘지 않는다."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from app.sources.parse import Record

SCHEMA = """
CREATE TABLE IF NOT EXISTS measurements (
    source      TEXT NOT NULL,
    station     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    base_time   TEXT NOT NULL,
    target_time TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL NOT NULL,
    unit        TEXT NOT NULL,
    data_origin TEXT NOT NULL DEFAULT 'live',
    collected_at TEXT NOT NULL,
    PRIMARY KEY (source, station, kind, target_time, metric)
);
CREATE INDEX IF NOT EXISTS idx_measurements_lookup
    ON measurements (station, metric, kind, target_time DESC);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(measurements)")}
    if "data_origin" not in columns:
        with conn:
            conn.execute(
                "ALTER TABLE measurements ADD COLUMN data_origin TEXT NOT NULL DEFAULT 'live'"
            )
    return conn


def upsert_records(conn: sqlite3.Connection, records: Iterable[Record], collected_at: datetime) -> int:
    rows = [
        (r.source, r.station, r.kind, r.base_time.isoformat(), r.target_time.isoformat(),
         r.metric, float(r.value), r.unit, r.data_origin, collected_at.isoformat())
        for r in records
    ]
    if not rows:
        return 0
    with conn:
        conn.executemany(
            "INSERT INTO measurements "
            "(source, station, kind, base_time, target_time, metric, value, unit, data_origin, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source, station, kind, target_time, metric) DO UPDATE SET "
            "value=excluded.value, base_time=excluded.base_time, "
            "data_origin=excluded.data_origin, collected_at=excluded.collected_at",
            rows,
        )
    return len(rows)


def latest_observation(conn: sqlite3.Connection, station: str, metric: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM measurements WHERE station=? AND metric=? AND kind='observation' "
        "ORDER BY target_time DESC LIMIT 1",
        (station, metric),
    ).fetchone()


def query_measurements(
    conn: sqlite3.Connection,
    station: str | None = None,
    metric: str | None = None,
    kind: str | None = None,
    since: datetime | None = None,
    limit: int = 500,
    include_fixture: bool = True,
) -> list[sqlite3.Row]:
    sql = ["SELECT * FROM measurements WHERE 1=1"]
    args: list = []
    for column, value in (("station", station), ("metric", metric), ("kind", kind)):
        if value:
            sql.append(f"AND {column}=?")
            args.append(value)
    if since is not None:
        sql.append("AND target_time >= ?")
        args.append(since.isoformat())
    if not include_fixture:
        sql.append("AND data_origin != 'fixture'")
    sql.append("ORDER BY target_time DESC LIMIT ?")
    args.append(int(limit))
    return list(conn.execute(" ".join(sql), args))


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def rows_to_dicts(rows: Sequence[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]
