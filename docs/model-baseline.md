# 기준선 예측과 평가 방법

학습 모델(#3)을 붙이기 전에 쓸 기준선이자, 모델이 없을 때의 폴백이다.
표준 라이브러리만 쓰므로 서버 메모리 한도(1.2GB) 안에서 가볍게 돈다.

## 1. 두 가지 기준선

| 이름 | 방식 | 쓰는 곳 |
|---|---|---|
| `baseline-persistence` | 마지막 관측값을 12시간 동안 유지 | 대기질 예측의 표준 기준선, 최종 폴백 |
| `baseline-wind-rule` | 산단 쪽에서 바람이 불 때만 상류 측정소 농도 쪽으로 보정 | 기본 제공값 |

`wind_rule`은 인과 모형이 아니다. "바람이 그쪽에서 불고 상류 농도가 높다"는 관측 사실만 반영한다.

### 보정 규칙

```text
예측 = 마지막 관측값 + α × 영향점수 × 감쇠 × (상류 관측값 − 마지막 관측값)
```

- α = 0.6 (상류 농도를 최대 60%까지만 반영)
- 영향점수: 풍향이 시설 방위 ±25° 안일 때 1에 가깝고, 벗어날수록 0. 풍속 3 m/s 이상이면 최대
- 감쇠: `exp(-h/8)` — 먼 시간일수록 보정을 줄인다
- 도달 시간: `거리 ÷ 풍속`보다 이른 시각에는 보정하지 않는다 (24 km·3 m/s → 약 2.2시간)
- 풍속이 없거나 0.3 m/s 이하이면 도달 시간을 계산할 수 없으므로 보정하지 않는다
- 예보가 비는 시각은 보정하지 않고 `baseline-persistence`로 표시한다 (신뢰도도 낮춘다)
- 상류 농도가 우리 동네보다 낮으면 보정하지 않는다 (예측을 낮추지 않는다)

시설 방위는 순천 시청 기준이며 `docs/api.md`와 같은 값을 쓴다.

| 시설 | 방위 | 거리 |
|---|---|---|
| 광양제철소 | 101° | 24 km |
| 여수국가산단 | 117° | 23 km |

## 2. 인터페이스

```python
from app.forecast.baseline import Observation, WeatherPoint, predict

items = predict({
    "target_history": [Observation(time, pm25, wind_direction, wind_speed), ...],
    "upwind_history": [Observation(time, pm25), ...],
    "weather":        [WeatherPoint(time, wind_direction, wind_speed), ...],
}, hours=12)
```

반환값은 `docs/api.md`의 `forecast[]` 형식이다(`forecast_time`, `pm25_predicted`, `air_quality`,
`confidence`, `industrial_influence`). 학습 모델도 같은 형식으로 돌려주면 응답 변환이 필요 없다.

폴백 순서: 학습 모델 → `wind_rule` → `persistence` → 빈 배열.
상류 관측이나 예보가 없으면 자동으로 `persistence`로 내려간다.

## 3. 평가 방법

시간 순서를 지키는 rolling-origin 방식이다. 각 기준 시각에서 **그 시각까지의 관측과 그때 이미
발행된 기상 예보만** 써서 12시간을 예측하고, 실제값과 비교해 MAE(평균절대오차)를 낸다.

```sh
python scripts/evaluate_baseline.py data/history.csv \
  --weather-forecast data/weather_forecast.csv --hours 12 --split 0.8
python scripts/evaluate_baseline.py data/history.csv \
  --weather-forecast data/weather_forecast.csv --json data/baseline_mae.json
```

입력 CSV (1시간 간격, 시간 오름차순)

```csv
time,pm25,upwind_pm25
2026-09-01T00:00:00+09:00,18,22
```

- `time`: ISO 8601 (KST)
- `pm25`: 순천 측정값 ㎍/㎥
- `upwind_pm25`: 광양·여수 등 상류 측정값

보관된 기상 예보 CSV는 **예보 발행 시각과 목표 시각을 모두** 포함해야 한다.

```csv
issued_at,target_time,wind_direction,wind_speed
2026-09-01T00:00:00+09:00,2026-09-01T01:00:00+09:00,110,3.1
```

- `issued_at`: 해당 예보를 실제로 알 수 있었던 발행 시각
- `target_time`: 예보 대상 시각
- 같은 목표 시각의 예보가 여러 개면 평가 기준 시각 이전에 발행된 최신본을 사용한다.
- 보관된 예보가 없으면 미래 관측 풍향을 대신 쓰지 않고 `wind_rule`도 persistence로 평가한다.

자료가 아직 없으면 동작 확인용 가상 자료를 만들 수 있다. 같은 seed면 같은 파일이 나온다.

```sh
python scripts/make_demo_history.py data/demo_history.csv
python scripts/evaluate_baseline.py data/demo_history.csv
```

이 명령은 평가 코드의 동작만 확인한다. 가상 자료에는 발행 이력이 있는 예보가 없으므로 두
기준선의 결과가 같아진다. `wind_rule` 성능 비교는 실제 보관 예보를 함께 제공한 뒤에만 한다.

학습 모델(#3)은 **같은 CSV, 같은 `--split`** 으로 비교해야 조건이 같다.

## 4. 한계

- 통계 규칙이라 특정 시설의 기여를 증명하지 않는다. 화면에는 "영향 가능성"으로만 표기한다.
- 과거 시점에 발행된 예보를 보관하지 않았다면 `wind_rule`의 과거 성능을 소급 평가할 수 없다.
- 상류 측정소가 한 곳뿐이면 다른 오염원(외부 유입, 도로)의 영향을 구분하지 못한다.
- 결측이 많은 구간은 평가에서 제외되므로, 기간별 표본 수(`origins`)를 함께 봐야 한다.

## 5. 다음 단계 (#3)

1. 실측 자료로 위 스크립트를 돌려 기준선 MAE를 기록한다.
2. 같은 분할에서 학습 모델(scikit-learn 등)의 MAE를 비교한다.
3. 더 나은 쪽을 `predict()`에 연결하고, 모델 로드 실패 시 기준선으로 폴백한다.
4. 학습 기간·피처·MAE를 README와 개발보고서에 적는다.
