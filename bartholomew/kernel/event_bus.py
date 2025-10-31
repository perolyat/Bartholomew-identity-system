from __future__ import annotations
import asyncio
from typing import Any, AsyncIterator, Dict, DefaultDict
from collections import defaultdict


class EventBus:
    def __init__(self) -> None:
        self._topics: DefaultDict[str, asyncio.Queue] = defaultdict(
            asyncio.Queue
        )

    async def publish(self, topic: str, event: Dict[str, Any]) -> None:
        await self._topics[topic].put(event)

    async def subscribe(self, topic: str) -> AsyncIterator[Dict[str, Any]]:
        q = self._topics[topic]
        while True:
            yield await q.get()
