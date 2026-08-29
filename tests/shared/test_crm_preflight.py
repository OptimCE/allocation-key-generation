"""Unit tests for the CRM pre-flight verdict.

No database — ``evaluate`` is a pure function of a ``CrmDataSummary``. These
pin the classification the whole feature hangs on: which findings block a run
and which are only reported.
"""

import datetime

from shared.crm_meter_repository import CrmDataSummary, EanCoverage
from shared.crm_preflight import MAX_READING_ROWS, evaluate
from shared.custom_errors import errors


def _coverage(
    ean: str,
    *,
    row_count: int = 4,
    distinct_ts: int = 4,
    consumption_kwh: float = 10.0,
    injection_kwh: float = 0.0,
) -> EanCoverage:
    return EanCoverage(
        ean=ean,
        row_count=row_count,
        distinct_ts=distinct_ts,
        consumption_kwh=consumption_kwh,
        injection_kwh=injection_kwh,
    )


def _summary(eans: list[EanCoverage], *, grid_size: int = 4) -> CrmDataSummary:
    return CrmDataSummary(
        eans=eans,
        grid_size=grid_size,
        first_timestamp=datetime.datetime(2025, 2, 1, tzinfo=datetime.UTC),
        last_timestamp=datetime.datetime(2025, 2, 28, tzinfo=datetime.UTC),
    )


def _codes(preflight) -> set[int]:
    return {b.error.code for b in preflight.blockers}


# ---------------------------------------------------------------------------
# 1. The happy path
# ---------------------------------------------------------------------------


def test_complete_period_with_consumption_and_injection_is_accepted():
    result = evaluate(
        _summary([_coverage("A"), _coverage("PV", consumption_kwh=0.0, injection_kwh=50.0)])
    )
    assert result.ok
    assert result.warnings is None
    # The injection-only meter is not a participant.
    assert result.consumer_eans == ["A"]


# ---------------------------------------------------------------------------
# 2. Blocking findings
# ---------------------------------------------------------------------------


def test_empty_period_blocks_with_a_single_message():
    result = evaluate(_summary([], grid_size=0))
    # One clear cause, not four derived ones.
    assert _codes(result) == {errors.generation.CRM_NO_DATA.code}


def test_duplicate_readings_block():
    # row_count > distinct_ts means the same (ean, timestamp) landed twice,
    # which would double this meter's energy with no visible symptom.
    result = evaluate(
        _summary(
            [
                _coverage("A", row_count=8, distinct_ts=4, injection_kwh=1.0),
            ]
        )
    )
    assert errors.generation.CRM_DUPLICATE_READINGS.code in _codes(result)
    assert "A" in result.blockers[0].detail


def test_no_injection_blocks():
    result = evaluate(_summary([_coverage("A", injection_kwh=0.0)]))
    assert errors.generation.CRM_NO_INJECTION.code in _codes(result)


def test_no_consuming_meter_blocks():
    result = evaluate(_summary([_coverage("PV", consumption_kwh=0.0, injection_kwh=50.0)]))
    assert errors.generation.CRM_NO_CONSUMERS.code in _codes(result)


def test_oversized_period_blocks():
    result = evaluate(
        _summary(
            [_coverage("A", row_count=MAX_READING_ROWS + 1, distinct_ts=MAX_READING_ROWS + 1,
                       injection_kwh=1.0)],
            grid_size=MAX_READING_ROWS + 1,
        )
    )
    assert errors.generation.CRM_RANGE_TOO_LARGE.code in _codes(result)


# ---------------------------------------------------------------------------
# 3. Gaps warn but do not block — the behaviour the user chose
# ---------------------------------------------------------------------------


def test_gaps_warn_but_do_not_block():
    result = evaluate(
        _summary(
            [
                _coverage("A", injection_kwh=10.0),
                _coverage("B", row_count=2, distinct_ts=2),
            ],
            grid_size=4,
        )
    )
    assert result.ok, "a gap must not stop the run"
    assert result.warnings is not None
    incomplete = result.warnings["incomplete_meters"]
    assert [m["ean"] for m in incomplete] == ["B"]
    assert incomplete[0] == {"ean": "B", "readings": 2, "expected": 4, "missing": 2}
    # The incomplete meter is still a participant; its gaps get zero-filled.
    assert "B" in result.consumer_eans
