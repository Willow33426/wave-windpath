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

시간 순서를 지키는 rolling-origin 방식이다. 각 기준 시각에서 **그 시각까지의 관측만** 써서
12시간을 예측하고, 실제값과 비교해 MAE(평균절대오차)를 낸다. 미래 정보 누수가 없다.

```sh
python scripts/evaluate_baseline.py data/history.csv --hours 12 --split 0.8
python scripts/evaluate_baseline.py data/history.csv --json data/baseline_mae.json
```

입력 CSV (1시간 간격, 시간 오름차순)

```csv
time,pm25,upwind_pm25,wind_direction,wind_speed
2026-09-01T00:00:00+09:00,18,22,110,3.1
```

- `time`: ISO 8601 (KST)
- `pm25`: 순천 측정값 ㎍/㎥
- `upwind_pm25`: 광양·여수 등 상류 측정값
- `wind_direction`: 바람이 불어오는 방향(도), `wind_speed`: m/s

자료가 아직 없으면 동작 확인용 가상 자료를 만들 수 있다. 같은 seed면 같은 파일이 나온다.

```sh
python scripts/make_demo_history.py data/demo_history.csv
python scripts/evaluate_baseline.py data/demo_history.csv
```

출력 예시 (**위 가상 자료 336행의 결과이며 실제 성능이 아니다**)

```text
   예측 시간  persistence  wind_rule
     1h         1.33       1.33
     6h         2.67       2.49
    12h         2.58       2.54

전체 MAE (㎍/㎥)
  baseline-persistence   2.41
  baseline-wind-rule     2.31
```

가상 자료는 "산단 방향 바람일 때 상류 농도를 따라간다"는 가정을 넣어 만든 것이므로,
wind_rule이 이기는 것은 당연하다. **실측 자료에서도 이긴다는 증거가 아니다.**

학습 모델(#3)은 **같은 CSV, 같은 `--split`** 으로 비교해야 조건이 같다.

## 4. 한계

- 통계 규칙이라 특정 시설의 기여를 증명하지 않는다. 화면에는 "영향 가능성"으로만 표기한다.
- 평가에서는 예보값 대신 실제 관측 기상값을 입력으로 쓴다. 실제 운영에서는 예보 오차가 더해진다.
- 상류 측정소가 한 곳뿐이면 다른 오염원(외부 유입, 도로)의 영향을 구분하지 못한다.
- 결측이 많은 구간은 평가에서 제외되므로, 기간별 표본 수(`origins`)를 함께 봐야 한다.

## 5. 다음 단계 (#3)

1. 실측 자료로 위 스크립트를 돌려 기준선 MAE를 기록한다.
2. 같은 분할에서 학습 모델(scikit-learn 등)의 MAE를 비교한다.
3. 더 나은 쪽을 `predict()`에 연결하고, 모델 로드 실패 시 기준선으로 폴백한다.
4. 학습 기간·피처·MAE를 README와 개발보고서에 적는다.
