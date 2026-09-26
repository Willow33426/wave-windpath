"""검증 구간에서 PM2.5 학습 모델의 시간별 MAE를 평가한다."""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.forecast.baseline import Observation, persistence_forecast
from app.forecast.model import make_features, train_models
from scripts.evaluate_baseline import _float, load_rows


def load_observed_weather(path: Path) -> dict:
    """동일 시각의 관측·재분석 기상을 읽는다. 미래 예보로 취급하지 않는다."""
    weather = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            time = (row.get("time") or "").strip()
            if time:
                weather[time] = (_float(row.get("wind_direction")),
                                 _float(row.get("wind_speed")))
    return weather


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("--hours", type=int, default=12)
    parser.add_argument("--split", type=float, default=0.8)
    parser.add_argument(
        "--observed-weather", type=Path,
        help="같은 시각의 관측·재분석 기상 CSV (탐색적 비교 전용; 미래 예보가 아님)",
    )
    args = parser.parse_args()

    rows = load_rows(args.csv)
    if not rows:
        raise SystemExit("CSV에 데이터가 없습니다.")
    observed_weather = (load_observed_weather(args.observed_weather)
                        if args.observed_weather else {})

    # 학습 함수에 전달할 CSV 형태로 변환한다.
    training_rows = [
        {
            "time": row["time"].isoformat(),
            "pm25": row["pm25"],
            "upwind_pm25": row["upwind_pm25"],
            "wind_direction": observed_weather.get(row["time"].isoformat(), (None, None))[0],
            "wind_speed": observed_weather.get(row["time"].isoformat(), (None, None))[1],
        }
        for row in rows
    ]
    try:
        models = train_models(training_rows, hours=args.hours, split=args.split)
    except ValueError as exc:
        raise SystemExit(f"{args.hours}시간 실측 평가를 진행할 수 없습니다: {exc}") from None

    actual = {
        row["time"]: row["pm25"]
        for row in rows
        if row["pm25"] is not None
    }
    start_index = max(1, int(len(rows) * args.split))
    errors = {h: [] for h in range(1, args.hours + 1)}
    persistence_errors = {h: [] for h in range(1, args.hours + 1)}
    origins = 0

    for i in range(start_index, len(rows) - 1):
        origin = rows[i]["time"]
        history = rows[: i + 1]

        target_history = [
            Observation(row["time"], row["pm25"])
            for row in history
            if row["pm25"] is not None
        ]
        upwind_history = [
            Observation(row["time"], row["upwind_pm25"])
            for row in history
            if row["upwind_pm25"] is not None
        ]
        if not target_history:
            continue
        features = make_features(
            target_history,
            upwind_history,
            training_rows[i]["wind_direction"],
            training_rows[i]["wind_speed"],
            origin,
        )
        if features is None:
            continue
        origins += 1
        persistence = persistence_forecast(target_history, args.hours, start=origin)

        for horizon, model in models.items():
            truth = actual.get(origin + timedelta(hours=horizon))
            if truth is None:
                continue
            predicted = float(model.predict([features])[0])
            errors[horizon].append(abs(predicted - truth))
            persistence_errors[horizon].append(
                abs(persistence[horizon - 1]["pm25_predicted"] - truth)
            )

    all_errors = [error for values in errors.values() for error in values]

    print(
        f"자료 {len(rows)}행 · 검증 시작 "
        f"{rows[start_index]['time'].isoformat()} · 기준 시각 {origins}개"
    )
    print()
    print(" 예측 시간       ML MAE  persistence MAE    채점 건수")
    for horizon, values in errors.items():
        score = f"{statistics.fmean(values):.2f}" if values else "-"
        base_values = persistence_errors[horizon]
        base_score = f"{statistics.fmean(base_values):.2f}" if base_values else "-"
        print(f"{horizon:>6}h {score:>12} {base_score:>16} {len(values):>10}")
    print()
    if all_errors:
        print(f"전체 ML MAE: {statistics.fmean(all_errors):.2f} ㎍/㎥")
        base_errors = [error for values in persistence_errors.values() for error in values]
        print(f"동일 표본 persistence MAE: {statistics.fmean(base_errors):.2f} ㎍/㎥")
    else:
        print("채점 가능한 예측이 없습니다.")
    if args.observed_weather:
        print("주의: 기상 관측·재분석 값을 쓴 탐색적 결과입니다. 실시간 이용 가능성은 검증되지 않았습니다.")
    else:
        print("기상 입력 없이 평가했습니다. 과거 발행 예보가 없어 wind_rule과의 운영 성능 비교는 보류합니다.")


if __name__ == "__main__":
    main()
