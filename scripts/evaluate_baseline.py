"""기준선 성능 평가.

시간 순서를 지키는 rolling-origin 방식으로 persistence와 wind_rule의 MAE를 비교한다.
미래 값을 입력으로 쓰지 않도록, 각 기준 시각까지 발행된 예보만 사용한다.
학습 모델(#3)도 같은 스크립트로 비교하면 조건이 같아진다.

사용법
    python scripts/evaluate_baseline.py data/history.csv --weather-forecast data/weather_forecast.csv
    python scripts/evaluate_baseline.py data/history.csv --hours 12 --split 0.8 --json out.json

CSV 형식 (헤더 필수, 1시간 간격, 시간 오름차순)
    time,pm25,upwind_pm25
    2026-09-01T00:00:00+09:00,18,22
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.forecast.baseline import (  # noqa: E402
    MODEL_NAME_PERSISTENCE,
    MODEL_NAME_WIND_RULE,
    Observation,
    WeatherPoint,
    persistence_forecast,
    wind_rule_forecast,
)


def _float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for raw in csv.DictReader(f):
            time_text = (raw.get("time") or "").strip()
            if not time_text:
                continue
            rows.append({
                "time": datetime.fromisoformat(time_text),
                "pm25": _float(raw.get("pm25")),
                "upwind_pm25": _float(raw.get("upwind_pm25")),
            })
    rows.sort(key=lambda r: r["time"])
    return rows


def load_forecasts(path: Path) -> list[dict]:
    """발행 시각이 보존된 기상 예보 CSV를 읽는다."""
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for raw in csv.DictReader(f):
            issued_at = (raw.get("issued_at") or "").strip()
            target_time = (raw.get("target_time") or "").strip()
            if not issued_at or not target_time:
                continue
            rows.append({
                "issued_at": datetime.fromisoformat(issued_at),
                "target_time": datetime.fromisoformat(target_time),
                "wind_direction": _float(raw.get("wind_direction")),
                "wind_speed": _float(raw.get("wind_speed")),
            })
    rows.sort(key=lambda r: (r["target_time"], r["issued_at"]))
    return rows


def _weather_available_at(forecasts: list[dict], origin: datetime,
                          hours: int) -> list[WeatherPoint]:
    """기준 시각에 이미 발행된 예보 중 목표 시각별 최신본만 고른다."""
    end = origin + timedelta(hours=hours)
    latest: dict[datetime, dict] = {}
    for row in forecasts:
        target = row["target_time"]
        issued = row["issued_at"]
        if issued > origin or not (origin < target <= end):
            continue
        previous = latest.get(target)
        if previous is None or previous["issued_at"] < issued:
            latest[target] = row
    return [
        WeatherPoint(target, row["wind_direction"], row["wind_speed"])
        for target, row in sorted(latest.items())
    ]


def evaluate(rows: list[dict], hours: int = 12, split: float = 0.8,
             forecasts: list[dict] | None = None) -> dict:
    """검증 구간의 각 시각을 기준으로 12시간 예측을 만들어 MAE를 계산한다."""
    if len(rows) < hours + 10:
        raise SystemExit("자료가 너무 적습니다. 최소 22시간 이상 필요합니다.")

    forecasts = forecasts or []
    actual = {r["time"]: r["pm25"] for r in rows if r["pm25"] is not None}
    start_index = max(1, int(len(rows) * split))
    errors = {MODEL_NAME_PERSISTENCE: [], MODEL_NAME_WIND_RULE: []}
    by_horizon: dict[int, dict[str, list[float]]] = {}
    origins = 0

    for i in range(start_index, len(rows) - 1):
        origin = rows[i]["time"]
        history = rows[: i + 1]                     # 기준 시각까지만 사용
        target_history = [Observation(r["time"], r["pm25"])
                          for r in history if r["pm25"] is not None]
        upwind_history = [Observation(r["time"], r["upwind_pm25"]) for r in history
                          if r["upwind_pm25"] is not None]
        weather = _weather_available_at(forecasts, origin, hours)
        if not target_history:
            continue
        origins += 1

        predictions = {
            MODEL_NAME_PERSISTENCE: persistence_forecast(target_history, hours, start=origin),
            MODEL_NAME_WIND_RULE: wind_rule_forecast(target_history, upwind_history, weather,
                                                     hours, start=origin),
        }
        for name, items in predictions.items():
            for step, item in enumerate(items, start=1):
                predicted = item["pm25_predicted"]
                truth = actual.get(datetime.fromisoformat(item["forecast_time"]))
                if predicted is None or truth is None:
                    continue
                error = abs(predicted - truth)
                errors[name].append(error)
                by_horizon.setdefault(step, {}).setdefault(name, []).append(error)

    def mae(values):
        return round(statistics.fmean(values), 2) if values else None

    return {
        "rows": len(rows),
        "validation_from": rows[start_index]["time"].isoformat(),
        "origins": origins,
        "hours": hours,
        "weather_forecasts": len(forecasts),
        "overall": {name: mae(values) for name, values in errors.items()},
        "by_horizon": {step: {name: mae(v) for name, v in per.items()}
                       for step, per in sorted(by_horizon.items())},
    }


def print_report(result: dict) -> None:
    print(f"자료 {result['rows']}행 · 검증 시작 {result['validation_from']} · 기준 시각 {result['origins']}개")
    if not result["weather_forecasts"]:
        print("주의: 보관된 기상 예보가 없어 wind_rule은 persistence로 평가됩니다.")
    print()
    print(f"{'예측 시간':>8} {'persistence':>12} {'wind_rule':>10}")
    for step, per in result["by_horizon"].items():
        p = per.get(MODEL_NAME_PERSISTENCE)
        w = per.get(MODEL_NAME_WIND_RULE)
        print(f"{step:>6}h {('-' if p is None else f'{p:.2f}'):>12} {('-' if w is None else f'{w:.2f}'):>10}")
    print()
    overall = result["overall"]
    print("전체 MAE (㎍/㎥)")
    for name, value in overall.items():
        print(f"  {name:<22} {'-' if value is None else value}")
    values = [v for v in overall.values() if v is not None]
    if len(values) == 2:
        if values[0] == values[1]:
            print("\n두 기준선의 MAE가 같습니다.")
        else:
            better = min(overall, key=lambda k: overall[k])
            print(f"\n더 나은 기준선: {better}")
    print("\n주의: 학습 모델(#3)은 같은 CSV·같은 분할로 비교해야 조건이 같습니다.")


def main() -> None:
    parser = argparse.ArgumentParser(description="기준선 예측 MAE 비교")
    parser.add_argument("csv", type=Path, help="시간별 관측 CSV")
    parser.add_argument("--hours", type=int, default=12)
    parser.add_argument("--split", type=float, default=0.8, help="학습/검증 경계 비율")
    parser.add_argument(
        "--weather-forecast", type=Path,
        help="발행 시각·목표 시각이 포함된 기상 예보 CSV",
    )
    parser.add_argument("--json", type=Path, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    rows = load_rows(args.csv)
    forecasts = load_forecasts(args.weather_forecast) if args.weather_forecast else []
    result = evaluate(rows, args.hours, args.split, forecasts)
    print_report(result)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 저장: {args.json}")


if __name__ == "__main__":
    main()
