"""순천 PM2.5 릿지 모델 학습·검증.

    python scripts/train_ridge.py            # 검증 결과 출력 + app/forecast/ridge_model.json 갱신
    python scripts/train_ridge.py --dry-run  # 파일은 그대로 두고 검증만

- 자료: app/forecast/training/suncheon_hourly_90d.csv (에어코리아 PM2.5 + Open-Meteo 과거 기상, 시간별)
- 검증: 시간순으로 자른 뒤 앞부분으로 학습하고 뒷부분(30%, 20% 두 가지)으로 채점한다.
  비교 대상은 '마지막 관측값 유지'(persistence). 규제 강도(alpha)는 학습 구간 안의 마지막 20%로만 고른다.
- 배포 모델: 같은 방식으로 고른 alpha로 전체 자료를 다시 학습한다.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.forecast import ridge  # noqa: E402

DATA = ROOT / "app" / "forecast" / "training" / "suncheon_hourly_90d.csv"
HORIZONS = range(1, 15)          # 관측이 1~2시간 늦게 들어와도 앞으로 12시간을 채우도록 14시간까지
ALPHAS = (1.0, 3.0, 10.0, 30.0, 100.0, 300.0)
SPLITS = (0.7, 0.8)
COLUMNS = {"pm": "suncheon_pm25", "gy": "gwangyang_pm25", "ys": "yeosu_pm25",
           "wd": "wind_direction", "ws": "wind_speed", "tp": "temperature", "hu": "humidity"}
SECTORS = ("북", "북동", "동", "남동", "남", "남서", "서", "북서")


def _float(text: str | None) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def load(path: Path) -> tuple[list[datetime], ridge.Series]:
    series: ridge.Series = {key: {} for key in COLUMNS}
    times = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for raw in csv.DictReader(f):
            t = datetime.fromisoformat(raw["time"])
            times.append(t)
            for key, column in COLUMNS.items():
                value = _float(raw.get(column))
                if value is not None:
                    series[key][t] = value
    return sorted(times), series


def samples(times, series, feature_rows):
    """(기준 시각, 예측 시간, 입력, 정답 변화량, 현재값)"""
    pm = series["pm"]
    out = []
    for t in times:
        row = feature_rows.get(t)
        if row is None:
            continue
        for h in HORIZONS:
            target = pm.get(t + timedelta(hours=h))
            if target is not None:
                out.append((t, h, row, target - row[0], row[0]))
    return out


def fit_all(train, alpha):
    means, stds = ridge.fit_scaler([s[2] for s in train])
    horizons = []
    for h in HORIZONS:
        part = [s for s in train if s[1] == h]
        x = [ridge.scale(s[2], means, stds) for s in part]
        intercept, coef = ridge.fit_ridge(x, [s[3] for s in part], alpha)
        horizons.append({"h": h, "intercept": intercept, "coef": coef})
    return {"means": means, "stds": stds, "alpha": alpha, "horizons": horizons}


def score(model, test, max_h=12):
    """예측 시간별 (모델 MAE, 유지 MAE, 건수). 화면에 쓰는 12시간까지만 채점한다."""
    by_h = {}
    for t, h, row, delta, now in test:
        if h > max_h:
            continue
        item = model["horizons"][h - 1]
        x = ridge.scale(row, model["means"], model["stds"])
        pred = max(0.0, now + item["intercept"] + sum(w * v for w, v in zip(item["coef"], x)))
        truth = now + delta
        errs = by_h.setdefault(h, ([], []))
        errs[0].append(abs(pred - truth))
        errs[1].append(abs(now - truth))
    return by_h


def overall(by_h):
    model = [e for m, _ in by_h.values() for e in m]
    base = [e for _, p in by_h.values() for e in p]
    return statistics.fmean(model), statistics.fmean(base), len(model)


def choose_alpha(train):
    """학습 구간 안에서만 alpha를 고른다(검증 구간을 보지 않는다)."""
    cut = train[int(len(train) * 0.8)][0]
    inner = [s for s in train if s[0] + timedelta(hours=s[1]) < cut]
    valid = [s for s in train if s[0] >= cut]
    return min(ALPHAS, key=lambda a: overall(score(fit_all(inner, a), valid))[0])


def evaluate(all_samples, times, split):
    cut = times[int(len(times) * split)]
    train = [s for s in all_samples if s[0] + timedelta(hours=s[1]) < cut]
    test = [s for s in all_samples if s[0] >= cut]
    alpha = choose_alpha(train)
    by_h = score(fit_all(train, alpha), test)
    mae_model, mae_base, n = overall(by_h)
    return {
        "split": split,
        "validation_from": cut.isoformat(),
        "validation_to": times[-1].isoformat(),
        "alpha": alpha,
        "scored": n,
        "mae_model": round(mae_model, 2),
        "mae_persistence": round(mae_base, 2),
        "improvement_pct": round(100 * (1 - mae_model / mae_base), 1),
        "by_horizon": [
            {"h": h, "model": round(statistics.fmean(m), 2), "persistence": round(statistics.fmean(p), 2)}
            for h, (m, p) in sorted(by_h.items())
        ],
    }


def evidence(times, series):
    """풍향별 순천 PM2.5 평균(풍속 1m/s 이상). 상관 관계일 뿐 원인을 뜻하지 않는다."""
    by_sector = {name: [] for name in SECTORS}
    industrial, other = [], []
    for t in times:
        pm, wd, ws = series["pm"].get(t), series["wd"].get(t), series["ws"].get(t)
        if pm is None or wd is None or ws is None or ws < 1.0:
            continue
        by_sector[SECTORS[int(((wd + 22.5) % 360) // 45)]].append(pm)
        (industrial if ridge.is_industrial_wind(wd) else other).append(pm)
    mean = lambda v: round(statistics.fmean(v), 1) if v else None  # noqa: E731
    return {
        "period_from": times[0].date().isoformat(),
        "period_to": times[-1].date().isoformat(),
        "min_wind_speed_mps": 1.0,
        "sectors": [{"name": k, "pm25_mean": mean(v), "hours": len(v)} for k, v in by_sector.items()],
        "industrial": {"pm25_mean": mean(industrial), "hours": len(industrial)},
        "other": {"pm25_mean": mean(other), "hours": len(other)},
        "note": "여름철(6~9월) 관측의 단순 평균 비교입니다. 인과 관계를 뜻하지 않습니다.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    times, series = load(DATA)
    feature_rows = {t: ridge.make_features(series, t) for t in times}
    all_samples = samples(times, series, feature_rows)
    results = [evaluate(all_samples, times, split) for split in SPLITS]
    for r in results:
        print(f"검증 {r['validation_from'][:10]}~{r['validation_to'][:10]} (alpha {r['alpha']:g}, {r['scored']}건): "
              f"AI {r['mae_model']} / 유지 {r['mae_persistence']} ㎍/㎥ → {r['improvement_pct']:+.1f}%")
        print("  h  " + " ".join(f"{x['h']:>5}" for x in r["by_horizon"]))
        print("  AI " + " ".join(f"{x['model']:5.2f}" for x in r["by_horizon"]))
        print("  유지" + " ".join(f"{x['persistence']:5.2f}" for x in r["by_horizon"]))

    alpha = choose_alpha(all_samples)
    final = fit_all(all_samples, alpha)
    ev = evidence(times, series)
    print(f"배포 모델 alpha {alpha:g}, 산단 방향 {ev['industrial']} / 그 외 {ev['other']}")
    if args.dry_run:
        return
    model = {
        "name": ridge.MODEL_NAME,
        "version": "1.0.0",
        "trained_at": date.today().isoformat(),
        "data": {
            "file": DATA.relative_to(ROOT).as_posix(),
            "period_from": times[0].isoformat(),
            "period_to": times[-1].isoformat(),
            "sources": ["한국환경공단 에어코리아 대기오염정보(순천 연향동·광양 태인동·여수 여천동)",
                        "Open-Meteo Historical Weather API(순천 좌표, CC BY 4.0)"],
        },
        "features": list(ridge.FEATURES),
        **final,
        "evaluation": results,
        "evidence": ev,
    }
    path = ridge.MODEL_PATH
    path.write_text(json.dumps(model, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"저장: {path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
