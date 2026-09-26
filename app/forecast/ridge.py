"""순천 PM2.5 예측 AI 모델: 예측 시간(1~14시간 뒤)마다 따로 학습한 릿지 회귀.

- 입력: 기준 시각까지 관측된 값만 쓴다(순천·광양·여수 PM2.5, 순천 풍향·풍속·기온·습도, 시각).
  미래의 실제 관측은 입력에 넣지 않는다.
- 정답: `기준 시각 + h`의 순천 PM2.5와 현재값의 차이. 변화량을 배우고 현재값에 더한다.
- 외부 패키지 없이 학습·추론한다. 대회 서버 메모리(1.2GB)와 설치 부담을 줄이기 위해서다.

학습은 `scripts/train_ridge.py`가 하고, 결과(계수·평균·표준편차·검증 성능)는
`ridge_model.json`에 저장한다. 서버는 이 파일만 읽어 예측한다.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

MODEL_NAME = "ml-ridge"
MODEL_PATH = Path(__file__).with_name("ridge_model.json")

# 순천 시청 기준 산단 방위(baseline.SUNCHEON_FACILITIES와 같은 값)와 허용 각도
INDUSTRIAL_BEARINGS = (101.0, 117.0)
INDUSTRIAL_TOLERANCE_DEG = 25.0

FEATURES = (
    "pm_now", "pm_1h", "pm_3h", "pm_24h", "pm_mean6", "pm_mean24",
    "up_now", "gy_now", "ys_now", "gy_delta3",
    "wind_sin", "wind_cos", "wind_speed", "industrial_wind",
    "hour_sin", "hour_cos", "temperature", "humidity",
)

# series 키: pm=순천 PM2.5, gy=광양, ys=여수, wd=풍향, ws=풍속, tp=기온, hu=습도
Series = dict[str, dict[datetime, float]]


def hour_of(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _last(series: Series, key: str, at: datetime, back: int = 3) -> float | None:
    """at 시각 값, 없으면 최대 back시간 전까지의 가장 최근 값."""
    values = series.get(key, {})
    for k in range(back + 1):
        value = values.get(at - timedelta(hours=k))
        if value is not None and math.isfinite(value):
            return value
    return None


def _mean(series: Series, key: str, at: datetime, hours: int) -> float | None:
    values = series.get(key, {})
    picked = [values[t] for t in (at - timedelta(hours=k) for k in range(hours))
              if values.get(t) is not None and math.isfinite(values[t])]
    return sum(picked) / len(picked) if picked else None


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def is_industrial_wind(direction: float | None) -> bool:
    """풍향(불어오는 방향)이 산단 방위 ±25도 안인가."""
    if direction is None:
        return False
    return min(_angle_diff(direction, b) for b in INDUSTRIAL_BEARINGS) <= INDUSTRIAL_TOLERANCE_DEG


def make_features(series: Series, at: datetime) -> list[float | None] | None:
    """기준 시각 at까지의 관측으로 입력 한 줄을 만든다. 핵심 PM2.5가 없으면 None."""
    at = hour_of(at)
    pm_now = series.get("pm", {}).get(at)
    pm_1h = _last(series, "pm", at - timedelta(hours=1))
    pm_3h = _last(series, "pm", at - timedelta(hours=3))
    if pm_now is None or pm_1h is None or pm_3h is None:
        return None

    gy_now, ys_now = _last(series, "gy", at), _last(series, "ys", at)
    ups = [v for v in (gy_now, ys_now) if v is not None]
    gy_3h = _last(series, "gy", at - timedelta(hours=3))
    direction = _last(series, "wd", at, back=1)
    speed = _last(series, "ws", at, back=1)
    radians = math.radians(direction) if direction is not None else None
    hour_angle = 2 * math.pi * at.hour / 24
    return [
        pm_now, pm_1h, pm_3h,
        _last(series, "pm", at - timedelta(hours=24)),
        _mean(series, "pm", at, 6),
        _mean(series, "pm", at, 24),
        sum(ups) / len(ups) if ups else None,
        gy_now, ys_now,
        gy_now - gy_3h if gy_now is not None and gy_3h is not None else None,
        math.sin(radians) if radians is not None else None,
        math.cos(radians) if radians is not None else None,
        speed,
        (speed or 0.0) if is_industrial_wind(direction) else 0.0,
        math.sin(hour_angle), math.cos(hour_angle),
        _last(series, "tp", at, back=1),
        _last(series, "hu", at, back=1),
    ]


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """가우스 소거(부분 피벗)로 a·x = b를 푼다. 특징 18개라 표준 라이브러리로 충분하다."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        if abs(m[col][col]) < 1e-12:
            raise ValueError("정규 방정식이 특이 행렬입니다")
        for r in range(col + 1, n):
            factor = m[r][col] / m[col][col]
            if factor:
                for c in range(col, n + 1):
                    m[r][c] -= factor * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def fit_scaler(rows: list[list[float | None]]) -> tuple[list[float], list[float]]:
    """특징별 평균·표준편차(결측 제외). 결측은 추론 때 평균으로 채운다."""
    means, stds = [], []
    for j in range(len(rows[0])):
        values = [r[j] for r in rows if r[j] is not None]
        mean = sum(values) / len(values) if values else 0.0
        var = sum((v - mean) ** 2 for v in values) / len(values) if values else 0.0
        means.append(mean)
        stds.append(math.sqrt(var) or 1.0)
    return means, stds


def scale(row: list[float | None], means: list[float], stds: list[float]) -> list[float]:
    return [0.0 if v is None else (v - m) / s for v, m, s in zip(row, means, stds)]


def fit_ridge(x: list[list[float]], y: list[float], alpha: float) -> tuple[float, list[float]]:
    """표준화된 x로 릿지 회귀를 푼다. (절편, 계수)"""
    n, d = len(x), len(x[0])
    y_mean = sum(y) / n
    xtx = [[0.0] * d for _ in range(d)]
    xty = [0.0] * d
    for row, target in zip(x, y):
        centered = target - y_mean
        for i in range(d):
            xi = row[i]
            if xi == 0.0:
                continue
            xty[i] += xi * centered
            for j in range(i, d):
                xtx[i][j] += xi * row[j]
    for i in range(d):
        for j in range(i):
            xtx[i][j] = xtx[j][i]
        xtx[i][i] += alpha
    return y_mean, _solve(xtx, xty)


def load_model(path: Path | str = MODEL_PATH) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def predict(model: dict, series: Series, origin: datetime) -> list[tuple[datetime, float]] | None:
    """origin 기준 1~H시간 뒤 PM2.5. 입력이 모자라면 None(호출 쪽에서 기준선으로 폴백)."""
    row = make_features(series, origin)
    if row is None:
        return None
    x = scale(row, model["means"], model["stds"])
    origin = hour_of(origin)
    result = []
    for item in model["horizons"]:
        delta = item["intercept"] + sum(w * v for w, v in zip(item["coef"], x))
        value = max(0.0, row[0] + delta)
        if not math.isfinite(value):
            return None
        result.append((origin + timedelta(hours=item["h"]), round(value, 1)))
    return result
