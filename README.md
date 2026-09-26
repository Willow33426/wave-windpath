# Wave 바람길

> 바람 데이터로 잇는 우리 동네 공기 예보 × AI 데이터센터 RE100 시뮬레이터
>
> 2026 SCNU OSS·AI 해커톤 경진대회 고급 트랙 · 팀 Wave

- **서비스 주소**: <https://a8.scnuoss.net/> (휴대폰 화면 기준)
- **상태**: 시민 모드 운영 중 (실데이터 자동 수집 + AI 12시간 예측 + 환기 추천). 산업 모드는 향후 계획
- **작업 목록**: [Issues](https://github.com/Willow33426/wave-windpath/issues)

## 한 줄 소개

같은 바람이 주민에게는 **공기가 오는 길**이고, 데이터센터에는 **전기와 냉각의 자원**입니다.
Wave 바람길은 기상 데이터 하나로 두 가지 질문에 답합니다.

| 모드 | 질문 | 대상 |
|---|---|---|
| 시민 모드 | 지금 우리 동네로 부는 바람은 어디에서 오나? 언제 환기·외출하면 좋은가? | 순천 등 광양만권 주민 |
| 산업 모드 (향후) | 이 후보지에 AI 데이터센터를 지으면 재생에너지로 전기를 얼마나 댈 수 있나? | 지자체·기업 유치 담당자 (해남 등 후보지) |

## 배경

- 순천은 광양제철소·광양국가산단·여수국가산단과 가깝습니다. 순천 시청 기준으로 광양제철소는 동쪽 101°, 여수국가산단은 동남동 117° 방향이라 **동~동남동풍**이 불 때 산단 쪽 공기가 도심으로 올 수 있습니다.
- 전남은 AI 데이터센터(해남 등)와 RE100 반도체 국가산단(순천) 유치도 추진하고 있어 산업 시설이 더 늘어날 예정입니다.
- 기존 대기질 앱은 측정값과 지역 단위 예보 등급을 보여 줍니다. 특정 시설에서 우리 동네 쪽으로 바람이 부는 시간대는 알려 주지 않습니다.
- 재생에너지 매칭 시뮬레이터는 대부분 해외 기업용 도구라서 지역 주민과 지자체가 쓰기 어렵습니다.

## 주요 기능

### 시민 모드 (순천) — 운영 중

- **지금 환기해도 될까요?**: 현재 초미세먼지와 바람으로 한 줄 결론(좋아요 / 짧게 / 닫아 두세요)과 외출 권고
- **바람길 나침반**: 바람이 어느 쪽에서 순천으로 불어오는지, 광양·여수 산단 방향(동~동남동)과 겹치는지 표시
- **AI 12시간 예측**: 순천·광양·여수 측정소와 순천 바람·기온·습도로 학습한 모델. 학습에 쓰지 않은 기간 실측으로 검증한 평균 오차 3.39㎍/㎥, '지금 값 유지'보다 10.5% 정확 ([모델 문서](docs/model-ai.md))
- **환기 추천 시간**: 예측이 '좋음'이면서 산단 쪽 바람이 아닌 시간대를 묶어 표시
- **실측 근거**: 지난 90일 바람 방향별 순천 초미세먼지 평균을 그대로 공개
- 공공데이터 30분 주기 자동 수집. 외부 API가 멈추면 대체 자료와 기준선 예측으로 계속 동작

### 향후 계획

- 산업 모드: 해남 등 AI 데이터센터 후보지의 재생에너지 자급률(RE100)·외기 냉각 시간 계산 (#5)
- AI 한 줄 브리핑(LLM) (#4), SmartThings 공기청정기 연동 (#7)
- 겨울·봄 고농도 계절 자료로 재학습

## 데이터

| 데이터 | 제공처 | 용도 |
|---|---|---|
| 단기예보 (시간별 풍향·풍속·기온) | 기상청 (공공데이터포털) | 시간별 '산단 쪽 바람' 표시, 환기 추천 시간 |
| 초단기실황 (현재 풍향·풍속·기온·습도) | 기상청 (공공데이터포털) | 바람길 나침반, AI 예측 입력 |
| 대기오염정보 실시간 (순천 연향동·광양 태인동·여수 여천동) | 한국환경공단 에어코리아 (공공데이터포털) | 현재 상태, AI 예측 입력 |
| 과거 시간별 기상 (순천 좌표) | Open-Meteo Historical Weather API | AI 학습 (90일) |
| 산단 방위 | 공개 좌표로 계산 (순천 시청 기준 광양제철소 101°, 여수국가산단 117°) | 산단 방향 바람 판정 (±25°) |

계산·예측 결과는 의사결정을 돕는 추정치입니다. 특정 시설의 영향을 증명하지 않으며, 모델 오차와 가정을 화면에 공개합니다.

## 기술 구성

```mermaid
flowchart LR
  KMA[기상청 단기예보·초단기실황] --> COL[수집 · 앱 내부 스케줄러 30분]
  AIR[에어코리아 순천·광양·여수 PM2.5] --> COL
  COL --> DB[(SQLite)]
  DB --> API[FastAPI /api/citizen/forecast]
  ML[AI 예측 · 시간별 릿지 회귀] --> API
  HIST[90일 실측 + Open-Meteo 과거 기상] -. 학습 scripts/train_ridge.py .-> ML
  API --> WEB[시민 화면 · 모바일 웹]
```

- 백엔드: Python, FastAPI
- 예측 모델: 예측 시간마다 따로 학습한 릿지 회귀 14개. 외부 패키지 없이 학습·추론하고, 서버는 계수 파일(`app/forecast/ridge_model.json`)만 읽습니다. 입력이 모자라면 기준선(지금 값 유지·풍향 규칙)으로 자동 전환합니다.
- 프론트엔드: 단일 HTML·CSS·JS(외부 라이브러리 없음). 대회 서버 Nginx가 첫 화면을 제공하고 `/api/`는 FastAPI로 넘깁니다.
- 배포: 대회 제공 서버, 팀 a8 (<https://a8.scnuoss.net/> → 내부 포트 3108). 사용자 권한 Supervisor와 crontab `@reboot`
- AI 코딩 도구: OpenAI Codex, Claude Code. 두 도구 모두 [AGENTS.md](AGENTS.md) 규칙을 따릅니다.

### 서버 한도에 맞춘 설계

대회 서버는 팀별로 **메모리 1.2GB, 프로세스 2개**를 씁니다 (<https://app.scnuoss.net/> 기준).

- 프로세스는 Supervisor와 앱(uvicorn 워커 1개) 두 개만 둡니다.
- 데이터 수집은 별도 프로세스 대신 앱 안의 스케줄러로 돌립니다.
- LLM은 서버에서 돌리지 않고 외부 API로 호출합니다. 예측 모델은 가벼운 모델만 올립니다.

## 저장소 구조

```text
requirements.txt              # 앱 의존성
app/
  main.py                     # FastAPI 앱, 수집 스케줄러 시작
  citizen.py                  # 시민 화면 응답 조립(예측·환기 추천·근거)
  collector.py, scheduler.py  # 공공데이터 수집(30분 주기), 실패 시 대체 자료
  sources/                    # 기상청·에어코리아 호출과 정규화
  forecast/ridge.py           # AI 예측 모델(릿지) 학습·추론 함수
  forecast/ridge_model.json   # 학습된 계수와 검증 결과
  forecast/baseline.py        # 기준선 예측(폴백)
  forecast/training/          # 학습 자료(90일 시간별)
  static/index.html           # 시민 화면
scripts/train_ridge.py        # 모델 학습·검증
scripts/compare_models.py     # 모델 비교 실험(선택, scikit-learn 필요)
deploy/                       # supervisord 설정, 한 줄 배포 스크립트
docs/                         # API 명세, 모델 문서, 데이터 출처
tests/                        # 단위 테스트 (CI에서 PR마다 실행)
AGENTS.md                     # 팀원과 AI 코딩 도구가 함께 따르는 작업 규칙
THIRD_PARTY_NOTICES.md        # 가져다 쓴 오픈소스의 저작권·라이선스 고지
```

실제 설정은 `app/.env`와 `deploy/.env`에 두며 Git에 커밋하지 않습니다.

## 로컬 실행

내 컴퓨터에서 서버를 띄워 보는 방법입니다. 저장소 폴더에서 위부터 차례로 실행합니다.

**Windows (PowerShell)**

```powershell
python -m venv .venv                  # 가상환경 만들기 (처음 한 번)
.venv\Scripts\Activate.ps1            # 가상환경 켜기
pip install -r requirements.txt       # 필요한 패키지 설치
copy app\.env.example app\.env        # 설정 파일 만들기 (처음 한 번)
python app\main.py                    # 서버 실행
```

**macOS·Linux**

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp app/.env.example app/.env
python app/main.py
```

브라우저에서 <http://127.0.0.1:8000/> 을 엽니다. 끝낼 때는 터미널에서 `Ctrl+C`를 누릅니다.
PowerShell에서 `Activate.ps1` 실행이 막히면 `Set-ExecutionPolicy -Scope Process RemoteSigned`를 먼저 실행합니다 (현재 창에만 적용).

## 배포

예제 저장소의 배포 절차를 팀 a8 기준으로 옮겼습니다. 비밀번호는 명령행·로그·채팅에 남기지 않습니다.

### 1. 배포 설정 (로컬)

```sh
cp deploy/.env.example deploy/.env
```

| 변수 | 값 |
|---|---|
| `USER_ID`, `PASSWORD` | 팀 SSH 계정과 비밀번호 (팀 채널에서 개별 전달) |
| `HOST` | `app.scnuoss.net` |
| `DEPLOY_ADDRESS` | `https://a8.scnuoss.net/` |
| `WORK_PATH` | `/home/a8/html/` |
| `INTERNAL_HOST`, `INTERNAL_PORT` | `127.0.0.1`, `3108` |

`WORK_PATH` 아래에 최상위 `requirements.txt`와 `app/`, `deploy/` 구조를 그대로 전송합니다.
로컬 가상환경, 로그, `.git`, 실제 환경 파일은 전송하지 않습니다. `deploy/.env`는 서버에 복사하지 않습니다.
서버의 `app/.env`에는 `INTERNAL_HOST`, `INTERNAL_PORT`와 앱 실행에 필요한 키만 적습니다.

### 2. 서버에서 실행

서버의 프로젝트 루트(`/home/a8/html`)에서 실행합니다. systemd 설정은 바꾸지 않습니다.

```sh
python3 -m venv .venv
.venv/bin/pip install -r deploy/requirements.txt
mkdir -p deploy/.run
chmod 700 deploy/.run
chmod 600 app/.env
.venv/bin/supervisord -c deploy/supervisord.conf
```

코드를 바꾼 뒤에는 아래 한 줄로 반영합니다. 최신 `main` 받기, 의존성 설치, 앱 재시작,
시민 화면(`app/static/index.html`)을 웹 루트 `index.html`로 복사, 코드·DB·`.git` 공개 차단, 상태 확인까지 차례로 합니다.

```sh
git pull --ff-only origin main && sh deploy/deploy.sh
```

대회 서버의 Nginx는 `/home/a8/html/`을 그대로 공개하고 `/api/`만 앱으로 넘깁니다.
그래서 첫 화면은 루트의 `index.html`이고, 나머지 폴더는 `chmod 700`으로 팀 계정만 읽게 둡니다.

수동으로 재시작할 때는 다음을 씁니다.

```sh
.venv/bin/supervisorctl -c deploy/supervisord.conf reread
.venv/bin/supervisorctl -c deploy/supervisord.conf update
.venv/bin/supervisorctl -c deploy/supervisord.conf restart wave-windpath
.venv/bin/supervisorctl -c deploy/supervisord.conf status
```

상태가 `RUNNING`인지, `https://a8.scnuoss.net/`이 HTTP 200을 돌려주는지 확인합니다.
로그는 `tail -f deploy/.run/app.log`로 봅니다.

### 3. 재부팅 후 자동 실행

`crontab -e`에 아래 한 줄을 추가합니다. 같은 항목이 있으면 교체하고 다른 항목은 그대로 둡니다.

```cron
@reboot cd /home/a8/html && /home/a8/html/.venv/bin/supervisord -c /home/a8/html/deploy/supervisord.conf >> /home/a8/html/deploy/.run/boot.log 2>&1
```

## 보안

- 비밀 값은 `.env`에만 둡니다. 저장소에는 값이 빈 `.env.example`만 있습니다.
- GitHub 비밀 스캔·푸시 차단, Dependabot 취약점 알림을 켜 두었습니다.
- `main` 브랜치는 보호되어 PR과 리뷰 1인 없이는 병합되지 않습니다.
- 공유 서버이므로 `app/.env`는 600, 운영 폴더는 700 권한으로 둡니다.
- 세부 규칙은 [AGENTS.md](AGENTS.md)의 보안 절을 따릅니다.

## 협업 규칙

- 작업은 Issue 단위로 나눕니다. `feature/기능명` 브랜치 → Pull Request → 1인 이상 리뷰 → 병합
- `main`에 직접 푸시하지 않습니다. PR 본문은 템플릿(무엇·왜·검증·영향)을 따릅니다.
- `.env`는 커밋하지 않습니다. API 키와 비밀번호는 채팅이나 AI 도구에 붙여 넣지 않습니다.

## 팀 Wave

| 이름 | 역할 |
|---|---|
| 류현우 (팀장) | PM, 데이터 수집, RE100·냉각 계산, 문서 |
| 문호영 | 바람길 AI 예측 모델, AI 브리핑(LLM) |
| 김현수 | 프론트엔드, UI/UX, 배포 |

## 일정

- 9/22: 주제·범위 확정 / 9/23 ~ 9/27: 개발·배포
- 9/27 22:00: 결과물 제출 / 9/30: 발표·심사

## 출처와 라이선스

- 코드: [MIT](LICENSE)
- 배포 구조, 배포 절차, 작업 규칙: 사전교육 3교시(서버 설정)에서 제공된 예제 [charsyam/scnu-oss-advanced-track-example](https://github.com/charsyam/scnu-oss-advanced-track-example) (MIT). 원본 저작권·라이선스 고지는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 있습니다.
- 데이터
  - 기상청 단기예보·초단기실황 조회서비스, 한국환경공단 에어코리아 대기오염정보 조회서비스: 공공데이터포털(data.go.kr) 오픈 API. 포털의 이용허락범위에 따라 출처를 표시합니다.
  - 모델 학습용 과거 기상: [Open-Meteo](https://open-meteo.com/) Historical Weather API (CC BY 4.0)
- 사용한 오픈소스

| 이름 | 용도 | 라이선스 |
|---|---|---|
| FastAPI | 웹 API | MIT |
| Uvicorn | ASGI 서버 | BSD-3-Clause |
| httpx | 공공 API 호출 | BSD-3-Clause |
| python-dotenv | `.env` 읽기 | BSD-3-Clause |
| Supervisor | 서버 프로세스 관리 | BSD 계열(Repoze) |
| scikit-learn, NumPy | 모델 비교 실험(`scripts/compare_models.py`)에만 사용, 서비스에는 미포함 | BSD-3-Clause |
