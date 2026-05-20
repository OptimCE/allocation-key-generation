"""Worker process entry point.

Bootstraps logging + tracing, loads algorithm implementations via the
shared registry, connects to NATS JetStream, and subscribes the dispatcher
to one consumer per discovered algorithm. Runs until SIGINT/SIGTERM, then
drains subscriptions and disposes resources cleanly.

Run locally:

    python -m worker.main
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from algorithms import autodiscover
from algorithms.registry import registry
from core import metrics as app_metrics
from core.database.database import crm_engine, local_engine
from core.logging import configure_logging
from core.queue.init import close_nats, get_jetstream, init_nats
from core.tracing import setup_tracer_provider
from worker import dispatcher

logger = logging.getLogger(__name__)

# Bounded retry on NATS connect so a slow-to-start broker doesn't crash
# the worker container immediately. Exponential backoff capped at 30 s
# means the worker will try for ~10 minutes before giving up and letting
# the orchestrator recreate it from scratch.
_NATS_CONNECT_MAX_ATTEMPTS = 10
_NATS_CONNECT_BASE_DELAY_SECONDS = 2
_NATS_CONNECT_MAX_DELAY_SECONDS = 30

# How often the queue depth poller refreshes nats.queue.depth. Matches the
# MeterProvider's export_interval_millis in core/tracing.py so each export
# cycle sees a fresh value.
_QUEUE_DEPTH_POLL_INTERVAL_SECONDS = 15

# All algorithm subjects (optimce.allocation.*) live on this single stream
# per core/queue/streams.json. If a future algorithm uses a different
# stream, extend AlgorithmMetadata with a stream name and read it here.
_ALGORITHM_STREAM_NAME = "ALGORITHMS"


async def _connect_nats_with_retry() -> None:
    for attempt in range(1, _NATS_CONNECT_MAX_ATTEMPTS + 1):
        try:
            await init_nats()
            return
        except Exception as exc:
            if attempt == _NATS_CONNECT_MAX_ATTEMPTS:
                logger.error(
                    "NATS connect attempt %d/%d failed: %s; aborting worker startup",
                    attempt,
                    _NATS_CONNECT_MAX_ATTEMPTS,
                    exc,
                )
                raise
            delay = min(
                _NATS_CONNECT_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                _NATS_CONNECT_MAX_DELAY_SECONDS,
            )
            logger.warning(
                "NATS connect attempt %d/%d failed: %s; retrying in %.1fs",
                attempt,
                _NATS_CONNECT_MAX_ATTEMPTS,
                exc,
                delay,
            )
            await asyncio.sleep(delay)


async def _poll_queue_depth(js, shutdown_event: asyncio.Event) -> None:
    """Refresh app_metrics.queue_depth_snapshot every poll interval.

    Reads JetStream consumer_info for each registered algorithm and stores
    the per-algorithm num_pending in the snapshot dict. The observable
    gauge in core.metrics reads from that dict on every export cycle.

    Best-effort: per-algorithm failures are logged at DEBUG and skipped.
    The loop only exits when shutdown_event is set, so a flaky consumer
    info call doesn't tear down the worker.
    """
    while not shutdown_event.is_set():
        for meta in registry.list_all():
            try:
                info = await js.consumer_info(_ALGORITHM_STREAM_NAME, f"worker-{meta.name}")
                app_metrics.queue_depth_snapshot[meta.name] = int(info.num_pending)
            except Exception as exc:
                logger.debug("queue depth poll failed for %s: %s", meta.name, exc)
        try:
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=_QUEUE_DEPTH_POLL_INTERVAL_SECONDS
            )
        except TimeoutError:
            continue


async def main() -> None:
    configure_logging()
    setup_tracer_provider()

    # Heavy import path: pulls numpy/cvxpy/pandas via each algorithm's
    # algorithm.py module, and registers implementation classes.
    autodiscover(load_implementations=True)
    logger.info(
        "Discovered %d algorithm(s): %s",
        len(registry.list_all()),
        [m.name for m in registry.list_all()],
    )

    await _connect_nats_with_retry()
    js = get_jetstream()

    shutdown_event = asyncio.Event()
    _install_signal_handlers(shutdown_event)

    subscriptions = []
    queue_depth_task: asyncio.Task | None = None
    try:
        for meta in registry.list_all():
            sub = await dispatcher.subscribe_algorithm(js, meta)
            subscriptions.append(sub)

        queue_depth_task = asyncio.create_task(
            _poll_queue_depth(js, shutdown_event), name="queue-depth-poller"
        )

        logger.info("Worker ready — listening on %d algorithm queue(s)", len(subscriptions))
        await shutdown_event.wait()
        logger.info("Shutdown signal received; draining subscriptions...")
    finally:
        # Stop the queue-depth poller before draining subscriptions so it
        # doesn't race against a closing JetStream connection.
        if queue_depth_task is not None:
            queue_depth_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await queue_depth_task

        # Drain in best-effort order. Each step is wrapped because we want
        # later steps to run even if an earlier one fails (e.g. NATS
        # already disconnected).
        for sub in subscriptions:
            try:
                await sub.drain()
            except Exception:
                logger.exception("Error draining subscription")

        try:
            await close_nats()
        except Exception:
            logger.exception("Error closing NATS connection")

        try:
            await local_engine.dispose()
        except Exception:
            logger.exception("Error disposing local DB engine")

        try:
            await crm_engine.dispose()
        except Exception:
            logger.exception("Error disposing CRM DB engine")

        logger.info("Worker shutdown complete")


def _install_signal_handlers(shutdown_event: asyncio.Event) -> None:
    """Wire SIGINT/SIGTERM to set ``shutdown_event``.

    ``loop.add_signal_handler`` is the right call on POSIX, but it raises
    ``NotImplementedError`` on Windows — fall back to ``signal.signal``
    there. Either way, the goal is the same: a single Ctrl+C / SIGTERM
    triggers the graceful shutdown path in ``main``.
    """
    loop = asyncio.get_running_loop()

    def _set_event() -> None:
        if not shutdown_event.is_set():
            shutdown_event.set()

    if sys.platform == "win32":
        # asyncio's add_signal_handler is unsupported on Windows.
        signal.signal(signal.SIGINT, lambda *_: _set_event())
        signal.signal(signal.SIGTERM, lambda *_: _set_event())
        return

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _set_event)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _set_event())


if __name__ == "__main__":
    asyncio.run(main())
