# 저장소 작업 규칙

팀원과 AI 코딩 도구(Codex, Claude Code 등)가 함께 따르는 규칙이다. 보안·배포 규칙은 강사 예제 저장소 [charsyam/scnu-oss-advanced-track-example](https://github.com/charsyam/scnu-oss-advanced-track-example) (MIT)를 따른다.

## 보안

- 실제 환경 설정 파일(`.env`, `.env.*`, `*.env`, `env` 등)은 절대로 Git에 스테이징하거나 커밋하지 않는다.
- `git add -f` 등으로 환경 설정 파일의 ignore 규칙을 우회하지 않는다.
- 커밋 전 스테이징된 파일 목록을 확인하여 환경 설정 파일이 포함되지 않았는지 검증한다.
- 공유용 `.env.example`만 예외로 허용하며, 비밀 정보나 실제 운영 설정을 포함하지 않고 안전한 예시 값만 기록한다.
- API 키(공공데이터포털, 기상청, LLM, SmartThings)는 `app/.env`에만 둔다. 코드, 문서, 로그, 커밋 메시지, AI 프롬프트에 키 원문을 넣지 않는다.
- 사용자 위치 등 개인정보는 서버에 저장하지 않는다.
- LLM API 키는 서버에서만 사용한다. 프론트엔드 JavaScript와 빌드 결과물에 키를 넣지 않는다.
- LLM 출력과 사용자 입력은 HTML에 넣기 전에 이스케이프한다(XSS 방지).
- LLM 호출 API에는 요청 횟수 제한과 캐시를 둔다(요금 폭증 방지).
- 정적 파일은 프론트엔드 빌드 폴더만 공개한다. 프로젝트 루트, `.git`, `.env`를 공개 경로에 두지 않는다.
- 오류 응답은 일반화하고, 로그에 키·토큰을 남기지 않는다.
- 인터넷에서 받은 설치 스크립트를 내용 확인 없이 실행하지 않는다.

## 구조

- 서버 코드와 앱 설정은 `app/`, 배포 및 운영 설정은 `deploy/`에 둔다. 앱 실행 설정은 `app/.env`, 서버 접속 및 배포 설정은 `deploy/.env`로 분리한다.
- 서버 주소와 포트의 환경 변수 이름은 `INTERNAL_HOST`, `INTERNAL_PORT`를 사용한다.
- 앱 의존성은 최상위 `requirements.txt`에서 관리한다. `deploy/requirements.txt`는 `-r ../requirements.txt`로 앱 의존성을 포함하고 운영 의존성만 추가한다. 배포 시 최상위 `requirements.txt`도 `WORK_PATH`에 전송한다.
- 프론트엔드는 빌드한 정적 파일을 FastAPI가 서빙해 포트 하나로 운영한다.
- 수집한 원본 데이터 캐시는 `data/`에 두고 Git에 커밋하지 않는다.
- 대회 서버는 팀별 메모리 1.2GB, 프로세스 2개 한도다. 프로세스는 Supervisor와 앱(uvicorn 워커 1개)만 두고, 데이터 수집은 앱 안의 스케줄러로 돌린다. LLM은 외부 API로 호출하고 서버에서 모델을 학습하지 않는다.

## 협업

- `main`에 직접 커밋하지 않는다. `feature/기능명` 브랜치에서 작업하고 Pull Request로 병합한다.
- PR은 목적 하나, 가능한 작게 만든다. 본문은 `.github/PULL_REQUEST_TEMPLATE.md`를 따른다.
- 문서와 주석은 한국어로 쓴다.

## 배포

- 배포 시 systemd 설정이나 서비스를 변경하지 않는다. 프로세스 관리는 사용자 권한으로 실행하는 Supervisor를 사용한다.
- 배포 전 `README.md`의 배포 및 운영 절차를 확인한다. 로컬 `deploy/.env`의 `HOST`, `USER_ID`, `PASSWORD`, `WORK_PATH`, `DEPLOY_ADDRESS`, `INTERNAL_HOST`, `INTERNAL_PORT`를 사용하되 비밀번호를 로그나 명령행에 노출하지 않는다.
- 서버의 `WORK_PATH`를 프로젝트 루트로 삼아 `app/`, `deploy/` 구조를 유지한다. `deploy/.env`는 서버로 복사하지 않고, 서버의 `app/.env`에는 `INTERNAL_HOST`, `INTERNAL_PORT`와 앱 실행에 필요한 키만 기록하며 권한을 `600`으로 설정한다.
- 가상환경은 프로젝트 루트의 `.venv/`에 생성하고 `deploy/requirements.txt`로 앱과 운영 의존성을 설치한다. 운영 로그와 소켓은 `deploy/.run/`에 저장하고 디렉터리 권한을 `700`으로 설정한다.
- 배포 시 앱을 반드시 `deploy/supervisord.conf`에 프로그램으로 등록하고, 사용자 권한의 `supervisord`로 실행한다. 자동 시작(`autostart=true`)과 자동 재시작(`autorestart=true`)을 설정한다.
- 재부팅 후 실행은 사용자 crontab의 `@reboot`로 등록한다. 기존 항목을 확인하여 중복 등록을 피하고, 폴더 구조 변경 시 기존 Supervisor 실행과 cron 경로도 함께 갱신한다. 다른 서비스의 cron 항목은 보존한다.
- 배포 후 `supervisorctl -c deploy/supervisord.conf status`로 `RUNNING` 상태를 확인하고, `DEPLOY_ADDRESS`에서 HTTP 200 및 기대한 HTML 응답을 검증한다. 재시작이나 재부팅을 실제로 검증했는지 구분하여 보고한다.
