"""Per-algorithm NATS subscription + message handler.

For each registered algorithm, ``subscribe_algorithm`` creates a durable
push subscription on ``meta.queue`` (e.g. ``optimce.allocation.olagsa``)
within a queue group of the same name. Multiple worker replicas in the
same queue group will share work via JetStream.

Message handling follows a strict failure-class matrix:

* **Deterministic failures** (object missing, bad file, bad inputs,
  algorithm exception) — mark the generation row FAILED with an error
  message and ack the message. We never want JetStream to redeliver a
  poison pill that will fail the same way every time.
* **Transient failures** (storage 5xx/timeout, DB connection lost
  mid-persist) — nak the message so JetStream redelivers. We do not
  touch the row's status; the next delivery will retry from scratch.

The dispatcher never holds a DB session while running an algorithm —
each step opens its own short-lived session, matching the architecture
agreed in the plan.

File-storage cleanup contract
-----------------------------
The service uploads the source file to MinIO at creation time. The
worker owns the file from that point on and is responsible for deleting
it once the row reaches a terminal status. Cleanup happens **only** on
terminal outcomes (SUCCESS, deterministic FAILURE) — never on transient
NAKs, because the next delivery still needs to read the file. The single
cleanup point is the ``handle`` wrapper's call to ``_delete_safely``
after a successful ``_process`` return.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Awaitable, Callable

from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import ConsumerConfig
from pydantic import ValidationError

from algorithms.base import AlgorithmMetadata
from algorithms.registry import registry
from core import metrics as app_metrics
from core import storage
from core.database.database import AsyncSessionLocalFactory
from core.queue.helper import Event
from shared import data_loading
from shared.const import GenerationStatus
from shared.models.local_models import GenerationModel
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
    file_storage_key: str
    file_name: str
    injection_name: str
    inputs: dict
    id_community: int
    status: int


@dataclasses.dataclass(frozen=True)
class _Terminal:
    """Returned by ``_process`` whenever it ends in a terminal state.

    The handler reads ``storage_key`` (when not None) and best-effort
    deletes the file. ``_TransientError`` is raised instead of returned
    when the message must be redelivered — in that case no file deletion
    happens, since the next attempt still needs to read it.
    """

    storage_key: str | None


async def subscribe_algorithm(
    js: JetStreamContext,
    meta: AlgorithmMetadata,
):
    """Subscribe the worker to a single algorithm's NATS subject.

    Returns the subscription so the caller can drain it on shutdown.
    """
    handler = _make_handler(meta)
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


def _make_handler(meta: AlgorithmMetadata) -> Callable[[Msg], Awaitable[None]]:
    """Build the per-message callback for ``meta``.

    Closes over ``meta`` so each subscription has its own pre-bound
    handler with no global lookups on the hot path.
    """

    async def handle(msg: Msg) -> None:
        try:
            event = Event.decode(msg.data)
        except Exception:
            logger.exception("Failed to decode event on %s; acking and dropping", meta.queue)
            await msg.ack()
            app_metrics.worker_messages.add(1, {"algorithm": meta.name, "outcome": "drop_decode"})
            return

        generation_id = event.data.get("generation_id") if isinstance(event.data, dict) else None
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
            outcome = await _process(meta, generation_id, msg)
        except _TransientError as exc:
            logger.warning(
                "Transient failure for generation %d, will redeliver: %s",
                generation_id,
                exc,
            )
            await msg.nak(delay=_NAK_RETRY_DELAY_SECONDS)
            app_metrics.worker_messages.add(1, {"algorithm": meta.name, "outcome": "nak"})
            return
        except Exception:
            # Catch-all so the subscription never dies. We treat unexpected
            # errors as deterministic — better to mark the row FAILED with
            # the traceback context than to redeliver indefinitely.
            logger.exception("Unhandled error processing generation %d", generation_id)
            await persistence.save_failure(generation_id, "unhandled_worker_error")
            await msg.ack()
            app_metrics.worker_messages.add(1, {"algorithm": meta.name, "outcome": "ack_unhandled"})
            return

        await msg.ack()
        if outcome.storage_key is not None:
            await _delete_safely(outcome.storage_key)

    return handle


class _TransientError(Exception):
    """Raised inside ``_process`` to signal the message should be NAK'd.

    The cleanup contract relies on this being raised (not returned): when
    the handler catches it, the file is left in place so the redelivered
    message can still read it.
    """


async def _process(
    meta: AlgorithmMetadata,
    generation_id: int,
    msg: Msg,
) -> _Terminal:
    """The pipeline for a single generation message.

    Returns ``_Terminal`` for terminal outcomes (the handler then acks +
    deletes). Raises ``_TransientError`` for failures that should be
    redelivered (no file deletion).
    """
    # ---- Step 1: snapshot the row, then close the session --------------
    snapshot = await _snapshot_generation(generation_id)
    if snapshot is None:
        logger.warning("Generation %d not found in DB; acking message", generation_id)
        return _Terminal(storage_key=None)

    if snapshot.status != GenerationStatus.PENDING:
        # Likely a redelivery after we successfully persisted but failed
        # to ack. The first delivery's cleanup may already have deleted
        # the file; ask for delete anyway — storage.delete is idempotent.
        logger.info(
            "Generation %d already in status %d; acking redelivery",
            generation_id,
            snapshot.status,
        )
        return _Terminal(storage_key=snapshot.file_storage_key)

    # ---- Step 2: download the source file ------------------------------
    try:
        content = await storage.download(snapshot.file_storage_key)
    except storage.ObjectNotFound:
        # The object is gone — possibly a previous run cleaned it up but
        # the row was somehow still PENDING (admin intervention, partial
        # crash). Terminal: mark FAILED and ack. Nothing left to delete.
        await persistence.save_failure(generation_id, "storage_object_missing")
        return _Terminal(storage_key=None)
    except storage.TransientStorageError as exc:
        raise _TransientError(f"storage download: {exc}") from exc

    # ---- Step 3: parse the file into the algorithm raw-data triple -----
    try:
        raw_data = data_loading.load(content, snapshot.file_name, snapshot.injection_name)
    except (
        data_loading.InvalidInjectionColumnError,
        data_loading.UnsupportedFileFormatError,
    ) as exc:
        await persistence.save_failure(generation_id, f"parse_failed: {exc}")
        return _Terminal(storage_key=snapshot.file_storage_key)
    except Exception as exc:
        await persistence.save_failure(generation_id, f"parse_failed_unexpected: {exc}")
        return _Terminal(storage_key=snapshot.file_storage_key)

    # ---- Step 4: re-validate inputs against the algorithm schema -------
    # Defensive: the API validates these on creation, but a row written
    # directly to the DB (or one created against an older algorithm
    # version) could have stale shapes.
    try:
        algo_input = meta.input_schema.model_validate(snapshot.inputs)
    except ValidationError as exc:
        await persistence.save_failure(generation_id, f"invalid_inputs: {exc.errors()[:5]}")
        return _Terminal(storage_key=snapshot.file_storage_key)

    # ---- Step 5: run the algorithm (no DB session held) ----------------
    try:
        impl_cls = registry.implementation(meta.name)
    except KeyError:
        # Should be impossible — we only subscribe to algorithms whose
        # implementation was loaded by autodiscover. Defensive guard.
        await persistence.save_failure(generation_id, f"implementation_missing: {meta.name}")
        return _Terminal(storage_key=snapshot.file_storage_key)

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
        logger.exception("Algorithm %s raised for generation %d", meta.name, generation_id)
        await persistence.save_failure(generation_id, f"algorithm_failed: {exc}")
        return _Terminal(storage_key=snapshot.file_storage_key)
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
        # next attempt has a fresh session. Do NOT delete the file here.
        raise _TransientError(f"persist failed: {exc}") from exc

    logger.info("Generation %d processed successfully", generation_id)
    return _Terminal(storage_key=snapshot.file_storage_key)


async def _delete_safely(key: str) -> None:
    """Best-effort delete; ``storage.delete`` already swallows its own errors.

    Wrapped so future instrumentation (e.g. counting leaked objects) has a
    single place to hook in.
    """
    await storage.delete(key)


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
            file_storage_key=row.file_storage_key,
            file_name=row.file_name,
            injection_name=row.injection_name,
            inputs=dict(row.inputs) if row.inputs else {},
            id_community=row.id_community,
            status=int(row.status),
        )
