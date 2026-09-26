# 데이터 출처와 API 키 발급

수집 모듈(#2)이 쓰는 공공데이터와 환경변수를 정리한다.
키 값은 이 문서나 코드에 적지 않는다. `app/.env`에만 둔다.

## 1. 발급 절차

1. [공공데이터포털](https://www.data.go.kr) 회원가입 후 로그인
2. 아래 두 서비스에서 **활용신청** (자동승인이지만 반영에 시간이 걸릴 수 있다)
   - 기상청\_단기예보 조회서비스 (`VilageFcstInfoService_2.0`)
   - 한국환경공단\_에어코리아\_대기오염정보 (`ArpltnInforInqireSvc`)
3. 마이페이지 > 개발계정에서 **일반 인증키(Decoding)** 를 복사
4. `app/.env`의 `DATA_GO_KR_SERVICE_KEY`에 붙여 넣는다 (두 서비스가 같은 키를 쓴다)

승인 전이거나 키가 없으면 앱은 `app/fixtures/`의 샘플로 동작한다. 각 행의
`data_origin`이 `fixture`로 저장되며, 실제 수집값은 `live`다. 응답의 `is_fallback`은
마지막 수집 실행에서 하나 이상의 fixture를 사용했는지를 나타낸다.

## 2. 사용 데이터

| 데이터 | 제공 기관 | 사용 항목 | 갱신 |
|---|---|---|---|
| 단기예보 조회서비스 | 기상청 | 풍향(VEC), 풍속(WSD), 기온(TMP), 습도(REH), 강수확률(POP) | 1일 8회 발표 |
| 초단기실황 조회서비스 | 기상청 | 풍향, 풍속, 기온, 습도 | 매시 |
| 대기오염정보 조회서비스 | 한국환경공단 에어코리아 | PM10, PM2.5, SO2, NO2, O3 | 매시 |

측정소 (v0.1)

| 키 | 측정소 | 역할 | 기상청 격자 |
|---|---|---|---|
| `suncheon` | 순천 연향동 | 예측 지점 | nx 70, ny 70 |
| `gwangyang` | 광양 태인동 | 상류(산단 방향) 참고 | nx 73, ny 70 |
| `yeosu` | 여수 여천동 | 상류(산단 방향) 참고 | nx 73, ny 66 |

격자 좌표와 측정소명은 첫 실측 때 응답을 보고 확정한다. 틀리면 이 표와 `app/config.py`를 함께 고친다.

- 에어코리아 조회 이름은 `연향동`, `태인동`, `여천동(여수)`다. 여수 측정소는 울산 여천동과 구분하려고 2026-09에 `여천동(여수)`로 바뀌었다. 옛 이름으로 조회하면 오류 없이 0건이 와서, 수집기는 이때 경고 로그(`응답 0건`)를 남긴다.

## 3. 환경변수

| 이름 | 예시 | 설명 |
|---|---|---|
| `DATA_GO_KR_SERVICE_KEY` | (비움) | 공공데이터포털 인증키(Decoding). 기상청·에어코리아 공용 |
| `COLLECT_INTERVAL_MINUTES` | `30` | 앱 내부 스케줄러 수집 주기 |
| `DB_PATH` | `data/wave.db` | SQLite 경로. `data/`는 커밋하지 않는다 |
| `REQUEST_TIMEOUT_SEC` | `10` | 외부 API 타임아웃 |
| `REQUEST_RETRIES` | `2` | 재시도 횟수(지수 대기) |

## 4. 저장 구조

`measurements` 한 테이블에 관측·예보를 함께 담는다.

- 학습·실측 평가에는 `data_origin='live'`인 행만 사용한다.
- API에서 fixture를 제외하려면 `/api/observations?include_fixture=false`로 조회한다.

| 컬럼 | 설명 |
|---|---|
| `source` | `kma` \| `airkorea` |
| `station` | 위 표의 키 |
| `kind` | `observation` \| `forecast` |
| `base_time` | 관측 시각 또는 예보 발표 시각 |
| `target_time` | 값이 가리키는 시각 |
| `metric` | `pm25`, `pm10`, `wind_direction`, `wind_speed`, `temperature`, `humidity`, `precipitation_prob` |
| `value`, `unit` | 숫자와 단위(`ug/m3`, `deg`, `m/s`, `C`, `%`) |
| `data_origin` | `live`(실수집) \| `fixture`(샘플 폴백) |
| `collected_at` | 수집 시각 |

기본키가 `(source, station, kind, target_time, metric)`이라 같은 값을 여러 번 수집해도 행이 늘지 않고 최신 값으로 갱신된다.

## 5. 수집 실행

```sh
python -m app.collector          # 1회 수집 (로그와 요약 출력)
python app/main.py               # 서버 실행 시 앱 안의 스케줄러가 주기 수집
curl http://127.0.0.1:8000/api/observations?station=suncheon&metric=pm25
```

## 6. 출처 표기

화면과 최종 제출물에는 아래 문구를 넣는다. API 응답의 `data_sources` 배열에도 같은 내용이 들어간다.

> 자료 출처: 기상청 단기예보·초단기실황(공공데이터포털), 한국환경공단 에어코리아 대기오염정보(공공데이터포털)

공공데이터포털 이용 조건을 따른다. 데이터별 이용허락 범위(공공누리 유형)는 최종 제출 때 확인해 함께 적는다.
