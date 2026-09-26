"""모델 비교 실험(선택 실행). docs/model-ai.md의 '모델을 고른 과정' 표를 만든다.

    pip install scikit-learn numpy
    python scripts/compare_models.py

서비스는 이 스크립트나 scikit-learn을 쓰지 않는다. 배포 모델은 scripts/train_ridge.py가 만든다.
검증 방식은 train_ridge.py와 같다: 시간순으로 자른 뒤 뒷부분(30%, 20%)만 채점.
"""
from __future__ import annotations

import math
import statistics
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.forecast import ridge  # noqa: E402
from scripts.train_ridge import DATA, SPLITS, load  # noqa: E402

HORIZONS = range(1, 13)
BASE = (0, 1, 2, 6, 10, 11, 12, 14, 15)  # 문호영 모델의 특징 9개: PM2.5 현재·1·3시간 전, 상류, 풍향 sin·cos, 풍속, 시각 sin·cos


def dataset(times, series, columns, delta):
    rows = {t: ridge.make_features(series, t) for t in times}
    out = []
    for t in times:
        row = rows[t]
        if row is None:
            continue
        for h in HORIZONS:
            target = series["pm"].get(t + timedelta(hours=h))
            if target is not None:
                x = [math.nan if row[i] is None else row[i] for i in columns]
                out.append((t, h, x, target - row[0] if delta else target, row[0], target))
    return out


def run(times, series, split, columns, delta, **params):
    data = dataset(times, series, columns, delta)
    cut = times[int(len(times) * split)]
    errors, base = [], []
    for h in HORIZONS:
        train = [s for s in data if s[1] == h and s[0] + timedelta(hours=h) < cut]
        test = [s for s in data if s[1] == h and s[0] >= cut]
        model = HistGradientBoostingRegressor(random_state=42, **params)
        model.fit(np.array([s[2] for s in train]), np.array([s[3] for s in train]))
        pred = model.predict(np.array([s[2] for s in test]))
        for s, p in zip(test, pred):
            value = max(0.0, p + (s[4] if delta else 0.0))
            errors.append(abs(value - s[5]))
            base.append(abs(s[4] - s[5]))
    return statistics.fmean(errors), statistics.fmean(base)


def main() -> None:
    times, series = load(DATA)
    everything = tuple(range(len(ridge.FEATURES)))
    configs = {
        "부스팅, 기본 특징 9개(문호영 설정)": (BASE, False, {"max_iter": 100, "min_samples_leaf": 5}),
        "부스팅, 확장 특징·규제": (everything, False, {"max_iter": 200, "learning_rate": 0.04, "max_depth": 3,
                                              "min_samples_leaf": 40, "l2_regularization": 1.0}),
    }
    for split in SPLITS:
        print(f"뒤 {round((1 - split) * 100)}% 검증")
        for name, (columns, delta, params) in configs.items():
            mae, base = run(times, series, split, columns, delta, **params)
            print(f"  {name}: {mae:.2f} (유지 {base:.2f}, {100 * (1 - mae / base):+.1f}%)")
    print("릿지(채택)는 python scripts/train_ridge.py --dry-run")


if __name__ == "__main__":
    main()
