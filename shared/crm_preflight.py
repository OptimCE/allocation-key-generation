"""Decide whether a CRM-sourced period can produce a meaningful allocation key.

One definition of "blocking", used in three places: the preview endpoint (so the
manager sees the problem before launching), ``POST /from-crm`` (so a stale
preview cannot slip a bad run through), and the worker (which re-reads the data
at execution time and must reach the same verdict).

Framework-free and pandas-free on purpose — the API, which has neither pandas
nor a request-scoped community, imports this too.
"""

from dataclasses import dataclass
from typing import Any

from core.errors.errors import Error
from shared.crm_meter_repository import CrmDataSummary
from shared.custom_errors import errors

# A year of quarter-hours across ~60 meters is ~2.1 M rows, which the worker
# pivots comfortably. Well past that we would rather refuse than risk the
# worker being OOM-killed mid-run, which reads to the manager as a silent hang.
MAX_READING_ROWS = 5_000_000


@dataclass(frozen=True)
class Blocker:
    """A reason this period cannot be used, with the detail the manager needs."""

    error: Error
    detail: str


@dataclass(frozen=True)
class Preflight:
    summary: CrmDataSummary
    consumer_eans: list[str]
    blockers: list[Blocker]
    warnings: dict[str, Any] | None

    @property
    def ok(self) -> bool:
        return not self.blockers


def evaluate(summary: CrmDataSummary) -> Preflight:
    """Classify a period's metering data into blockers and warnings.

    Gaps are deliberately **not** blocking: missing quarters are zero-filled and
    reported. Duplicates are, because there is no unique constraint on
    ``meter_consumption(ean, timestamp)`` and a repeated import would inflate a
    participant's share with no visible symptom.
    """
    blockers: list[Blocker] = []

    if not summary.eans:
        # Nothing else can be said about an empty period; return early so the
        # manager gets one clear message rather than four derived ones.
        return Preflight(
            summary=summary,
            consumer_eans=[],
            blockers=[
                Blocker(
                    error=errors.generation.CRM_NO_DATA,
                    detail="no readings for this sharing operation over this period",
                )
            ],
            warnings=None,
        )

    if summary.total_rows > MAX_READING_ROWS:
        blockers.append(
            Blocker(
                error=errors.generation.CRM_RANGE_TOO_LARGE,
                detail=(
                    f"{summary.total_rows} readings exceed the {MAX_READING_ROWS} limit; "
                    "choose a shorter period"
                ),
            )
        )

    duplicates = summary.duplicate_eans
    if duplicates:
        blockers.append(
            Blocker(
                error=errors.generation.CRM_DUPLICATE_READINGS,
                detail=(
                    "the same timestamp appears more than once for meter(s) "
                    f"{', '.join(duplicates)} — the data was most likely imported twice"
                ),
            )
        )

    consumer_eans = summary.consumer_eans
    if not consumer_eans:
        blockers.append(
            Blocker(
                error=errors.generation.CRM_NO_CONSUMERS,
                detail="no meter drew any energy over this period",
            )
        )

    if summary.total_injection_kwh <= 0:
        blockers.append(
            Blocker(
                error=errors.generation.CRM_NO_INJECTION,
                detail="no energy was injected over this period — there is nothing to share",
            )
        )

    incomplete = summary.incomplete
    warnings: dict[str, Any] | None = None
    if incomplete:
        warnings = {
            "incomplete_meters": [
                {
                    "ean": e.ean,
                    "readings": e.distinct_ts,
                    "expected": summary.grid_size,
                    "missing": summary.grid_size - e.distinct_ts,
                }
                for e in incomplete
            ]
        }

    return Preflight(
        summary=summary,
        consumer_eans=consumer_eans,
        blockers=blockers,
        warnings=warnings,
    )
