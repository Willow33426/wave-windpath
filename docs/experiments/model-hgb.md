# 부스팅 모델 실험 기록 (#3)

> 초기 모델 실험 기록입니다(`feature/air-quality-model`). 서비스에는 90일 실측으로 같은 조건 비교를 한 뒤 릿지 회귀를 채택했고, 그 결과는 [docs/model-ai.md](../model-ai.md)에 있습니다. 모델 코드(`app/forecast/model.py`)는 위 브랜치에 남아 있습니다.

## 실행 방법

프로젝트 루트에서 다음 명령을 순서대로 실행한다.

```cmd
python -m pip install -r requirements.txt
python scripts\make_demo_history.py data\demo_history.csv
python scripts\evaluate_baseline.py data\demo_history.csv --hours 12 --split 0.8
python scripts\evaluate_model.py data\demo_history.csv --hours 12 --split 0.8
```

## 데이터와 입력 특성

개발용 데이터는 2026-09-01 00:00부터 2026-09-14 23:00까지의 시간별 가상 데이터 336행이다. 앞쪽 80%로 예측 시간별 모델 12개를 학습하고, 2026-09-12 04:00(+09:00)부터 검증한다. 학습에는 분할 경계 이전의 입력과 정답만 사용한다.

입력 특성은 현재·1시간 전·3시간 전 PM2.5, 최신 상류 PM2.5, 풍향의 sine·cosine, 풍속, 시간대의 sine·cosine이다.

## 개발용 평가 결과

| 방법 | 전체 MAE (㎍/㎥) |
|---|---:|
| Persistence | 2.41 |
| Wind rule (과거 발행 예보 없음, persistence로 폴백) | 2.41 |
| 학습 모델 | 2.01 |

이 수치는 가상 데이터에서 나온 결과이며 실제 서비스 성능이 아니다. `evaluate_model.py`는 ML과 persistence를 같은 기준 시각·예측 시간·정답에 대해 함께 채점한다. PR #18 이후 과거 발행 예보가 없는 평가에서 wind rule은 persistence로 폴백한다. 운영 모델 선정은 실제 시각에 이용 가능했던 예보를 기록한 뒤 같은 조건으로 다시 평가해야 한다.

## 90일 실측 평가 (2026-09-26 수령)

팀 제공 자료 `history_delivery_20260926.zip`의 `history.csv`는 2026-06-23 00:00부터 2026-09-20 23:00(KST)까지 시간별 2,160행이다. 순천 PM2.5가 있는 시간은 1,944행(90.0%)이다. 압축 해제 자료는 Git에서 제외되는 `data/history_export/`에 보관한다. `summary.json`의 순천 2,076건은 시간별 CSV의 비결측 행 수와 다르다. 집계 기준은 확인이 필요하며 시간별 평가의 표본 수로 사용하지 않는다.

```cmd
python scripts\evaluate_baseline.py data\history_export\history.csv --hours 12 --split 0.8
python scripts\evaluate_model.py data\history_export\history.csv --hours 12 --split 0.8
python scripts\evaluate_model.py data\history_export\history.csv --hours 12 --split 0.8 --observed-weather data\history_export\weather_observed.csv
```

| 평가 | ML MAE | 동일 표본 persistence MAE | 해석 |
|---|---:|---:|---|
| PM2.5와 시간 특성만 사용 | 3.79 | 3.63 | ML이 기준선보다 낮은 성능 |
| 같은 시각의 관측·재분석 기상 사용 | 3.61 | 3.63 | 탐색적 결과. 차이가 0.02로 작고 실시간 이용 가능성 미검증 |

검증 시작은 2026-09-03 00:00 KST이며 분할 비율은 0.8이다. 기준선 단독 스크립트의 전체 persistence MAE는 3.61이지만 ML과 동일 표본으로 비교한 값은 3.63이다. 기상 자료는 Open-Meteo Historical Weather API의 관측·재분석 자료로, 과거에 발표된 예보가 아니다. 이를 `--weather-forecast`에 넣지 않는다. 당시 발행 예보가 없으므로 wind rule은 persistence로 평가되고, 현재 결과만으로 ML 운영 모델을 선택하지 않는다.

## 모델 저장과 제한

`save_models()`와 `load_models()`로 12개 모델을 저장·불러올 수 있다. `predict(features)`는 기본 경로 `data/model.joblib`에서 모델을 찾고, 모델 파일이나 필요한 과거 관측이 없으면 `wind_rule` 또는 `persistence` 기준선으로 대체한다. 학습은 결측된 정답 행을 제외하고, 상류 관측과 기상 입력의 결측은 모델 입력에서 결측값으로 처리한다.

`data/demo_model.joblib`은 로컬 동작 확인용이며 운영 모델이 아니다. 서버에는 검증된 실데이터 모델을 아직 연결하지 않았다. 실관측 이력이 충분히 쌓이면 누락값과 데이터 출처를 확인하고 실데이터로 다시 학습·평가해야 한다.

## 서버 입력 연결 준비

`app.forecast.inputs.build_features(conn, hours=12)`는 SQLite의 순천 PM2.5·풍향·풍속 관측, 광양(없으면 여수) PM2.5 관측, 순천 풍향·풍속 예보를 `predict()` 입력으로 묶는다. 현재 시각까지 수집된 값만 읽으며, PM2.5 실측이 없으면 빈 이력을 반환한다. `GET /api/citizen/forecast?location=suncheon&hours=12`가 이 입력으로 예측을 실행해 #1 응답 형식으로 돌려준다. 실측으로 검증된 운영 모델 파일이 없으면 기준선을 사용한다.

PR #18의 `data_origin` 열이 있으면 평상시 예측 입력에서 fixture 행을 제외한다. 수집기가 fixture 폴백 상태를 표시한 경우에만 fixture를 화면 응답에 포함하고 `is_fallback`으로 표시한다.

현재 DB는 같은 예보 대상 시각의 값을 새 발표분으로 덮어쓴다. 과거 시점에 이용할 수 있었던 예보로 공정하게 재평가하려면 예보 발표 이력을 별도로 보존해야 한다.

## 과거 실측 확보

승인된 에어코리아 키가 `app/.env`에 설정된 환경에서 `python scripts/export_airkorea_history.py`를 실행하면 최근 한 달 순천·광양·여수 PM2.5를 `data/airkorea_month_history.csv`로 내보낸다. 키는 CSV에 기록하지 않는다. 이 CSV에는 과거 기상 관측이 없으므로 풍향 규칙은 사실상 persistence가 되며, 학습 모델도 기상 변수를 사용하지 못한다. 따라서 이 CSV만으로 얻은 MAE를 기상 변수를 모두 쓴 운영 모델의 성능으로 표시하지 않는다. CSV는 `data/`에 보관하고 Git에 추가하지 않는다.
