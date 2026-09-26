"""승인된 에어코리아 API에서 최근 한 달 PM2.5 실측을 평가 CSV로 내보낸다.

앱 설정의 서비스 키를 읽어 사용하지만 키와 원본 요청 URL은 출력하지 않는다.
CSV에는 순천·광양(없으면 여수) PM2.5만 담는다. 과거 기상 관측은 이
API에 없으므로 빈칸으로 두며, 풍향 규칙과 기상 변수 평가는 하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import STATIONS, load_settings  # noqa: E402
from app.sources.fetch import AIRKOREA_URL, _get_json  # noqa: E402
from app.sources.parse import UpstreamError, parse_airkorea  # noqa: E402


def fetch_pm25(station, settings) -> dict:
    params = {
        "serviceKey": settings.service_key,
        "returnType": "json",
        "numOfRows": 1000,
        "pageNo": 1,
        "stationName": station.air_station,
        "dataTerm": "MONTH",
        "ver": "1.3",
    }
    payload = _get_json(AIRKOREA_URL, params, settings)
    body = payload.get("response", {}).get("body", {})
    if int(body.get("totalCount", 0)) > params["numOfRows"]:
        raise UpstreamError("한 페이지에 담기지 않은 관측값이 있습니다")
    return {record.target_time: record.value
            for record in parse_airkorea(payload, station.key)
            if record.metric == "pm25"}


def rows_for_csv(values: dict[str, dict]) -> list[list]:
    target = values["suncheon"]
    gwangyang = values["gwangyang"]
    yeosu = values["yeosu"]
    return [[time.isoformat(), target[time],
             gwangyang.get(time, yeosu.get(time, "")), "", ""]
            for time in sorted(target)]


def main() -> None:
    parser = argparse.ArgumentParser(description="에어코리아 실측 평가 CSV 내보내기")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "data" / "airkorea_month_history.csv")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.has_service_key:
        raise SystemExit("앱 서비스 키가 설정되지 않아 내보내기를 실행할 수 없습니다.")

    try:
        values = {station.key: fetch_pm25(station, settings)
                  for station in STATIONS}
    except UpstreamError as exc:
        raise SystemExit(f"에어코리아 자료 조회 실패: {exc}") from None
    rows = rows_for_csv(values)
    if not rows:
        raise SystemExit("순천 PM2.5 실측이 없어 CSV를 만들지 않았습니다.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["time", "pm25", "upwind_pm25", "wind_direction", "wind_speed"])
        writer.writerows(rows)
    paired = sum(bool(row[2] != "") for row in rows)
    print(f"순천 PM2.5 {len(rows)}시간, 상류 동시 관측 {paired}시간 저장: {args.output}")
    print("과거 기상값이 없으므로 이 CSV의 풍향·풍속 입력은 비어 있습니다.")


if __name__ == "__main__":
    main()
