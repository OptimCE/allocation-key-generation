"""Tests for the solver process-pool offload added to the worker.

These cover the moving parts introduced to keep CPU-bound solves off the
event loop (so NATS PING/PONG + the liveness heartbeat never starve) and to
run several generations in parallel:

* ``worker.main._detect_available_cpus`` / ``_solver_pool_size`` — sizing the
  pool from the CPUs the container is actually allowed to use (cgroup quota),
  not the host core count.
* ``worker.main._init_solver_process`` — each solver process raises its own
  OOM score, so the kernel kills it rather than the main process.
* ``worker.dispatcher._run_algorithm`` / ``_run_algorithm_subprocess`` — the
  inline-vs-executor branch and the picklable subprocess entry point.
* ``worker.dispatcher._make_handler`` spawning path — when an ``inflight`` set
  is provided the callback returns immediately and the work runs as a tracked
  background task (inline-to-completion when it is not).

A ThreadPoolExecutor stands in for the ProcessPoolExecutor so the tests stay
fast and cross-platform (no spawn/pickle round-trip); picklability of the
payloads is asserted separately. Only the Linux OOM-score test starts a real
solver process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pickle
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from pydantic import Field

from algorithms.base import (
    AlgorithmInput,
    AlgorithmMetadata,
    AlgorithmRawData,
    AlgorithmResult,
)
from core.queue.helper import Event
from tests.fake_jetstream import FakeJetStream
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
# The solver process volunteers for the OOM killer
# ---------------------------------------------------------------------------


def _worker_main_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == worker_main.__name__]


def test_solver_process_raises_its_oom_score(monkeypatch, tmp_path, caplog):
    score = tmp_path / "oom_score_adj"
    score.write_text("0\n")
    monkeypatch.setattr(worker_main, "_OOM_SCORE_ADJ_PATH", score)

    with caplog.at_level(logging.DEBUG, logger=worker_main.__name__):
        worker_main._init_solver_process()

    assert score.read_text() == "1000\n"
    assert _worker_main_records(caplog) == []


def test_solver_process_starts_where_there_is_no_oom_score(monkeypatch, tmp_path, caplog):
    """Dev on Windows or macOS: no /proc, so nothing to warn about."""
    monkeypatch.setattr(worker_main, "_OOM_SCORE_ADJ_PATH", tmp_path / "no-proc" / "oom_score_adj")
    monkeypatch.setattr(worker_main, "_OOM_SCORE_ADJ_EXPECTED", False)

    with caplog.at_level(logging.DEBUG, logger=worker_main.__name__):
        worker_main._init_solver_process()

    [record] = _worker_main_records(caplog)
    assert record.levelno == logging.DEBUG


def test_on_linux_a_solver_that_cannot_raise_its_oom_score_warns_once(
    monkeypatch, tmp_path, caplog
):
    """The process still starts, but an OOM kill may now take the main process,
    costing a whole run per delivery up to the delivery limit: worth one
    warning."""
    monkeypatch.setattr(worker_main, "_OOM_SCORE_ADJ_PATH", tmp_path / "no-proc" / "oom_score_adj")
    monkeypatch.setattr(worker_main, "_OOM_SCORE_ADJ_EXPECTED", True)

    with caplog.at_level(logging.DEBUG, logger=worker_main.__name__):
        worker_main._init_solver_process()

    [record] = _worker_main_records(caplog)
    assert record.levelno == logging.WARNING
    assert str(os.getpid()) in record.getMessage()
    assert "main process" in record.getMessage()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc/<pid>/oom_score_adj")
def test_on_linux_only_the_solver_processes_volunteer():
    pool = worker_main._SolverPool(1, worker_main._init_solver_process)
    try:
        solver_pid = pool.submit(os.getpid).result(timeout=60)
        with open(f"/proc/{solver_pid}/oom_score_adj") as solver:
            assert solver.read().strip() == "1000"
        with open("/proc/self/oom_score_adj") as main:
            assert main.read().strip() != "1000"
    finally:
        pool.shutdown(wait=True)


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


def _algorithms_js() -> FakeJetStream:
    return FakeJetStream({"ALGORITHMS": ["optimce.allocation.>"]})


async def test_subscribe_sets_ack_wait_max_deliver_and_max_ack_pending():
    js = _algorithms_js()
    meta = _make_meta()

    await dispatcher.subscribe_algorithm(js, meta, max_ack_pending=3)

    config = js.stored(meta.queue, f"worker-{meta.name}")
    assert config.ack_wait == dispatcher._ACK_WAIT_SECONDS
    assert config.max_deliver == dispatcher._MAX_DELIVER
    assert config.max_ack_pending == 3


async def test_subscribe_caps_the_deliveries_of_a_durable_an_earlier_build_created():
    """The deployed durables have max_deliver -1 (unlimited), the broker's
    default: the backstop has to reach them in place."""
    js = _algorithms_js()
    meta = _make_meta()
    js.seed(meta.queue, f"worker-{meta.name}", ack_wait=float(dispatcher._ACK_WAIT_SECONDS))
    assert js.stored(meta.queue, f"worker-{meta.name}").max_deliver == -1

    await dispatcher.subscribe_algorithm(js, meta, max_ack_pending=1)

    assert js.stored(meta.queue, f"worker-{meta.name}").max_deliver == dispatcher._MAX_DELIVER


async def test_subscribe_updates_a_durable_an_earlier_build_created():
    """The dev durables created on 2026-05-11 still had max_ack_pending=1000."""
    js = _algorithms_js()
    meta = _make_meta()
    js.seed(meta.queue, f"worker-{meta.name}", ack_wait=120.0)

    await dispatcher.subscribe_algorithm(js, meta, max_ack_pending=1)

    config = js.stored(meta.queue, f"worker-{meta.name}")
    assert config.ack_wait == dispatcher._ACK_WAIT_SECONDS
    assert config.max_ack_pending == 1


async def test_subscribe_omits_max_ack_pending_when_unset():
    """Without an explicit max_ack_pending the durable keeps the broker's value."""
    js = _algorithms_js()
    meta = _make_meta()
    js.seed(meta.queue, f"worker-{meta.name}", max_ack_pending=7)

    await dispatcher.subscribe_algorithm(js, meta)

    config = js.stored(meta.queue, f"worker-{meta.name}")
    assert config.ack_wait == dispatcher._ACK_WAIT_SECONDS
    assert config.max_ack_pending == 7
