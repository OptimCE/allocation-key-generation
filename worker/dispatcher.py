"""Per-algorithm NATS subscription + message handler.

For each registered algorithm, ``subscribe_algorithm`` creates a durable
push subscription on ``meta.queue`` (e.g. ``optimce.allocation.olagsa``)
within a queue group of the same name. Multiple worker replicas in the
same queue group will share work via JetStream.

Message handling follows a strict failure-class matrix:

* **Deterministic failures** (bad URL, bad file, bad inputs, algorithm
  exception) — mark the generation row FAILED with an error message
  and ack the message. We never want JetStream to redeliver a poison
  pill that will fail the same way every time.
* **Transient failures** (network timeout, 5xx from storage, DB
  connection lost mid-persist) — nak the message so JetStream
  redelivers. We do not touch the row's status; the next delivery will
  retry from scratch.

The dispatcher never holds a DB session while running an algorithm —
each step opens its own short-lived session, matching the architecture
agreed in the plan.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Awaitable, Callable

import httpx
from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import ConsumerConfig
from pydantic import ValidationError

from algorithms.base import AlgorithmMetadata
from algorithms.registry import registry
from core import metrics as app_metrics
from core.database.database import AsyncSessionLocalFactory
from core.queue.helper import Event
from shared import data_loading
from shared.const import GenerationStatus
from shared.models.local_models import GenerationModel
from worker import http_client as http_client_module
from worker import persistence

logger = logging.getLogger(__name__)

# Long enough to cover any realistic OLAGSA-style optimisation. JetStream
# will not redeliver until ack_wait elapses, so this also caps how long a
# silent crash can stall the queue.
_ACK_WAIT_SECONDS = 10 * 60

# How long JetStream waits before redelivering after a NAK. Short enough
# to retry transient failures quickly, long enough to not hot-loop on a
# flaky downstream.
_NAK_RETRY_DELAY_SECONDS = 30


@dataclasses.dataclass(frozen=True)
class _GenerationSnapshot:
    """Per-message snapshot of the row, captured before the session closes."""

    id: int
    file_url: str
    file_name: str
    injection_name: str
    inputs: dict
    id_community: int
    status: int


async def subscribe_algorithm(
    js: JetStreamContext,
    meta: AlgorithmMetadata,
    http: httpx.AsyncClient,
):
    """Subscribe the worker to a single algorithm's NATS subject.

    Returns the subscription so the caller can drain it on shutdown.
    """
    handler = _make_handler(meta, http)
    sub = await js.subscribe(
        subject=meta.queue,
        durable=f"worker-{meta.name}",
        queue=f"worker-{meta.name}",
        manual_ack=True,
        cb=handler,
        config=ConsumerConfig(ack_wait=_ACK_WAIT_SECONDS),
    )
    logger.info("Subscribed to %s (durable=worker-%s)", meta.queue, meta.name)
    return sub


def _make_handler(
    meta: AlgorithmMetadata, http: httpx.AsyncClient
) -> Callable[[Msg], Awaitable[None]]:
    """Build the per-message callback for ``meta``.

    Closes over ``meta`` and ``http`` so each subscription has its own
    pre-bound handler with no global lookups on the hot path.
    """

    async def handle(msg: Msg) -> None:
        try:
            event = Event.decode(msg.data)
        except Exception:
            logger.exception(
                "Failed to decode event on %s; acking and dropping", meta.queue
            )
            await msg.ack()
            app_metrics.worker_messages.add(
                1, {"algorithm": meta.name, "outcome": "drop_decode"}
            )
            return

        generation_id = (
            event.data.get("generation_id") if isinstance(event.data, dict) else None
        )
        if not isinstance(generation_id, int):
            logger.error(
                "Event on %s has missing/invalid generation_id: %r",
                meta.queue,
                event.data,
            )
            await msg.ack()
            app_metrics.worker_messages.add(
                1, {"algorithm": meta.name, "outcome": "drop_invalid_id"}
            )
            return

        try:
            await _process(meta, http, generation_id, msg)
        except _TransientError as exc:
            logger.warning(
                "Transient failure for generation %d, will redeliver: %s",
                generation_id,
                exc,
            )
            await msg.nak(delay=_NAK_RETRY_DELAY_SECONDS)
            app_metrics.worker_messages.add(
                1, {"algorithm": meta.name, "outcome": "nak"}
            )
        except Exception:
            # Catch-all so the subscription never dies. We treat unexpected
            # errors as deterministic — better to mark the row FAILED with
            # the traceback context than to redeliver indefinitely.
            logger.exception("Unhandled error processing generation %d", generation_id)
            await persistence.save_failure(generation_id, "unhandled_worker_error")
            await msg.ack()
            app_metrics.worker_messages.add(
                1, {"algorithm": meta.name, "outcome": "ack_unhandled"}
            )

    return handle


class _TransientError(Exception):
    """Raised inside ``_process`` to signal the message should be NAK'd."""


async def _process(
    meta: AlgorithmMetadata,
    http: httpx.AsyncClient,
    generation_id: int,
    msg: Msg,
) -> None:
    """The pipeline for a single generation message.

    Raises ``_TransientError`` for failures that should be redelivered.
    Marks the row FAILED + acks the message for deterministic failures.
    Acks the message and returns on success.
    """
    # ---- Step 1: snapshot the row, then close the session --------------
    snapshot = await _snapshot_generation(generation_id)
    if snapshot is None:
        logger.warning("Generation %d not found in DB; acking message", generation_id)
        await msg.ack()
        return

    if snapshot.status != GenerationStatus.PENDING:
        # Likely a redelivery after we successfully persisted but failed
        # to ack. Nothing to do; just ack and move on.
        logger.info(
            "Generation %d already in status %d; acking redelivery",
            generation_id,
            snapshot.status,
        )
        await msg.ack()
        return

    # ---- Step 2: download the source file ------------------------------
    try:
        content = await http_client_module.download(http, snapshot.file_url)
    except httpx.HTTPStatusError as exc:
        if 500 <= exc.response.status_code < 600:
            raise _TransientError(f"download {exc.response.status_code}") from exc
        await persistence.save_failure(
            generation_id, f"download_failed: HTTP {exc.response.status_code}"
        )
        await msg.ack()
        return
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise _TransientError(f"download transport: {exc}") from exc

    # ---- Step 3: parse the file into the algorithm raw-data triple -----
    try:
        raw_data = data_loading.load(
            content, snapshot.file_name, snapshot.injection_name
        )
    except (
        data_loading.InvalidInjectionColumnError,
        data_loading.UnsupportedFileFormatError,
    ) as exc:
        await persistence.save_failure(generation_id, f"parse_failed: {exc}")
        await msg.ack()
        return
    except Exception as exc:
        await persistence.save_failure(generation_id, f"parse_failed_unexpected: {exc}")
        await msg.ack()
        return

    # ---- Step 4: re-validate inputs against the algorithm schema -------
    # Defensive: the API validates these on creation, but a row written
    # directly to the DB (or one created against an older algorithm
    # version) could have stale shapes.
    try:
        algo_input = meta.input_schema.model_validate(snapshot.inputs)
    except ValidationError as exc:
        await persistence.save_failure(
            generation_id, f"invalid_inputs: {exc.errors()[:5]}"
        )
        await msg.ack()
        return

    # ---- Step 5: run the algorithm (no DB session held) ----------------
    try:
        impl_cls = registry.implementation(meta.name)
    except KeyError:
        # Should be impossible — we only subscribe to algorithms whose
        # implementation was loaded by autodiscover. Defensive guard.
        await persistence.save_failure(
            generation_id, f"implementation_missing: {meta.name}"
        )
        await msg.ack()
        return

    start = time.perf_counter()
    try:
        result = await impl_cls().run(algo_input, raw_data)
    except Exception as exc:
        # Record duration even for failures — the time-to-failure is a
        # useful signal for slow-failing algorithms (e.g. solver timeout).
        app_metrics.generation_duration.record(
            time.perf_counter() - start,
            {"algorithm": meta.name, "status": "failed"},
        )
        # Algorithm exceptions are deterministic by definition: the same
        # inputs will fail the same way on redelivery. Ack and move on.
        logger.exception(
            "Algorithm %s raised for generation %d", meta.name, generation_id
        )
        await persistence.save_failure(generation_id, f"algorithm_failed: {exc}")
        await msg.ack()
        return
    app_metrics.generation_duration.record(
        time.perf_counter() - start,
        {"algorithm": meta.name, "status": "success"},
    )

    # ---- Step 6: persist the result ------------------------------------
    try:
        await persistence.save_success(generation_id, result)
    except Exception as exc:
        # DB-level failures during persist are most likely transient
        # (connection drop, deadlock). Let JetStream redeliver so the
        # next attempt has a fresh session.
        raise _TransientError(f"persist failed: {exc}") from exc

    await msg.ack()
    logger.info("Generation %d processed successfully", generation_id)


async def _snapshot_generation(generation_id: int) -> _GenerationSnapshot | None:
    """Read the row in a short-lived session and return a frozen snapshot.

    The session is closed before the algorithm runs, per the architecture
    agreed in the plan: no DB connection is held during heavy compute.
    """
    async with AsyncSessionLocalFactory() as session:
        row = await session.get(GenerationModel, generation_id)
        if row is None:
            return None
        return _GenerationSnapshot(
            id=row.id,
            file_url=row.file_url,
            file_name=row.file_name,
            injection_name=row.injection_name,
            inputs=dict(row.inputs) if row.inputs else {},
            id_community=row.id_community,
            status=int(row.status),
        )
