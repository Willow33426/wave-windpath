"""PM2.5 학습 모델의 입력, 학습, 저장 및 12시간 예측."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from pathlib import Path

import joblib
from sklearn.ensemble import HistGradientBoostingRegressor

from app.forecast import baseline
from app.forecast.baseline import Observation

logger = logging.getLogger(__name__)
MODEL_NAME = "ml-hist-gradient-boosting"

FEATURE_NAMES = (
    "pm25_now",
    "pm25_1h_ago",
    "pm25_3h_ago",
    "upwind_pm25",
    "wind_direction_sin",
    "wind_direction_cos",
    "wind_speed",
    "hour_sin",
    "hour_cos",
)


def make_features(
    target_history: list[Observation],
    upwind_history: list[Observation],
    wind_direction: float | None,
    wind_speed: float | None,
    at: datetime,
) -> list[float] | None:
    """기준 시각까지의 관측으로 모델 입력 한 행을 만든다."""
    target = {
        obs.time: obs.pm25
        for obs in target_history
        if obs.time <= at and obs.pm25 is not None
    }
    upwind = [
        obs for obs in upwind_history
        if obs.time <= at and obs.pm25 is not None
    ]

    now = target.get(at)
    one_hour_ago = target.get(at - timedelta(hours=1))
    three_hours_ago = target.get(at - timedelta(hours=3))
    if now is None or one_hour_ago is None or three_hours_ago is None:
        return None

    latest_upwind = max(upwind, key=lambda obs: obs.time).pm25 if upwind else None
    direction = math.radians(wind_direction) if wind_direction is not None else None
    hour_angle = 2 * math.pi * at.hour / 24

    return [
        float(now),
        float(one_hour_ago),
        float(three_hours_ago),
        float(latest_upwind) if latest_upwind is not None else math.nan,
        math.sin(direction) if direction is not None else math.nan,
        math.cos(direction) if direction is not None else math.nan,
        float(wind_speed) if wind_speed is not None else math.nan,
        math.sin(hour_angle),
        math.cos(hour_angle),
    ]


def train_models(
    rows: list[dict],
    hours: int = 12,
    split: float = 0.8,
) -> dict[int, HistGradientBoostingRegressor]:
    """시간순 CSV 행으로 예측 시간별 모델을 학습한다.

    분할 경계 이전의 입력과 정답만 학습에 사용한다.
    """
    if not 0 < split < 1:
        raise ValueError("split은 0과 1 사이여야 합니다.")

    rows = sorted(rows, key=lambda row: row["time"])
    cutoff = int(len(rows) * split)
    times = [datetime.fromisoformat(row["time"]) for row in rows]

    target_history = [
        Observation(time=t, pm25=float(row["pm25"]))
        for t, row in zip(times, rows)
    ]
    upwind_history = [
        Observation(time=t, pm25=float(row["upwind_pm25"]))
        for t, row in zip(times, rows)
    ]

    models: dict[int, HistGradientBoostingRegressor] = {}
    for horizon in range(1, hours + 1):
        x_train: list[list[float]] = []
        y_train: list[float] = []

        for i in range(3, cutoff - horizon):
            row = rows[i]
            features = make_features(
                target_history[: i + 1],
                upwind_history[: i + 1],
                float(row["wind_direction"]),
                float(row["wind_speed"]),
                times[i],
            )
            if features is None:
                continue

            if times[i + horizon] != times[i] + timedelta(hours=horizon):
                continue

            x_train.append(features)
            y_train.append(float(rows[i + horizon]["pm25"]))

        if not x_train:
            raise ValueError(f"{horizon}시간 예측에 사용할 학습 자료가 없습니다.")

        model = HistGradientBoostingRegressor(
            max_iter=100,
            min_samples_leaf=5,
            random_state=42,
        )
        model.fit(x_train, y_train)
        models[horizon] = model

    return models


def save_models(
    models: dict[int, HistGradientBoostingRegressor],
    path: str | Path,
) -> None:
    """학습된 시간별 모델을 파일로 저장한다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(models, path)


def load_models(path: str | Path) -> dict[int, HistGradientBoostingRegressor]:
    """프로젝트에서 직접 생성한 모델 파일을 불러온다."""
    return joblib.load(path)


def predict(
    features: dict,
    hours: int = 12,
    model_path: str | Path = "data/model.joblib",
) -> list[dict]:
    """학습 모델로 예측하고, 사용할 수 없으면 기준선으로 대체한다."""
    fallback = baseline.predict(features, hours)
    history = features.get("target_history") or []
    if not history:
        return fallback

    path = Path(model_path)
    if not path.is_file():
        return fallback

    latest = max(history, key=lambda obs: obs.time)
    input_row = make_features(
        history,
        features.get("upwind_history") or [],
        latest.wind_direction,
        latest.wind_speed,
        latest.time,
    )
    if input_row is None:
        return fallback

    try:
        models = load_models(path)
        if any(h not in models for h in range(1, hours + 1)):
            return fallback

        result = []
        for horizon in range(1, hours + 1):
            value = max(0.0, float(models[horizon].predict([input_row])[0]))
            item = dict(fallback[horizon - 1])
            item["pm25_predicted"] = round(value, 1)
            item["air_quality"] = baseline._grade(value)
            item["confidence"] = None
            item["industrial_influence"] = {
                "level": "unknown",
                "score": None,
                "upwind_facilities": [],
            }
            item["model"] = MODEL_NAME
            result.append(item)
        return result
    except (OSError, ValueError, KeyError, IndexError):
        logger.exception("ML 모델 예측 실패: 기준선으로 대체")
        return fallback