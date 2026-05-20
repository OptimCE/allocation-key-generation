"""Failure-path tests for the worker dispatcher.

The dispatcher classifies failures into two buckets:

* **Deterministic** (algorithm raises, malformed event, bad inputs,
  missing implementation, missing storage object) → mark the row FAILED +
  ack the message so JetStream does not redeliver a poison pill.
* **Transient** (storage 5xx, DB connection drop during persist) → nak
  the message with a delay so JetStream retries on a fresh delivery.

These tests invoke the per-message handler returned by ``_make_handler``
directly with a ``FakeMsg``, so no live NATS connection or real Postgres
session is required. External collaborators (``_snapshot_generation``,
``storage.download`` / ``storage.delete``, ``data_loading.load``,
``registry.implementation``, ``persistence.save_success`` /
``save_failure``) are patched on the dispatcher module so the test
exercises the dispatcher's branching only.

Cleanup expectations
--------------------
The dispatcher must call ``storage.delete`` exactly once for every
**terminal** outcome (success, parse failure, algorithm failure, invalid
inputs, implementation missing, object missing). It must **not** call
delete on transient failures (NAK), because the redelivered message
still needs to read the file.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import Field
from sqlalchemy.exc import OperationalError

from algorithms.base import (
    AlgorithmInput,
    AlgorithmMetadata,
    AlgorithmRawData,
    AlgorithmResult,
)
from core.queue.helper import Event
from shared.const import GenerationStatus
from worker import dispatcher

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeMsg:
    """Minimal stand-in for ``nats.aio.msg.Msg`` used by the dispatcher.

    Tracks whether ``ack()`` or ``nak()`` was called and (for nak) the
    delay so tests can assert on the JetStream redelivery contract.
    """

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.acked = False
        self.naked = False
        self.nak_delay: int | None = None

    async def ack(self) -> None:
        self.acked = True

    async def nak(self, delay: int | None = None) -> None:
        self.naked = True
        self.nak_delay = delay


class _DummyInput(AlgorithmInput):
    value: int = Field(..., ge=0)


def _make_meta(name: str = "fake_algo") -> AlgorithmMetadata:
    return AlgorithmMetadata(
        name=name,
        description="dispatcher failure-path test fake",
        version="1.0",
        queue=f"optimce.allocation.{name}",
        input_schema=_DummyInput,
    )


def _make_event_bytes(generation_id, inputs: dict | None = None) -> bytes:
    return Event(
        type="generation.requested",
        data={"generation_id": generation_id, "inputs": inputs or {}},
    ).encode()


_TEST_STORAGE_KEY = "allocations/1/test-uuid/data.csv"


def _make_snapshot(
    *,
    generation_id: int = 1,
    inputs: dict | None = None,
    status: int = GenerationStatus.PENDING,
    file_storage_key: str = _TEST_STORAGE_KEY,
) -> dispatcher._GenerationSnapshot:
    return dispatcher._GenerationSnapshot(
        id=generation_id,
        file_storage_key=file_storage_key,
        file_name="data.csv",
        injection_name="production",
        inputs=inputs if inputs is not None else {"value": 1},
        id_community=1,
        status=int(status),
    )


class _FakeAlgoSuccess:
    async def run(self, inputs, raw_data):
        return AlgorithmResult()


class _FakeAlgoRaises:
    async def run(self, inputs, raw_data):
        raise RuntimeError("boom")


@pytest.fixture
def patched_save(monkeypatch):
    """Replace persistence.save_success / save_failure with AsyncMock spies.

    Returns the (save_success, save_failure) pair so individual tests can
    assert on call counts and arguments without touching a real DB.
    """
    save_success = AsyncMock()
    save_failure = AsyncMock()
    monkeypatch.setattr(dispatcher.persistence, "save_success", save_success)
    monkeypatch.setattr(dispatcher.persistence, "save_failure", save_failure)
    return save_success, save_failure


@pytest.fixture
def patched_storage(monkeypatch):
    """Replace storage.download / storage.delete with AsyncMock spies.

    Default behaviour: download returns dummy bytes, delete is a no-op.
    Returns the (download, delete) pair so tests can override download
    side-effects and assert on delete invocations.
    """
    download = AsyncMock(return_value=b"file-bytes")
    delete = AsyncMock()
    monkeypatch.setattr(dispatcher.storage, "download", download)
    monkeypatch.setattr(dispatcher.storage, "delete", delete)
    return download, delete


@pytest.fixture
def stub_io(monkeypatch, patched_storage):
    """Default-success patches for download and parse so each test only
    needs to override the specific step it's exercising.
    """
    monkeypatch.setattr(
        dispatcher.data_loading,
        "load",
        MagicMock(return_value=AlgorithmRawData(C=None, VA=None, consumer_names=[])),
    )


# ---------------------------------------------------------------------------
# Malformed events — handler bails before _process is reached
# ---------------------------------------------------------------------------


async def test_handler_acks_and_drops_when_event_is_invalid_json(patched_save, patched_storage):
    save_success, save_failure = patched_save
    _download, delete = patched_storage
    handler = dispatcher._make_handler(_make_meta())
    msg = FakeMsg(b"not json at all")

    await handler(msg)

    assert msg.acked is True
    assert msg.naked is False
    save_success.assert_not_awaited()
    save_failure.assert_not_awaited()
    delete.assert_not_awaited()


async def test_handler_acks_when_generation_id_missing_from_event(patched_save, patched_storage):
    save_success, save_failure = patched_save
    _download, delete = patched_storage
    handler = dispatcher._make_handler(_make_meta())
    payload = Event(type="generation.requested", data={"oops": "no id"}).encode()
    msg = FakeMsg(payload)

    await handler(msg)

    assert msg.acked is True
    assert msg.naked is False
    save_success.assert_not_awaited()
    save_failure.assert_not_awaited()
    delete.assert_not_awaited()


async def test_handler_acks_when_generation_id_is_not_int(patched_save, patched_storage):
    save_success, save_failure = patched_save
    _download, delete = patched_storage
    handler = dispatcher._make_handler(_make_meta())
    payload = Event(type="generation.requested", data={"generation_id": "abc"}).encode()
    msg = FakeMsg(payload)

    await handler(msg)

    assert msg.acked is True
    assert msg.naked is False
    save_success.assert_not_awaited()
    save_failure.assert_not_awaited()
    delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Algorithm raises — deterministic, mark FAILED, ack, delete file
# ---------------------------------------------------------------------------


async def test_handler_calls_save_failure_when_algorithm_raises(
    monkeypatch, patched_save, stub_io, patched_storage
):
    save_success, save_failure = patched_save
    _download, delete = patched_storage
    monkeypatch.setattr(
        dispatcher,
        "_snapshot_generation",
        AsyncMock(return_value=_make_snapshot()),
    )
    monkeypatch.setattr(
        "algorithms.registry.registry.implementation",
        lambda name: _FakeAlgoRaises,
    )

    handler = dispatcher._make_handler(_make_meta())
    msg = FakeMsg(_make_event_bytes(generation_id=42))

    await handler(msg)

    assert msg.acked is True
    assert msg.naked is False
    save_success.assert_not_awaited()
    save_failure.assert_awaited_once()
    args, _kwargs = save_failure.await_args
    assert args[0] == 42
    assert args[1].startswith("algorithm_failed:")
    delete.assert_awaited_once_with(_TEST_STORAGE_KEY)


# ---------------------------------------------------------------------------
# Implementation missing — registry lookup raises KeyError
# ---------------------------------------------------------------------------


async def test_handler_calls_save_failure_when_implementation_missing(
    monkeypatch, patched_save, stub_io, patched_storage
):
    save_success, save_failure = patched_save
    _download, delete = patched_storage
    monkeypatch.setattr(
        dispatcher,
        "_snapshot_generation",
        AsyncMock(return_value=_make_snapshot()),
    )

    def _missing(_name):
        raise KeyError("no impl")

    monkeypatch.setattr("algorithms.registry.registry.implementation", _missing)

    handler = dispatcher._make_handler(_make_meta("fake_algo_unloaded"))
    msg = FakeMsg(_make_event_bytes(generation_id=7))

    await handler(msg)

    assert msg.acked is True
    assert msg.naked is False
    save_success.assert_not_awaited()
    save_failure.assert_awaited_once()
    args, _kwargs = save_failure.await_args
    assert args[0] == 7
    assert args[1].startswith("implementation_missing:")
    delete.assert_awaited_once_with(_TEST_STORAGE_KEY)


# ---------------------------------------------------------------------------
# Invalid inputs — Pydantic ValidationError on the snapshot inputs
# ---------------------------------------------------------------------------


async def test_handler_calls_save_failure_when_inputs_fail_validation(
    monkeypatch, patched_save, stub_io, patched_storage
):
    save_success, save_failure = patched_save
    _download, delete = patched_storage
    bad_snapshot = _make_snapshot(inputs={"unknown_field": 1})
    monkeypatch.setattr(
        dispatcher,
        "_snapshot_generation",
        AsyncMock(return_value=bad_snapshot),
    )

    handler = dispatcher._make_handler(_make_meta())
    msg = FakeMsg(_make_event_bytes(generation_id=11))

    await handler(msg)

    assert msg.acked is True
    assert msg.naked is False
    save_success.assert_not_awaited()
    save_failure.assert_awaited_once()
    args, _kwargs = save_failure.await_args
    assert args[0] == 11
    assert args[1].startswith("invalid_inputs:")
    delete.assert_awaited_once_with(_TEST_STORAGE_KEY)


# ---------------------------------------------------------------------------
# DB unavailable during persist — TRANSIENT, nak for redelivery, NO DELETE
# ---------------------------------------------------------------------------


async def test_handler_naks_when_save_success_raises_db_error(
    monkeypatch, patched_save, stub_io, patched_storage
):
    save_success, save_failure = patched_save
    _download, delete = patched_storage
    save_success.side_effect = OperationalError("SELECT 1", {}, Exception("conn lost"))

    monkeypatch.setattr(
        dispatcher,
        "_snapshot_generation",
        AsyncMock(return_value=_make_snapshot()),
    )
    monkeypatch.setattr(
        "algorithms.registry.registry.implementation",
        lambda name: _FakeAlgoSuccess,
    )

    handler = dispatcher._make_handler(_make_meta())
    msg = FakeMsg(_make_event_bytes(generation_id=99))

    await handler(msg)

    assert msg.naked is True
    assert msg.acked is False
    assert msg.nak_delay == dispatcher._NAK_RETRY_DELAY_SECONDS
    save_success.assert_awaited_once()
    save_failure.assert_not_awaited()
    # Critical: a NAK must not delete the file — redelivery will need it.
    delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# DB unavailable during snapshot — caught by handler's catch-all,
# marked FAILED with "unhandled_worker_error", acked.
# ---------------------------------------------------------------------------


async def test_handler_acks_with_unhandled_error_when_snapshot_raises(
    monkeypatch, patched_save, patched_storage
):
    save_success, save_failure = patched_save
    _download, delete = patched_storage
    monkeypatch.setattr(
        dispatcher,
        "_snapshot_generation",
        AsyncMock(side_effect=OperationalError("SELECT 1", {}, Exception("db down"))),
    )

    handler = dispatcher._make_handler(_make_meta())
    msg = FakeMsg(_make_event_bytes(generation_id=55))

    await handler(msg)

    assert msg.acked is True
    assert msg.naked is False
    save_success.assert_not_awaited()
    save_failure.assert_awaited_once_with(55, "unhandled_worker_error")
    # snapshot failed before we knew the key; the catch-all skips deletion.
    delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Happy path — success, ack, delete
# ---------------------------------------------------------------------------


async def test_handler_deletes_object_on_success(
    monkeypatch, patched_save, stub_io, patched_storage
):
    save_success, save_failure = patched_save
    _download, delete = patched_storage
    monkeypatch.setattr(
        dispatcher,
        "_snapshot_generation",
        AsyncMock(return_value=_make_snapshot()),
    )
    monkeypatch.setattr(
        "algorithms.registry.registry.implementation",
        lambda name: _FakeAlgoSuccess,
    )

    handler = dispatcher._make_handler(_make_meta())
    msg = FakeMsg(_make_event_bytes(generation_id=33))

    await handler(msg)

    assert msg.acked is True
    assert msg.naked is False
    save_success.assert_awaited_once()
    save_failure.assert_not_awaited()
    delete.assert_awaited_once_with(_TEST_STORAGE_KEY)


# ---------------------------------------------------------------------------
# Storage object missing — deterministic, mark FAILED, ack
# ---------------------------------------------------------------------------


async def test_handler_marks_failed_when_storage_object_missing(
    monkeypatch, patched_save, patched_storage
):
    save_success, save_failure = patched_save
    download, delete = patched_storage
    download.side_effect = dispatcher.storage.ObjectNotFound("gone")

    monkeypatch.setattr(
        dispatcher,
        "_snapshot_generation",
        AsyncMock(return_value=_make_snapshot()),
    )

    handler = dispatcher._make_handler(_make_meta())
    msg = FakeMsg(_make_event_bytes(generation_id=44))

    await handler(msg)

    assert msg.acked is True
    assert msg.naked is False
    save_success.assert_not_awaited()
    save_failure.assert_awaited_once_with(44, "storage_object_missing")
    # Object was already gone; the handler still calls delete only when the
    # snapshot reports a key. Here _process returns _Terminal(storage_key=None)
    # because the object is already absent, so no second delete attempt.
    delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Storage transient — NAK, NO DELETE
# ---------------------------------------------------------------------------


async def test_handler_naks_on_storage_transient_error(monkeypatch, patched_save, patched_storage):
    save_success, save_failure = patched_save
    download, delete = patched_storage
    download.side_effect = dispatcher.storage.TransientStorageError("storage 503")

    monkeypatch.setattr(
        dispatcher,
        "_snapshot_generation",
        AsyncMock(return_value=_make_snapshot()),
    )

    handler = dispatcher._make_handler(_make_meta())
    msg = FakeMsg(_make_event_bytes(generation_id=77))

    await handler(msg)

    assert msg.naked is True
    assert msg.acked is False
    assert msg.nak_delay == dispatcher._NAK_RETRY_DELAY_SECONDS
    save_success.assert_not_awaited()
    save_failure.assert_not_awaited()
    # Critical: a NAK must not delete the file — redelivery will need it.
    delete.assert_not_awaited()
