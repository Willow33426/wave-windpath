"""같은 입력으로 계산을 반복하지 않도록 짧게 응답을 재사용한다.

수집은 30분 주기라 시민 예보는 그사이 거의 바뀌지 않는다. 발표장처럼 사용자가 한꺼번에
몰릴 때 요청마다 다시 계산하면(1회 수십 ms) 단일 워커가 줄을 서게 되므로,
`version`(마지막 수집 시각)이 같고 `ttl_sec`이 지나지 않았으면 이전 결과를 돌려준다.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Hashable


class ResponseCache:
    def __init__(self, ttl_sec: float, clock: Callable[[], float] = time.monotonic):
        self.ttl_sec = ttl_sec
        self._clock = clock
        self._items: dict[Hashable, tuple[float, Any, Any]] = {}

    def get(self, key: Hashable, version: Any) -> Any | None:
        item = self._items.get(key)
        if item is None:
            return None
        saved_at, saved_version, value = item
        if saved_version != version or self._clock() - saved_at >= self.ttl_sec:
            return None
        return value

    def put(self, key: Hashable, version: Any, value: Any) -> None:
        self._items[key] = (self._clock(), version, value)
