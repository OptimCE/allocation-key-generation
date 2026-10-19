"""When JetStream redelivers a generation, and when it must not.

* **Long runs.** A generation can take far longer than ack_wait (10 minutes):
  208 solves and over 9 minutes for 20 meters x 92 days with 2 iterations, and
  waiting for a solver slot comes on top. While it is being processed the
  handler sends in-progress acks, so JetStream does not hand the message out a
  second time and the worker does not solve the same generation twice.
* **Solver crashes.** A solver process that dies (OOM kill, native crash)
  breaks the whole pool, so every generation solving at that moment sees it.
  The generation is redelivered while its delivery count is below
  ``_MAX_SOLVER_CRASH_DELIVERIES``, then marked FAILED, so a crash that repeats
  on every delivery cannot loop.
* **Shutdown.** The drain in ``worker.main`` cancels generations that outlast
  it; cancelling one stops its in-progress acks and settles nothing, so the
  message is redelivered once ack_wait runs out.

A ThreadPoolExecutor stands in for the solver process pool (a real one only
where a process has to die), and the in-progress interval is shortened so a
"long" run takes a few hundredths of a second. The solve blocks its thread
until the test releases it, as a real solve blocks its process.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import threading
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from nats.errors import ConnectionClosedError, NotJSMessageError
from pydantic import Field

from algorithms.base import (
    AlgorithmInput,
    AlgorithmMetadata,
    AlgorithmRawData,
    AlgorithmResult,
)
from core.queue.helper import Event
from shared.const import DataSource, GenerationStatus
from worker import dispatcher
from worker import main as worker_main

# Short enough that a test sees several in-progress acks within a fraction of
# a second, long enough that the event loop is never flooded.
_FAST_INTERVAL_SECONDS = 0.01

_STORAGE_KEY = "allocations/1/test-uuid/data.csv"

# What ProcessPoolExecutor reports when one of its processes is killed.
_POOL_BROKEN = (
    "A process in the process pool was terminated abruptly while the future was "
    "running or pending."
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeMsg:
    """``nats.aio.msg.Msg`` stand-in that records every settle call in order.

    ``events`` holds "in_progress", "ack" and "nak" as they happen, so a test
    can check that no in-progress ack follows the ack or nak.
    ``num_delivered`` feeds ``metadata.num_delivered``; None makes ``metadata``
    raise, as it does for a message that did not come from JetStream.
    """

    def __init__(
        self,
        data: bytes,
        *,
        num_delivered: int | None = 1,
        in_progress_error: Exception | None = None,
    ) -> None:
        self.data = data
        self.events: list[str] = []
        self.nak_delay: int | None = None
        self._num_delivered = num_delivered
        self._in_progress_error = in_progress_error
        self._progressed = asyncio.Event()

    @property
    def metadata(self) -> SimpleNamespace:
        if self._num_delivered is None:
            raise NotJSMessageError
        return SimpleNamespace(num_delivered=self._num_delivered)

    @property
    def acked(self) -> bool:
        return "ack" in self.events

    @property
    def naked(self) -> bool:
        return "nak" in self.events

    @property
    def in_progress_count(self) -> int:
        return self.events.count("in_progress")

    async def ack(self) -> None:
        self.events.append("ack")

    async def nak(self, delay: int | None = None) -> None:
        self.events.append("nak")
        self.nak_delay = delay

    async def in_progress(self) -> None:
        self.events.append("in_progress")
        self._progressed.set()
        if self._in_progress_error is not None:
            raise self._in_progress_error

    async def wait_for_in_progress(self, count: int) -> None:
        """Return once ``count`` in-progress acks were sent; fail after 5 s."""
        async with asyncio.timeout(5):
            while self.in_progress_count < count:
                self._progressed.clear()
                await self._progressed.wait()


class _DummyInput(AlgorithmInput):
    value: int = Field(default=1, ge=0)


class _QuickAlgo:
    async def run(self, inputs, raw_data) -> AlgorithmResult:
        return AlgorithmResult()


def _blocking_algo(release: threading.Event, error: Exception | None = None) -> type:
    """An algorithm whose solve blocks its thread until ``release`` is set."""

    class _BlockingAlgo:
        async def run(self, inputs, raw_data) -> AlgorithmResult:
            if not release.wait(timeout=10):
                raise TimeoutError("the test never released the solve")
            if error is not None:
                raise error
            return AlgorithmResult()

    return _BlockingAlgo


class _CrashingPool(Executor):
    """A pool whose solves all fail as if their process had been killed."""

    def submit(self, fn, /, *args, **kwargs):
        future: Future = Future()
        future.set_exception(BrokenProcessPool(_POOL_BROKEN))
        return future


def _make_meta(name: str = "redelivery_fake") -> AlgorithmMetadata:
    return AlgorithmMetadata(
        name=name,
        description="dispatcher redelivery test fake",
        version="1.0",
        queue=f"optimce.allocation.{name}",
        input_schema=_DummyInput,
    )


def _make_msg(generation_id: int = 1, **kwargs) -> FakeMsg:
    data = Event(type="generation.requested", data={"generation_id": generation_id}).encode()
    return FakeMsg(data, **kwargs)


def _make_raw() -> AlgorithmRawData:
    return AlgorithmRawData(C=np.zeros((2, 3)), VA=np.ones((2, 3)), consumer_names=["a", "b"])


def _make_snapshot(generation_id: int = 1) -> dispatcher._GenerationSnapshot:
    return dispatcher._GenerationSnapshot(
        id=generation_id,
        source=DataSource.FILE,
        file_storage_key=_STORAGE_KEY,
        file_name="data.csv",
        injection_name="production",
        id_sharing_operation=None,
        period_start=None,
        period_end=None,
        inputs={"value": 1},
        id_community=1,
        status=int(GenerationStatus.PENDING),
    )


async def _quiet_for_a_few_intervals() -> None:
    """Long enough for a heartbeat that was still running to send again."""
    await asyncio.sleep(_FAST_INTERVAL_SECONDS * 5)


@pytest.fixture
def fast_heartbeat(monkeypatch):
    monkeypatch.setattr(dispatcher, "_IN_PROGRESS_INTERVAL_SECONDS", _FAST_INTERVAL_SECONDS)


@pytest.fixture
def patched_save(monkeypatch):
    save_success = AsyncMock()
    save_failure = AsyncMock()
    monkeypatch.setattr(dispatcher.persistence, "save_success", save_success)
    monkeypatch.setattr(dispatcher.persistence, "save_failure", save_failure)
    return save_success, save_failure


@pytest.fixture
def patched_delete(monkeypatch):
    """Stub the row snapshot and the file I/O; returns the storage.delete spy."""
    monkeypatch.setattr(
        dispatcher, "_snapshot_generation", AsyncMock(return_value=_make_snapshot())
    )
    monkeypatch.setattr(dispatcher.storage, "download", AsyncMock(return_value=b"file-bytes"))
    monkeypatch.setattr(
        dispatcher.data_loading,
        "load",
        MagicMock(
            return_value=AlgorithmRawData(
                C=np.zeros((1, 4)), VA=np.ones((1, 4)), consumer_names=["a"]
            )
        ),
    )
    delete = AsyncMock()
    monkeypatch.setattr(dispatcher.storage, "delete", delete)
    return delete


def _use_algorithm(monkeypatch, algo: type) -> None:
    # Looked up by name inside the solver (thread here, process in production).
    monkeypatch.setattr("algorithms.registry.registry.implementation", lambda name: algo)


@pytest.fixture
def solver_thread():
    with ThreadPoolExecutor(max_workers=1) as executor:
        yield executor


# ---------------------------------------------------------------------------
# Long runs: in-progress acks while the generation is processed
# ---------------------------------------------------------------------------


async def test_a_long_solve_sends_in_progress_acks_until_it_is_acked(
    monkeypatch, fast_heartbeat, patched_save, patched_delete, solver_thread
):
    save_success, save_failure = patched_save
    release = threading.Event()
    _use_algorithm(monkeypatch, _blocking_algo(release))
    msg = _make_msg()

    task = asyncio.create_task(
        dispatcher._handle_message(_make_meta(), msg, executor=solver_thread)
    )
    try:
        await msg.wait_for_in_progress(3)
        assert not msg.acked
        assert not msg.naked
    finally:
        release.set()
    await task

    assert msg.events[-1] == "ack"
    assert msg.events.count("ack") == 1
    assert not msg.naked
    save_success.assert_awaited_once()
    save_failure.assert_not_awaited()
    patched_delete.assert_awaited_once_with(_STORAGE_KEY)
    # Stopped with the generation: nothing more after the ack.
    await _quiet_for_a_few_intervals()
    assert msg.events[-1] == "ack"


async def test_waiting_for_a_solver_slot_also_sends_in_progress_acks(
    monkeypatch, fast_heartbeat, patched_save, patched_delete, solver_thread
):
    """The semaphore is shared by every algorithm's subscription, so a
    generation can wait behind another one's long solve for longer than
    ack_wait. A redelivery then would run it twice."""
    save_success, _save_failure = patched_save
    _use_algorithm(monkeypatch, _QuickAlgo)
    slot = asyncio.Semaphore(1)
    await slot.acquire()  # another generation is solving
    msg = _make_msg()

    task = asyncio.create_task(
        dispatcher._handle_message(_make_meta(), msg, executor=solver_thread, semaphore=slot)
    )
    try:
        await msg.wait_for_in_progress(3)
        save_success.assert_not_awaited()
    finally:
        slot.release()
    await task

    assert msg.events[-1] == "ack"
    save_success.assert_awaited_once()
    await _quiet_for_a_few_intervals()
    assert msg.events[-1] == "ack"


async def test_in_progress_acks_stop_when_the_algorithm_fails(
    monkeypatch, fast_heartbeat, patched_save, patched_delete, solver_thread
):
    save_success, save_failure = patched_save
    release = threading.Event()
    _use_algorithm(monkeypatch, _blocking_algo(release, error=RuntimeError("boom")))
    msg = _make_msg(generation_id=12)

    task = asyncio.create_task(
        dispatcher._handle_message(_make_meta(), msg, executor=solver_thread)
    )
    try:
        await msg.wait_for_in_progress(2)
    finally:
        release.set()
    await task

    assert msg.events[-1] == "ack"
    save_success.assert_not_awaited()
    save_failure.assert_awaited_once_with(12, "algorithm_failed: boom")
    await _quiet_for_a_few_intervals()
    assert msg.events[-1] == "ack"


async def test_a_failing_in_progress_ack_does_not_fail_the_generation(
    monkeypatch, fast_heartbeat, patched_save, patched_delete, solver_thread
):
    """A lost in-progress ack only risks a redelivery later; the generation
    itself carries on and is stored."""
    save_success, save_failure = patched_save
    release = threading.Event()
    _use_algorithm(monkeypatch, _blocking_algo(release))
    msg = _make_msg(in_progress_error=ConnectionClosedError())

    task = asyncio.create_task(
        dispatcher._handle_message(_make_meta(), msg, executor=solver_thread)
    )
    try:
        # Keeps trying after a failure.
        await msg.wait_for_in_progress(3)
    finally:
        release.set()
    await task

    assert msg.events[-1] == "ack"
    save_success.assert_awaited_once()
    save_failure.assert_not_awaited()


async def test_a_generation_shorter_than_the_interval_sends_no_in_progress_ack(
    monkeypatch, patched_save, patched_delete, solver_thread
):
    save_success, _save_failure = patched_save
    _use_algorithm(monkeypatch, _QuickAlgo)
    msg = _make_msg()

    await dispatcher._handle_message(_make_meta(), msg, executor=solver_thread)

    assert msg.events == ["ack"]
    save_success.assert_awaited_once()


def test_in_progress_acks_come_several_times_per_ack_wait():
    """A few lost or late in-progress acks must not let ack_wait lapse."""
    assert dispatcher._ACK_WAIT_SECONDS >= 5 * dispatcher._IN_PROGRESS_INTERVAL_SECONDS


# ---------------------------------------------------------------------------
# Shutdown: the drain in worker.main
# ---------------------------------------------------------------------------


async def test_shutdown_drain_lets_a_solve_that_ends_in_time_ack(
    monkeypatch, fast_heartbeat, patched_save, patched_delete, solver_thread
):
    save_success, _save_failure = patched_save
    monkeypatch.setattr(worker_main, "_INFLIGHT_DRAIN_TIMEOUT_SECONDS", 5)
    release = threading.Event()
    _use_algorithm(monkeypatch, _blocking_algo(release))
    inflight: set[asyncio.Task] = set()
    handler = dispatcher._make_handler(_make_meta(), executor=solver_thread, inflight=inflight)
    msg = _make_msg()

    await handler(msg)
    try:
        await msg.wait_for_in_progress(1)
        asyncio.get_running_loop().call_later(_FAST_INTERVAL_SECONDS * 3, release.set)
        await worker_main._drain_inflight(inflight)
    finally:
        release.set()

    assert msg.events[-1] == "ack"
    save_success.assert_awaited_once()
    patched_delete.assert_awaited_once_with(_STORAGE_KEY)


async def test_shutdown_drain_cancels_a_straggler_and_stops_its_in_progress_acks(
    monkeypatch, fast_heartbeat, patched_save, patched_delete, solver_thread
):
    """Cancelled at the drain timeout: neither acked nor naked, no row
    written, file kept, and no more in-progress acks, so JetStream redelivers
    it once ack_wait runs out."""
    save_success, save_failure = patched_save
    monkeypatch.setattr(worker_main, "_INFLIGHT_DRAIN_TIMEOUT_SECONDS", _FAST_INTERVAL_SECONDS)
    release = threading.Event()
    _use_algorithm(monkeypatch, _blocking_algo(release))
    inflight: set[asyncio.Task] = set()
    handler = dispatcher._make_handler(_make_meta(), executor=solver_thread, inflight=inflight)
    msg = _make_msg()

    await handler(msg)
    (task,) = inflight
    try:
        await msg.wait_for_in_progress(2)
        await worker_main._drain_inflight(inflight)
        await asyncio.wait({task})
        beats_at_cancel = msg.in_progress_count
        await _quiet_for_a_few_intervals()
    finally:
        release.set()

    assert task.cancelled()
    assert msg.events == ["in_progress"] * beats_at_cancel
    save_success.assert_not_awaited()
    save_failure.assert_not_awaited()
    patched_delete.assert_not_awaited()


async def test_shutdown_drain_does_nothing_without_inflight_work():
    await worker_main._drain_inflight(set())


# ---------------------------------------------------------------------------
# Solver crashes: retried on a few deliveries, then FAILED
# ---------------------------------------------------------------------------


async def test_run_algorithm_reports_a_broken_pool_as_a_solver_crash():
    slot = asyncio.Semaphore(1)

    with pytest.raises(dispatcher._SolverCrashError, match="terminated abruptly"):
        await dispatcher._run_algorithm(
            "pool_fake", _QuickAlgo, _DummyInput(), _make_raw(), _CrashingPool(), slot
        )

    assert slot.locked() is False


async def test_a_killed_solver_process_is_reported_as_a_solver_crash():
    """The real thing: a pool process that dies surfaces as a solver crash.

    The process exits in the pool initializer, before it can take the work, so
    nothing needs to be registered inside it. "spawn" on every platform: fork
    from a multi-threaded test process warns, and warnings fail the suite.
    """
    pool = ProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=os._exit,
        initargs=(1,),
    )
    try:
        with pytest.raises(dispatcher._SolverCrashError):
            await asyncio.wait_for(
                dispatcher._run_algorithm(
                    "pool_fake", _QuickAlgo, _DummyInput(), _make_raw(), pool, None
                ),
                timeout=60,
            )
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


@pytest.mark.parametrize("num_delivered", [1, dispatcher._MAX_SOLVER_CRASH_DELIVERIES - 1])
async def test_a_solver_crash_before_the_last_delivery_is_redelivered(
    monkeypatch, patched_save, patched_delete, num_delivered
):
    save_success, save_failure = patched_save
    _use_algorithm(monkeypatch, _QuickAlgo)
    msg = _make_msg(num_delivered=num_delivered)

    await dispatcher._handle_message(_make_meta(), msg, executor=_CrashingPool())

    assert msg.events == ["nak"]
    assert msg.nak_delay == dispatcher._NAK_RETRY_DELAY_SECONDS
    save_success.assert_not_awaited()
    save_failure.assert_not_awaited()
    # The redelivery still needs the file.
    patched_delete.assert_not_awaited()


@pytest.mark.parametrize(
    "num_delivered",
    # The cap itself, and a message already redelivered past it for other
    # reasons (an expired ack_wait, storage NAKs).
    [dispatcher._MAX_SOLVER_CRASH_DELIVERIES, dispatcher._MAX_SOLVER_CRASH_DELIVERIES + 4],
)
async def test_a_solver_crash_on_the_last_delivery_marks_the_generation_failed(
    monkeypatch, patched_save, patched_delete, num_delivered
):
    save_success, save_failure = patched_save
    _use_algorithm(monkeypatch, _QuickAlgo)
    msg = _make_msg(generation_id=21, num_delivered=num_delivered)

    await dispatcher._handle_message(_make_meta(), msg, executor=_CrashingPool())

    assert msg.events == ["ack"]
    save_success.assert_not_awaited()
    save_failure.assert_awaited_once_with(
        21,
        f"solver_crashed (likely out of memory) on delivery {num_delivered}: {_POOL_BROKEN}",
    )
    patched_delete.assert_awaited_once_with(_STORAGE_KEY)


async def test_a_solver_crash_with_an_unreadable_delivery_count_is_not_retried(
    monkeypatch, patched_save, patched_delete
):
    """Without a count there is no way to stop a retry loop, so fail."""
    _save_success, save_failure = patched_save
    _use_algorithm(monkeypatch, _QuickAlgo)
    msg = _make_msg(generation_id=22, num_delivered=None)

    await dispatcher._handle_message(_make_meta(), msg, executor=_CrashingPool())

    assert msg.events == ["ack"]
    save_failure.assert_awaited_once_with(
        22, f"solver_crashed (likely out of memory) on delivery unknown: {_POOL_BROKEN}"
    )
    patched_delete.assert_awaited_once_with(_STORAGE_KEY)


async def test_an_algorithm_exception_in_the_pool_is_not_retried_like_a_crash(
    monkeypatch, patched_save, patched_delete, solver_thread
):
    """Only a dead solver process is retried; an exception the algorithm
    raises fails the generation on its first delivery."""
    _save_success, save_failure = patched_save
    release = threading.Event()
    release.set()
    _use_algorithm(monkeypatch, _blocking_algo(release, error=ValueError("bad split")))
    msg = _make_msg(generation_id=23, num_delivered=1)

    await dispatcher._handle_message(_make_meta(), msg, executor=solver_thread)

    assert msg.events == ["ack"]
    save_failure.assert_awaited_once_with(23, "algorithm_failed: bad split")
