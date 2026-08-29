"""Worker tests for the CRM-sourced branch of ``_process``.

Same style as test_dispatcher_failure_paths.py: drive ``_load_from_crm``
directly with patched collaborators, so no live NATS, Postgres or MinIO is
needed.

What matters here is the failure classification, which differs from the file
path in one respect that is easy to get wrong: a CRM **read** error must be
transient (NAK, redeliver) while **rejected data** must be deterministic
(FAILED, ack). Getting that backwards either burns a run on a blip or hot-loops
the queue on a permanent data problem.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.const import DataSource
from shared.crm_meter_repository import ConsumptionRow, CrmDataSummary, EanCoverage
from worker import dispatcher

_TZ = datetime.UTC


def _snapshot(
    *,
    generation_id: int = 1,
    source: DataSource = DataSource.CRM,
    id_sharing_operation: int | None = 7,
    period_start: datetime.date | None = datetime.date(2025, 2, 1),
    period_end: datetime.date | None = datetime.date(2025, 2, 28),
) -> dispatcher._GenerationSnapshot:
    return dispatcher._GenerationSnapshot(
        id=generation_id,
        source=source,
        file_storage_key=None,
        file_name=None,
        injection_name=None,
        id_sharing_operation=id_sharing_operation,
        period_start=period_start,
        period_end=period_end,
        inputs={"value": 1},
        id_community=42,
        status=0,
    )


def _coverage(ean: str, *, consumption: float, injection: float, dup: bool = False) -> EanCoverage:
    return EanCoverage(
        ean=ean,
        row_count=4 if not dup else 8,
        distinct_ts=4,
        consumption_kwh=consumption,
        injection_kwh=injection,
    )


def _summary(eans: list[EanCoverage]) -> CrmDataSummary:
    return CrmDataSummary(
        eans=eans,
        grid_size=4,
        first_timestamp=datetime.datetime(2025, 2, 1, tzinfo=_TZ),
        last_timestamp=datetime.datetime(2025, 2, 1, 0, 45, tzinfo=_TZ),
    )


def _rows() -> list[ConsumptionRow]:
    base = datetime.datetime(2025, 2, 1, tzinfo=_TZ)
    out: list[ConsumptionRow] = []
    for i in range(4):
        ts = base + datetime.timedelta(minutes=15 * i)
        out.append(ConsumptionRow(timestamp=ts, ean="A", gross=10.0, inj_gross=0.0))
        out.append(ConsumptionRow(timestamp=ts, ean="PV", gross=0.0, inj_gross=25.0))
    return out


@pytest.fixture
def patched_save(monkeypatch):
    save_failure = AsyncMock()
    monkeypatch.setattr(dispatcher.persistence, "save_failure", save_failure)
    return save_failure


def _patch_crm(monkeypatch, *, summary, rows=None, raises: Exception | None = None):
    """Patch AsyncSessionCRMFactory + CrmMeterRepository on the dispatcher."""
    repository = MagicMock()
    if raises is not None:
        repository.summarize = AsyncMock(side_effect=raises)
    else:
        repository.summarize = AsyncMock(return_value=summary)
    repository.fetch_rows = AsyncMock(return_value=rows or [])

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(dispatcher, "AsyncSessionCRMFactory", MagicMock(return_value=session_cm))
    monkeypatch.setattr(dispatcher, "CrmMeterRepository", MagicMock(return_value=repository))
    return repository


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


async def test_returns_raw_data_built_from_crm_rows(monkeypatch, patched_save):
    repository = _patch_crm(
        monkeypatch,
        summary=_summary(
            [
                _coverage("A", consumption=40.0, injection=0.0),
                _coverage("PV", consumption=0.0, injection=100.0),
            ]
        ),
        rows=_rows(),
    )

    result = await dispatcher._load_from_crm(_snapshot())

    assert not isinstance(result, dispatcher._Terminal)
    assert result.consumer_names == ["A"]
    assert result.C.shape == (1, 4)
    # VA carries the PV site's production even though it is not a participant.
    assert result.VA.tolist() == [[25.0, 25.0, 25.0, 25.0]]
    patched_save.assert_not_awaited()
    # The community is passed explicitly — the worker has no ContextVar.
    assert repository.summarize.await_args.kwargs["id_community"] == 42
    assert repository.summarize.await_args.kwargs["id_sharing_operation"] == 7


# ---------------------------------------------------------------------------
# 2. Transient: the CRM is unreachable
# ---------------------------------------------------------------------------


async def test_crm_read_failure_is_transient(monkeypatch, patched_save):
    _patch_crm(monkeypatch, summary=None, raises=OSError("connection reset"))

    with pytest.raises(dispatcher._TransientError, match="crm read"):
        await dispatcher._load_from_crm(_snapshot())

    # A blip must not burn the run.
    patched_save.assert_not_awaited()


# ---------------------------------------------------------------------------
# 3. Deterministic: the data itself is unusable
# ---------------------------------------------------------------------------


async def test_empty_period_fails_deterministically(monkeypatch, patched_save):
    repository = _patch_crm(monkeypatch, summary=_summary([]))

    result = await dispatcher._load_from_crm(_snapshot())

    assert isinstance(result, dispatcher._Terminal)
    # Nothing to clean up — there is no uploaded object on this path.
    assert result.storage_key is None
    patched_save.assert_awaited_once()
    assert "crm_data_rejected" in patched_save.await_args.args[1]
    # The expensive read is skipped once the period is already rejected.
    repository.fetch_rows.assert_not_awaited()


async def test_duplicate_readings_fail_deterministically(monkeypatch, patched_save):
    _patch_crm(
        monkeypatch,
        summary=_summary([_coverage("A", consumption=40.0, injection=10.0, dup=True)]),
    )

    result = await dispatcher._load_from_crm(_snapshot())

    assert isinstance(result, dispatcher._Terminal)
    detail = patched_save.await_args.args[1]
    assert "crm_data_rejected" in detail
    assert "imported twice" in detail


async def test_no_injection_fails_deterministically(monkeypatch, patched_save):
    _patch_crm(monkeypatch, summary=_summary([_coverage("A", consumption=40.0, injection=0.0)]))

    result = await dispatcher._load_from_crm(_snapshot())

    assert isinstance(result, dispatcher._Terminal)
    assert "nothing to share" in patched_save.await_args.args[1]


async def test_incomplete_crm_columns_fail_deterministically(monkeypatch, patched_save):
    # ck_generation_source makes this unreachable via the API, but a row written
    # straight to the DB could still land here.
    _patch_crm(monkeypatch, summary=_summary([]))

    result = await dispatcher._load_from_crm(_snapshot(id_sharing_operation=None))

    assert isinstance(result, dispatcher._Terminal)
    patched_save.assert_awaited_once_with(1, "crm_source_incomplete")


# ---------------------------------------------------------------------------
# 4. Gaps still run — the user-chosen behaviour, verified end to end
# ---------------------------------------------------------------------------


async def test_gaps_do_not_stop_the_run_and_are_zero_filled(monkeypatch, patched_save):
    # B is missing the last two quarters; the grid stays 4 long.
    rows = _rows()
    rows = [r for r in rows if not (r.ean == "A" and r.timestamp.minute >= 30)]
    _patch_crm(
        monkeypatch,
        summary=_summary(
            [
                EanCoverage(
                    "A", row_count=2, distinct_ts=2, consumption_kwh=20.0, injection_kwh=0.0
                ),
                _coverage("PV", consumption=0.0, injection=100.0),
            ]
        ),
        rows=rows,
    )

    result = await dispatcher._load_from_crm(_snapshot())

    assert not isinstance(result, dispatcher._Terminal)
    assert result.C.tolist() == [[10.0, 10.0, 0.0, 0.0]]
    patched_save.assert_not_awaited()
