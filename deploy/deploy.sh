#!/bin/sh
# 서버의 프로젝트 루트(/home/a8/html)에서 실행한다.
#   최신 main 반영 → 의존성 설치 → 앱 재시작 → 첫 화면 교체 → 웹 루트 노출 차단 → 상태 확인
# 사용법: cd /home/a8/html && git pull --ff-only origin main && sh deploy/deploy.sh
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
CONF=deploy/supervisord.conf
CTL=".venv/bin/supervisorctl -c $CONF"
SITE=https://a8.scnuoss.net

echo "[1/5] 최신 main 받기"
git pull --ff-only origin main

echo "[2/5] 의존성 설치"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install -q -r deploy/requirements.txt

echo "[3/5] 앱 재시작"
mkdir -p deploy/.run
if $CTL pid >/dev/null 2>&1; then
    $CTL reread >/dev/null
    $CTL update >/dev/null
    $CTL restart wave-windpath
else
    .venv/bin/supervisord -c "$CONF"
fi

# 서버가 재부팅돼도 앱이 다시 뜨도록 @reboot 한 줄을 둔다(같은 항목은 교체, 다른 항목은 그대로).
BOOT="@reboot cd $ROOT && $ROOT/.venv/bin/supervisord -c $ROOT/$CONF >> $ROOT/deploy/.run/boot.log 2>&1"
( crontab -l 2>/dev/null | grep -v "deploy/supervisord.conf"; echo "$BOOT" ) | crontab - \
    || echo "crontab 등록 실패: README '재부팅 후 자동 실행'을 따라 직접 추가하세요."

echo "[4/5] 첫 화면 교체와 웹 루트 노출 차단"
# Nginx가 이 폴더를 그대로 공개한다. 첫 화면(index.html)만 루트에 두고
# 코드·DB·가상환경·.git은 팀 계정만 읽을 수 있게 막는다.
cp app/static/index.html index.html
chmod 644 index.html
for d in .git .venv app data deploy docs scripts tests; do
    if [ -e "$d" ]; then
        chmod 700 "$d"
    fi
done
if [ -f app/.env ]; then
    chmod 600 app/.env
fi

echo "[5/5] 상태 확인"
$CTL status wave-windpath || true
i=0
until curl -fsS "$SITE/api/health"; do
    i=$((i + 1))
    if [ "$i" -ge 10 ]; then
        echo "앱이 응답하지 않습니다. tail -n 50 deploy/.run/app.log 로 원인을 확인하세요."
        exit 1
    fi
    sleep 3
done
echo
curl -sS -o /dev/null -w "시민 예보 API: HTTP %{http_code}\n" "$SITE/api/citizen/forecast?location=suncheon&hours=12"
curl -sS -o /dev/null -w "노출 차단 확인(.git): HTTP %{http_code} (403·404면 정상)\n" "$SITE/.git/HEAD"
