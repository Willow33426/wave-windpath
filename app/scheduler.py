"""앱 프로세스 안에서 도는 수집 스케줄러.

대회 서버는 팀별 프로세스 2개(Supervisor + 앱) 한도라 cron 대신 앱 안에서 돌린다.
"""
from __future__ import annotations

import asyncio
import logging

from app.collector import collect_once
from app.config import Settings

logger = logging.getLogger(__name__)


async def run_collector_loop(settings: Settings, conn) -> None:
    """주기적으로 수집한다. 한 번 실패해도 루프는 멈추지 않는다."""
    interval = max(5, settings.collect_interval_minutes) * 60
    while True:
        try:
            await asyncio.to_thread(collect_once, settings, conn)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 수집 실패로 서버가 죽으면 안 된다
            logger.exception("수집 주기 실행 실패")
        await asyncio.sleep(interval)
