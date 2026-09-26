# 시민 모드 API 명세 (v0.1)

프론트엔드(#6), 수집(#2), 예측 모델(#3)이 공통으로 쓰는 JSON 계약이다.
산업 모드 계약은 이 문서 범위가 아니다(후속 이슈에서 정의).

- 기준 주소: 운영 `https://a8.scnuoss.net`, 로컬 `http://127.0.0.1:8000`
- 인증 없음. 모든 응답은 `application/json; charset=utf-8`
- mock: [`docs/mock/`](mock) — 프론트엔드는 이 파일로 먼저 개발한다

## 공통 규칙

| 항목 | 규칙 |
|---|---|
| 시각 | ISO 8601, 한국 시간(+09:00) 고정. 예 `2026-09-24T15:00:00+09:00` |
| 단위 | `pm25`·`pm10` ㎍/㎥, `wind_speed_mps` m/s, `temperature_c` ℃ |
| 풍향 | `wind_direction` 0~359 정수(도). **바람이 불어오는 방향**(기상 관례). 북 0, 동 90, 남 180, 서 270 |
| 값 없음 | 숫자·문자열 필드는 `null`. 화면에는 "정보 없음"으로 표시하고 계산에서 제외 |
| 배열 | 길이가 0일 수 있다. `forecast`가 비면 예측 불가 상태 |
| 실패 처리 | 외부 API가 죽어도 캐시·fixture로 200 응답을 준다. 이때 `is_fallback: true` |
| 표현 규칙 | 예측값을 확정 사실이나 특정 시설의 인과관계로 단정하지 않는다. `industrial_influence`는 참고 지표 |

## 1. GET /api/health

서버와 데이터 상태 확인용. 배포 검증(#13)과 모니터링에 쓴다.

```http
GET /api/health
```

```json
{
  "status": "ok",
  "version": "0.1.0",
  "time": "2026-09-24T15:20:00+09:00",
  "db": "ok",
  "last_collected_at": "2026-09-24T15:00:00+09:00",
  "is_fallback": false
}
```

- `status`: `"ok"` | `"degraded"` — 수집이 2시간 이상 밀리면 `degraded`
- `db`: `"ok"` | `"error"`
- `last_collected_at`: 마지막 수집 성공 시각. 수집 이력이 없으면 `null`

## 2. GET /api/citizen/forecast

시민 모드 화면이 쓰는 단일 엔드포인트. 현재 상태 + 향후 N시간 예측 + 행동 권고를 한 번에 준다.

```http
GET /api/citizen/forecast?location=suncheon&hours=12
```

| 파라미터 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `location` | string | `suncheon` | 지역 코드. v0.1은 `suncheon`만 지원 |
| `hours` | int | 12 | 1~24. 범위를 벗어나면 400 |

### 응답 필드

| 필드 | 타입 | null | 설명 |
|---|---|---|---|
| `location` | object | X | `id`, `name`, `lat`, `lon`, `air_station`, `weather_station` |
| `updated_at` | datetime | X | 응답을 만든 시각 |
| `observed_at` | datetime | O | `current` 입력 중 가장 최근 관측 시각. 소스별 시각은 `data_sources[].observed_at` 참조 |
| `is_fallback` | boolean | X | 캐시·fixture로 응답했는지 |
| `data_sources` | array | X | `{name, provider, observed_at, fetched_at, note}` 목록. 화면 하단 출처와 소스별 관측 시각 표기에 사용 |
| `current` | object | O | 현재 상태. 관측이 없으면 `null` |
| `forecast` | array | X | 시간별 예측. 길이는 요청 `hours` 이하 |
| `recommendation` | object | X | 환기·외출 권고 |
| `reason` | string | O | 판단 근거 한 문장. AI 브리핑(#4)이 이 값을 대체할 수 있다 |
| `model` | object | X | 예측에 사용한 모델 정보 |

대기질과 기상 관측은 갱신 주기가 다를 수 있다. `observed_at` 하나로 두 자료가 같은 시각이라고 가정하지 말고, 화면과 계산에서는 반드시 각 `data_sources[].observed_at`을 함께 확인한다.

### 시민 AI 브리핑 (#4)

`GET /api/citizen/briefing?location=suncheon&hours=12&sensitive=false`는 같은 조건의
시민 예보를 2~3문장으로 요약한다. Nginx가 `/api/`를 제거하는 환경에서는
`/citizen/briefing`도 같은 응답을 준다. 응답은 `text`, `source`(`llm` 또는
`template`), `observed_at`을 포함한다. LLM 키가 없거나, 호출 오류·시간 초과·한도
소진·형식 오류가 발생하면 200 응답과 함께 `source=template`을 반환한다. 이 API는
예측 수치나 등급을 변경하지 않는다.

`app/.env`에서 `LLM_PROVIDER`(`none`, `openai`, `gemini`), `LLM_MODEL`,
`LLM_API_KEY`, `LLM_TIMEOUT_SEC`를 설정한다. 기본값 `none`에서는 외부 호출 없이
템플릿만 사용한다. 지원 모델명은 제공자의 API 문서를 확인해 환경변수에 넣는다.
LLM 키는 서버에만 보관하며 응답·로그·정적 화면에 넣지 않는다. 브리핑 결과는
마지막 수집 시각별로 5분간 캐시하고, 단일 워커에서 LLM 호출을 분당 10회로 제한한다.
화면은 브리핑 본문을 `textContent`로 표시한다.

`current` 객체

| 필드 | 타입 | null | 설명 |
|---|---|---|---|
| `pm25`, `pm10` | number | O | 관측 농도 ㎍/㎥ |
| `air_quality` | string | O | `좋음` \| `보통` \| `나쁨` \| `매우나쁨` (PM2.5 기준 `≤15` / `≤35` / `≤75` / `>75`. 예측값은 소수가 나오므로 구간이 아니라 부등호로 정한다) |
| `wind_direction` | int | O | 0~359 |
| `wind_direction_label` | string | O | `남남서` 같은 16방위 한글 표기 |
| `wind_speed_mps` | number | O | |
| `temperature_c` | number | O | |
| `industrial_influence` | object | X | 아래 참조 |

`forecast[]` 항목

| 필드 | 타입 | null | 설명 |
|---|---|---|---|
| `forecast_time` | datetime | X | 정시 기준 |
| `pm25_predicted` | number | O | 예측 농도 ㎍/㎥ |
| `air_quality` | string | O | 위와 같은 4단계 |
| `wind_direction`, `wind_direction_label`, `wind_speed_mps` | | O | 예보 입력값 |
| `industrial_influence` | object | X | |
| `confidence` | number | O | 0~1. 기준선만 쓰면 낮게 준다 |
| `is_fallback` | boolean | X | 이 시각 값이 폴백인지 |

`industrial_influence` 객체 — **참고 지표**이며 인과 증명이 아니다

| 필드 | 타입 | null | 설명 |
|---|---|---|---|
| `level` | string | X | `low` \| `medium` \| `high` \| `unknown` |
| `score` | number | O | 0~1. 상류 측정소 농도와 풍향 일치도로 계산 |
| `upwind_facilities` | string[] | X | 바람이 불어오는 쪽 시설 이름. 없으면 빈 배열 |
| `note` | string | O | 화면에 그대로 보여 줄 수 있는 설명 |

`recommendation` 객체

| 필드 | 타입 | 설명 |
|---|---|---|
| `ventilation.status` | string | `good` \| `caution` \| `avoid` |
| `ventilation.windows` | array | `{start, end}` 구간 목록(시간대 추천) |
| `ventilation.text` | string | 화면 문구 |
| `outdoor.status`, `outdoor.windows`, `outdoor.text` | | 외출 권고, 구조 동일 |
| `summary` | string | 한 줄 요약 |

`model` 객체: `name`(예 `baseline-persistence`, `wind-rule`, `gbm-v1`), `version`, `mae_validation`(㎍/㎥, 없으면 `null`), `trained_at`

### 정상 응답

[`docs/mock/citizen_forecast.json`](mock/citizen_forecast.json)

### 데이터 부족·폴백 응답

외부 API 장애나 키 미발급 상태에서도 화면이 뜨도록 fixture로 응답한다.
`is_fallback: true`, `confidence: null`, `data_sources[].note`에 폴백 사유를 적는다.

> **주의**: mock 파일의 시각을 그대로 내보내면 안 된다. `updated_at`·`observed_at`·
> `forecast[].forecast_time`과 "몇 시간 전" 같은 문구는 **응답을 만드는 시각 기준으로 다시
> 계산**한다. 고정 시각을 그대로 쓰면 며칠 뒤에는 오래된 값이 방금 것처럼 보인다.
> 서버는 `app/collector.py`의 `_rebase()`가 같은 일을 한다.

[`docs/mock/citizen_forecast_fallback.json`](mock/citizen_forecast_fallback.json)

### 오류 응답

캐시도 fixture도 없을 때만 오류를 낸다.

| 상태 | code | 상황 |
|---|---|---|
| 400 | `INVALID_PARAMETER` | `hours` 범위 초과, 지원하지 않는 `location` |
| 503 | `UPSTREAM_UNAVAILABLE` | 외부 API 실패 + 캐시·fixture 없음 |

```json
{
  "error": {
    "code": "UPSTREAM_UNAVAILABLE",
    "message": "대기질 데이터를 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.",
    "retry_after_sec": 300
  }
}
```

오류 메시지에는 키·내부 경로·스택 트레이스를 넣지 않는다(AGENTS.md 보안 규칙).

## 모델 연결 방법 (#3)

예측 모델은 아래 형태만 지키면 응답 변환 없이 붙는다.

```python
predict(features) -> list[dict]   # 길이 = hours
# dict 키: forecast_time, pm25_predicted, confidence
```

`air_quality`는 `pm25_predicted`에서 서버가 계산하고, `industrial_influence`는 풍향·상류 측정소 값으로 서버가 채운다.
모델 로드 실패·데이터 부족이면 기준선(persistence)으로 폴백하고 `model.name`을 그대로 바꿔 표기한다.

## 산단 방향 기준 (순천 시청 기준 방위)

`industrial_influence`는 아래 방위와 `wind_direction`(바람이 불어오는 방향)을 비교해 판단한다.

| 시설 | 방위 | 거리 |
|---|---|---|
| 광양제철소 | 약 101° (동남동) | 약 24 km |
| 여수국가산단 | 약 117° (동남동~남동) | 약 23 km |

- v0.1 기준: `wind_direction`이 **100~135°**이면 "산단 쪽에서 바람이 오는 시간대"로 본다.
- 허용 폭과 점수 산식은 #3에서 검증 후 조정하고, 바뀌면 이 문서도 함께 고친다.

## 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| 0.1 | 2026-09-24 | 최초 작성(#1) |
