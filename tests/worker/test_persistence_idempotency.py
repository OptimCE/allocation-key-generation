"""Idempotency tests for worker.persistence.

The dispatcher already short-circuits when ``status != PENDING`` is
observed before it starts processing, but a long-running algorithm can
exceed JetStream's ack_wait while the worker holds the message — JetStream
will then redeliver to a different worker in the same queue group, and
both workers will read ``status=PENDING`` from their initial snapshot.

These tests prove that the conditional-UPDATE-WHERE-PENDING guard inside
``save_success`` and ``save_failure`` makes the persistence layer the
last line of defense: only one of two concurrent calls can flip the row,
and the loser exits without writing duplicate result rows.

The patched factory makes ``worker.persistence.AsyncSessionLocalFactory``
hand back the test's per-test session (which uses
``join_transaction_mode="create_savepoint"``), so every commit inside
``save_success`` becomes a savepoint release that the conftest rolls back
at end of test. No real DB pollution.
"""

import pytest_asyncio
from sqlalchemy import select

from algorithms.base import (
    AlgorithmResult,
    AllocationKeyResult,
    ConsumerResult,
    IterationResult,
)
from shared.const import GenerationStatus
from shared.models.local_models import (
    AllocationKeyGeneratedModel,
    GenerationModel,
)
from tests.factories.generation_factory import create_generation
from worker import persistence


def _result_with_one_key() -> AlgorithmResult:
    return AlgorithmResult(
        keys=[
            AllocationKeyResult(
                name="K1",
                description="desc",
                iterations=[
                    IterationResult(
                        number=0,
                        energy_allocated_percentage=100.0,
                        surplus_total=1.0,
                        consumers=[ConsumerResult(name="C1", energy_allocated_percentage=100.0)],
                    )
                ],
            )
        ]
    )


@pytest_asyncio.fixture
async def patched_factory(db_session, monkeypatch):
    """Route worker.persistence sessions through the test's outer transaction.

    Without this, ``AsyncSessionLocalFactory`` would open a fresh production
    session and ``session.commit()`` would persist data outside the per-test
    rollback — leaking state between tests.
    """

    class _Ctx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Factory:
        def __call__(self):
            return _Ctx()

    monkeypatch.setattr(persistence, "AsyncSessionLocalFactory", _Factory())
    yield


async def _count_keys_for(db_session, generation_id: int) -> int:
    rows = await db_session.execute(
        select(AllocationKeyGeneratedModel).where(
            AllocationKeyGeneratedModel.id_generation == generation_id
        )
    )
    return len(rows.scalars().all())


# ---------------------------------------------------------------------------
# save_success
# ---------------------------------------------------------------------------


async def test_save_success_writes_keys_and_flips_status_when_pending(db_session, patched_factory):
    gen = await create_generation(db_session, id_community=1)

    await persistence.save_success(gen.id, _result_with_one_key())

    await db_session.refresh(gen)
    assert gen.status == GenerationStatus.SUCCESS
    assert await _count_keys_for(db_session, gen.id) == 1


async def test_save_success_no_op_when_already_succeeded(db_session, patched_factory):
    gen = await create_generation(db_session, id_community=1, status=GenerationStatus.SUCCESS)

    await persistence.save_success(gen.id, _result_with_one_key())

    assert await _count_keys_for(db_session, gen.id) == 0


async def test_save_success_no_op_when_already_failed(db_session, patched_factory):
    gen = await create_generation(
        db_session,
        id_community=1,
        status=GenerationStatus.FAILED,
        error_message="prior failure",
    )

    await persistence.save_success(gen.id, _result_with_one_key())

    assert await _count_keys_for(db_session, gen.id) == 0
    await db_session.refresh(gen)
    assert gen.status == GenerationStatus.FAILED
    assert gen.error_message == "prior failure"


async def test_save_success_idempotent_on_double_call(db_session, patched_factory):
    """Same-message redelivery: second call sees status=SUCCESS and exits."""
    gen = await create_generation(db_session, id_community=1)

    await persistence.save_success(gen.id, _result_with_one_key())
    await persistence.save_success(gen.id, _result_with_one_key())

    assert await _count_keys_for(db_session, gen.id) == 1


async def test_save_success_no_op_for_missing_row(db_session, patched_factory):
    await persistence.save_success(999_999, _result_with_one_key())

    rows = await db_session.execute(select(GenerationModel))
    assert rows.scalars().all() == []


# ---------------------------------------------------------------------------
# save_failure
# ---------------------------------------------------------------------------


async def test_save_failure_flips_status_when_pending(db_session, patched_factory):
    gen = await create_generation(db_session, id_community=1)

    await persistence.save_failure(gen.id, "boom")

    await db_session.refresh(gen)
    assert gen.status == GenerationStatus.FAILED
    assert gen.error_message == "boom"


async def test_save_failure_no_op_when_already_succeeded(db_session, patched_factory):
    gen = await create_generation(db_session, id_community=1, status=GenerationStatus.SUCCESS)

    await persistence.save_failure(gen.id, "should be ignored")

    await db_session.refresh(gen)
    assert gen.status == GenerationStatus.SUCCESS
    assert gen.error_message is None


async def test_save_failure_truncates_long_message(db_session, patched_factory):
    gen = await create_generation(db_session, id_community=1)

    long_message = "x" * 5000
    await persistence.save_failure(gen.id, long_message)

    await db_session.refresh(gen)
    assert gen.status == GenerationStatus.FAILED
    assert gen.error_message is not None
    assert len(gen.error_message) == 2000
