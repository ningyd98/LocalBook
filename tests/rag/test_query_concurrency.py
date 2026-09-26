"""RAG retrieval must not stall other requests on the async worker."""

from __future__ import annotations

import asyncio
import threading

from server.rag.retrieval.hybrid import HybridSearchOutcome
from server.rag.schemas import RetrievalStats
from server.rag.service import RagService


def test_query_keeps_event_loop_responsive_during_retrieval() -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingRetriever:
        reranker_enabled = False

        def search(self, *args, **kwargs):
            started.set()
            release.wait(timeout=1)
            return HybridSearchOutcome(results=[], stats=RetrievalStats())

    class EnabledIndex:
        enabled = True

    service = RagService(index=EnabledIndex(), retriever=BlockingRetriever())

    async def run() -> None:
        task = asyncio.create_task(service.query("test question"))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 1.5)
            assert not task.done(), "synchronous retrieval blocked the event loop"
        finally:
            release.set()
        await task

    asyncio.run(run())
