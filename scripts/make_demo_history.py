"""평가 스크립트를 바로 돌려 볼 수 있는 가상 관측 자료 생성.

실측 자료가 준비되기 전까지 `scripts/evaluate_baseline.py`의 동작을 확인하는 용도다.
실제 성능을 재는 자료가 아니다. 같은 seed면 같은 파일이 나온다.

    python scripts/make_demo_history.py data/demo_history.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
FACILITY_BEARING = 109.0        # 광양(101°)·여수(117°)의 가운데


def generate(hours: int = 336, seed: int = 20260924,
             start: datetime | None = None) -> list[dict]:
    random.seed(seed)
    start = start or datetime(2026, 9, 1, tzinfo=KST)
    pm25, upwind = 20.0, 26.0
    rows = []
    for i in range(hours):
        direction = FACILITY_BEARING + 60 * math.sin(i / 11) + random.gauss(0, 18)
        speed = max(0.4, 2.8 + 1.4 * math.sin(i / 7) + random.gauss(0, 0.6))
        upwind += (26 - upwind) * 0.25 + random.gauss(0, 3.2)          # 평균으로 되돌아온다
        toward = abs(((direction - FACILITY_BEARING) + 180) % 360 - 180) < 25
        pm25 += (20 - pm25) * 0.25 + (0.18 * (upwind - pm25) if toward else 0.0) \
            + random.gauss(0, 1.6)
        rows.append({
            "time": (start + timedelta(hours=i)).isoformat(),
            "pm25": round(max(2.0, pm25), 1),
            "upwind_pm25": round(max(2.0, upwind), 1),
            "wind_direction": round(direction % 360, 1),
            "wind_speed": round(speed, 1),
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="가상 관측 자료 생성(동작 확인용)")
    parser.add_argument("out", type=Path, nargs="?", default=Path("data/demo_history.csv"))
    parser.add_argument("--hours", type=int, default=336)
    parser.add_argument("--seed", type=int, default=20260924)
    args = parser.parse_args()

    rows = generate(args.hours, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)}행 생성: {args.out}")


if __name__ == "__main__":
    main()
