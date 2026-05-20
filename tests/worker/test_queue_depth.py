"""Tests for the queue-depth poller in worker/main.py.

The poller is what feeds the ``nats.queue.depth`` observable gauge — if
it stops updating, Mimir would see a flat line and we'd miss a NATS
backlog. These tests exercise it against a fake JetStream so we don't
need a real NATS broker.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from algorithms.base import AlgorithmInput, AlgorithmMetadata
from core import metrics as app_metrics
from worker import main as worker_main


class _Input(AlgorithmInput):
    """Dummy schema — the poller never touches it, but registry needs one."""


def _meta(name: str) -> AlgorithmMetadata:
    return AlgorithmMetadata(
        name=name,
        description="queue depth poller test fake",
        version="1.0",
        queue=f"optimce.allocation.{name}",
        input_schema=_Input,
    )


async def test_poller_writes_num_pending_into_snapshot(monkeypatch):
    """Single tick: fake js.consumer_info returns 42 → snapshot["fake"] == 42."""
    fake_registry = MagicMock()
    fake_registry.list_all.return_value = [_meta("fake")]
    monkeypatch.setattr(worker_main, "registry", fake_registry)
    monkeypatch.setattr(app_metrics, "queue_depth_snapshot", {})

    js = SimpleNamespace(consumer_info=AsyncMock(return_value=SimpleNamespace(num_pending=42)))
    shutdown = asyncio.Event()

    async def _stop_after_one_cycle():
        # Yield long enough for the poller to run one iteration before we
        # set shutdown. The poller uses asyncio.wait_for on the event with
        # the 15 s timeout, so setting the event short-circuits the wait.
        await asyncio.sleep(0)
        shutdown.set()

    await asyncio.gather(
        worker_main._poll_queue_depth(js, shutdown),
        _stop_after_one_cycle(),
    )

    assert app_metrics.queue_depth_snapshot["fake"] == 42
    js.consumer_info.assert_awaited_with("ALGORITHMS", "worker-fake")


async def test_poller_skips_algorithms_whose_consumer_info_raises(monkeypatch):
    """A flaky consumer must not crash the poller or stop other updates."""
    fake_registry = MagicMock()
    fake_registry.list_all.return_value = [_meta("good"), _meta("bad")]
    monkeypatch.setattr(worker_main, "registry", fake_registry)
    monkeypatch.setattr(app_metrics, "queue_depth_snapshot", {})

    async def _consumer_info(stream, consumer):
        if "bad" in consumer:
            raise RuntimeError("transient nats error")
        return SimpleNamespace(num_pending=7)

    js = SimpleNamespace(consumer_info=_consumer_info)
    shutdown = asyncio.Event()

    async def _stop_after_one_cycle():
        await asyncio.sleep(0)
        shutdown.set()

    await asyncio.gather(
        worker_main._poll_queue_depth(js, shutdown),
        _stop_after_one_cycle(),
    )

    assert app_metrics.queue_depth_snapshot.get("good") == 7
    assert "bad" not in app_metrics.queue_depth_snapshot
