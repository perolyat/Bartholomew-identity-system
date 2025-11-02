from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._topics: defaultdict[str, asyncio.Queue] = defaultdict(asyncio.Queue)

    async def publish(self, topic: str, event: dict[str, Any]) -> None:
        await self._topics[topic].put(event)

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        q = self._topics[topic]
        while True:
            yield await q.get()
