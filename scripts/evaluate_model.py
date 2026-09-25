"""검증 구간에서 PM2.5 학습 모델의 시간별 MAE를 평가한다."""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.forecast.baseline import Observation, persistence_forecast
from app.forecast.model import make_features, train_models
from scripts.evaluate_baseline import load_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("--hours", type=int, default=12)
    parser.add_argument("--split", type=float, default=0.8)
    args = parser.parse_args()

    rows = load_rows(args.csv)
    if not rows:
        raise SystemExit("CSV에 데이터가 없습니다.")

    # 학습 함수에 전달할 CSV 형태로 변환한다.
    training_rows = [
        {
            "time": row["time"].isoformat(),
            "pm25": row["pm25"],
            "upwind_pm25": row["upwind_pm25"],
            "wind_direction": row["wind_direction"],
            "wind_speed": row["wind_speed"],
        }
        for row in rows
    ]
    models = train_models(training_rows, hours=args.hours, split=args.split)

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
            rows[i]["wind_direction"],
            rows[i]["wind_speed"],
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
    print("주의: 데모 데이터 결과는 실제 서비스 성능이 아닙니다.")


if __name__ == "__main__":
    main()
