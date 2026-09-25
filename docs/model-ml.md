# PM2.5 학습 모델 (#3)

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
| Wind rule | 2.31 |
| 학습 모델 | 2.01 |

이 수치는 가상 데이터에서 나온 결과이며 실제 서비스 성능이 아니다. `evaluate_model.py`는 ML과 persistence를 같은 기준 시각·예측 시간·정답에 대해 함께 채점한다. 현재 wind rule 평가는 미래의 실제 풍향·풍속을 예보 대신 사용하므로, 운영 환경에서 동일한 입력 조건의 비교로 해석할 수 없다. 운영 모델 선정은 실제 시각에 이용 가능했던 예보를 기록한 뒤 같은 조건으로 다시 평가해야 한다.

## 모델 저장과 제한

`save_models()`와 `load_models()`로 12개 모델을 저장·불러올 수 있다. `predict(features)`는 기본 경로 `data/model.joblib`에서 모델을 찾고, 모델 파일이나 필요한 과거 관측이 없으면 `wind_rule` 또는 `persistence` 기준선으로 대체한다. 학습은 결측된 정답 행을 제외하고, 상류 관측과 기상 입력의 결측은 모델 입력에서 결측값으로 처리한다.

`data/demo_model.joblib`은 로컬 동작 확인용이며 운영 모델이 아니다. 서버에는 검증된 실데이터 모델을 아직 연결하지 않았다. 실관측 이력이 충분히 쌓이면 누락값과 데이터 출처를 확인하고 실데이터로 다시 학습·평가해야 한다.

## 서버 입력 연결 준비

`app.forecast.inputs.build_features(conn, hours=12)`는 SQLite의 순천 PM2.5·풍향·풍속 관측, 광양(없으면 여수) PM2.5 관측, 순천 풍향·풍속 예보를 `predict()` 입력으로 묶는다. 현재 시각까지 수집된 값만 읽으며, PM2.5 실측이 없으면 빈 이력을 반환한다. `GET /api/citizen/forecast?location=suncheon&hours=12`가 이 입력으로 예측을 실행해 #1 응답 형식으로 돌려준다. 실측으로 검증된 운영 모델 파일이 없으면 기준선을 사용한다.

현재 DB는 같은 예보 대상 시각의 값을 새 발표분으로 덮어쓴다. 과거 시점에 이용할 수 있었던 예보로 공정하게 재평가하려면 예보 발표 이력을 별도로 보존해야 한다.

## 과거 실측 확보

승인된 에어코리아 키가 `app/.env`에 설정된 환경에서 `python scripts/export_airkorea_history.py`를 실행하면 최근 한 달 순천·광양·여수 PM2.5를 `data/airkorea_month_history.csv`로 내보낸다. 키는 CSV에 기록하지 않는다. 이 CSV에는 과거 기상 관측이 없으므로 풍향 규칙은 사실상 persistence가 되며, 학습 모델도 기상 변수를 사용하지 못한다. 따라서 이 CSV만으로 얻은 MAE를 기상 변수를 모두 쓴 운영 모델의 성능으로 표시하지 않는다. CSV는 `data/`에 보관하고 Git에 추가하지 않는다.
