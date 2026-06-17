"""Tests for the solver process-pool offload added to the worker.

These cover the moving parts introduced to keep CPU-bound solves off the
event loop (so NATS PING/PONG + the liveness heartbeat never starve) and to
run several generations in parallel:

* ``worker.main._detect_available_cpus`` / ``_solver_pool_size`` — sizing the
  pool from the CPUs the container is actually allowed to use (cgroup quota),
  not the host core count.
* ``worker.dispatcher._run_algorithm`` / ``_run_algorithm_subprocess`` — the
  inline-vs-executor branch and the picklable subprocess entry point.
* ``worker.dispatcher._make_handler`` spawning path — when an ``inflight`` set
  is provided the callback returns immediately and the work runs as a tracked
  background task (inline-to-completion when it is not).

A ThreadPoolExecutor stands in for the ProcessPoolExecutor so the tests stay
fast and cross-platform (no spawn/pickle round-trip); picklability of the
payloads is asserted separately.
"""

from __future__ import annotations

import asyncio
import pickle
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
from nats.js.api import ConsumerConfig
from pydantic import Field

from algorithms.base import (
    AlgorithmInput,
    AlgorithmMetadata,
    AlgorithmRawData,
    AlgorithmResult,
)
from core.queue.helper import Event
from worker import dispatcher
from worker import main as worker_main

# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


class _DummyInput(AlgorithmInput):
    value: int = Field(default=1, ge=0)


class _FakePoolAlgo:
    """Stand-in implementation: ``run`` is async but does no real awaiting."""

    async def run(self, inputs, raw_data) -> AlgorithmResult:
        return AlgorithmResult()


class FakeMsg:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.acked = False
        self.naked = False

    async def ack(self) -> None:
        self.acked = True

    async def nak(self, delay: int | None = None) -> None:
        self.naked = True


def _make_meta(name: str = "pool_fake") -> AlgorithmMetadata:
    return AlgorithmMetadata(
        name=name,
        description="solver pool test fake",
        version="1.0",
        queue=f"optimce.allocation.{name}",
        input_schema=_DummyInput,
    )


def _make_raw() -> AlgorithmRawData:
    return AlgorithmRawData(
        C=np.zeros((2, 3)),
        VA=np.ones((2, 3)),
        consumer_names=["a", "b"],
    )


def _event_bytes(generation_id: int = 1) -> bytes:
    return Event(type="generation.requested", data={"generation_id": generation_id}).encode()


# ---------------------------------------------------------------------------
# CPU detection / pool sizing
# ---------------------------------------------------------------------------


def test_detect_cpus_reads_cgroup_v2_quota(monkeypatch):
    """cgroup v2 "quota period" (microseconds) → quota/period cores."""

    def fake_read_text(self, *args, **kwargs):
        if self.as_posix() == "/sys/fs/cgroup/cpu.max":
            return "200000 100000"
        raise FileNotFoundError(self)

    monkeypatch.setattr(worker_main.pathlib.Path, "read_text", fake_read_text)
    assert worker_main._detect_available_cpus() == 2


def test_detect_cpus_cgroup_v2_unlimited_falls_through(monkeypatch):
    """ "max" quota means unlimited → fall back to a sane positive count."""

    def fake_read_text(self, *args, **kwargs):
        if self.as_posix() == "/sys/fs/cgroup/cpu.max":
            return "max 100000"
        raise FileNotFoundError(self)

    monkeypatch.setattr(worker_main.pathlib.Path, "read_text", fake_read_text)
    assert worker_main._detect_available_cpus() >= 1


def test_detect_cpus_reads_cgroup_v1_quota(monkeypatch):
    """cgroup v1 splits quota / period across two files."""

    def fake_read_text(self, *args, **kwargs):
        posix = self.as_posix()
        if posix == "/sys/fs/cgroup/cpu/cpu.cfs_quota_us":
            return "300000"
        if posix == "/sys/fs/cgroup/cpu/cpu.cfs_period_us":
            return "100000"
        raise FileNotFoundError(self)

    monkeypatch.setattr(worker_main.pathlib.Path, "read_text", fake_read_text)
    assert worker_main._detect_available_cpus() == 3


def test_solver_pool_size_reserves_one_core(monkeypatch):
    monkeypatch.setattr(worker_main, "_detect_available_cpus", lambda: 4)
    assert worker_main._solver_pool_size() == 3


def test_solver_pool_size_floor_is_one(monkeypatch):
    monkeypatch.setattr(worker_main, "_detect_available_cpus", lambda: 1)
    assert worker_main._solver_pool_size() == 1


# ---------------------------------------------------------------------------
# _SolverPool — rebuilds itself when a worker dies abnormally
# ---------------------------------------------------------------------------


def test_solver_pool_rebuilds_after_broken_worker(monkeypatch):
    """A poisoned pool (BrokenProcessPool on submit) is rebuilt transparently.

    The underlying ProcessPoolExecutor is faked so no real processes spawn.
    """
    from concurrent.futures.process import BrokenProcessPool

    created: list = []

    class _FakePool:
        def __init__(self, *args, **kwargs):
            self.broken = False
            created.append(self)

        def submit(self, fn, *args, **kwargs):
            if self.broken:
                raise BrokenProcessPool("worker died")
            return ("future", self)

        def shutdown(self, *args, **kwargs):
            pass

    monkeypatch.setattr(worker_main, "ProcessPoolExecutor", _FakePool)

    pool = worker_main._SolverPool(2, lambda: None)
    assert len(created) == 1

    # Healthy submit goes to the original pool.
    assert pool.submit(len, [1, 2])[1] is created[0]

    # Poison the live pool: the next submit must rebuild and use a fresh one.
    created[0].broken = True
    result = pool.submit(len, [1, 2, 3])
    assert len(created) == 2
    assert result[1] is created[1]


# ---------------------------------------------------------------------------
# Payload picklability — required to cross the process-pool boundary
# ---------------------------------------------------------------------------


def test_solve_payloads_survive_pickling():
    inp = _DummyInput(value=2)
    raw = _make_raw()
    res = AlgorithmResult()

    # Trusted, self-produced data — round-trip just proves picklability.
    assert pickle.loads(pickle.dumps(inp)).value == 2  # noqa: S301
    raw_rt = pickle.loads(pickle.dumps(raw))  # noqa: S301
    assert raw_rt.C.shape == (2, 3)
    assert raw_rt.consumer_names == ["a", "b"]
    assert isinstance(pickle.loads(pickle.dumps(res)), AlgorithmResult)  # noqa: S301


# ---------------------------------------------------------------------------
# _run_algorithm — inline vs executor branch
# ---------------------------------------------------------------------------


async def test_run_algorithm_inline_when_no_executor():
    result = await dispatcher._run_algorithm(
        "pool_fake", _FakePoolAlgo, _DummyInput(), _make_raw(), None, None
    )
    assert isinstance(result, AlgorithmResult)


async def test_run_algorithm_uses_executor_and_semaphore(monkeypatch):
    """Executor branch: resolves via the registry inside the worker and runs."""
    monkeypatch.setattr("algorithms.registry.registry.implementation", lambda name: _FakePoolAlgo)
    semaphore = asyncio.Semaphore(1)
    with ThreadPoolExecutor(max_workers=1) as ex:
        result = await dispatcher._run_algorithm(
            "pool_fake", _FakePoolAlgo, _DummyInput(), _make_raw(), ex, semaphore
        )
    assert isinstance(result, AlgorithmResult)
    # Semaphore released after the solve so the pool slot is reusable.
    assert semaphore.locked() is False


def test_run_algorithm_subprocess_resolves_and_runs(monkeypatch):
    """The picklable entry point resolves the impl and drives its async run.

    Sync test: ``_run_algorithm_subprocess`` calls ``asyncio.run`` internally,
    which must not be invoked from inside a running loop.
    """
    monkeypatch.setattr("algorithms.registry.registry.implementation", lambda name: _FakePoolAlgo)
    result = dispatcher._run_algorithm_subprocess("pool_fake", _DummyInput(), _make_raw())
    assert isinstance(result, AlgorithmResult)


# ---------------------------------------------------------------------------
# _make_handler — spawning vs inline
# ---------------------------------------------------------------------------


async def test_handler_spawns_tracked_task_when_inflight_provided(monkeypatch):
    """With an inflight set, the callback returns immediately and the work
    runs as a tracked task that completes (and de-registers) afterwards."""
    started = asyncio.Event()

    async def _fake_handle(meta, msg, *, executor=None, semaphore=None):
        started.set()
        await msg.ack()

    monkeypatch.setattr(dispatcher, "_handle_message", _fake_handle)

    inflight: set[asyncio.Task] = set()
    handler = dispatcher._make_handler(_make_meta(), inflight=inflight)
    msg = FakeMsg(_event_bytes())

    await handler(msg)

    # Returned without waiting for the work: exactly one tracked task, and
    # _fake_handle hasn't necessarily run yet.
    assert len(inflight) == 1
    assert msg.acked is False

    await asyncio.gather(*list(inflight))
    await asyncio.sleep(0)  # let the done-callback run

    assert started.is_set()
    assert msg.acked is True
    assert len(inflight) == 0  # done-callback discarded the finished task


async def test_handler_runs_inline_when_no_inflight(monkeypatch):
    """Default (inflight=None) path processes to completion before returning —
    the shape the existing failure-path tests rely on."""
    calls: list[bool] = []

    async def _fake_handle(meta, msg, *, executor=None, semaphore=None):
        await msg.ack()
        calls.append(True)

    monkeypatch.setattr(dispatcher, "_handle_message", _fake_handle)

    handler = dispatcher._make_handler(_make_meta())
    msg = FakeMsg(_event_bytes())

    await handler(msg)

    assert calls == [True]
    assert msg.acked is True


# ---------------------------------------------------------------------------
# subscribe_algorithm — consumer config wiring
# ---------------------------------------------------------------------------


async def test_subscribe_sets_ack_wait_and_max_ack_pending():
    captured: dict = {}

    class _FakeJS:
        async def subscribe(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace()

    await dispatcher.subscribe_algorithm(_FakeJS(), _make_meta(), max_ack_pending=3)

    config = captured["config"]
    assert config.ack_wait == dispatcher._ACK_WAIT_SECONDS
    assert config.max_ack_pending == 3


async def test_subscribe_omits_max_ack_pending_when_unset():
    """Backwards-compatible default: no explicit max_ack_pending override."""
    captured: dict = {}

    class _FakeJS:
        async def subscribe(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace()

    await dispatcher.subscribe_algorithm(_FakeJS(), _make_meta())

    config = captured["config"]
    assert config.ack_wait == dispatcher._ACK_WAIT_SECONDS
    # Left at the ConsumerConfig default rather than forced to a value.
    default = ConsumerConfig(ack_wait=dispatcher._ACK_WAIT_SECONDS)
    assert config.max_ack_pending == default.max_ack_pending
