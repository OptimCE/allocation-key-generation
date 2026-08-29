"""Integration tests for the CRM-sourced generation routes.

Full ASGI stack against a real Postgres, same conventions as
test_generation_routes.py: gateway headers rather than dependency overrides,
NATS patched at the import site in the service module, and an active
`algorithm` subscription on every request.

Nothing here touches MinIO — that is the point of the CRM source.
"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from core.database.models import Community
from shared.const import DataSource, GenerationStatus
from shared.custom_errors import errors
from shared.models.local_models import GenerationModel
from tests.factories.meter_factory import (
    create_meter,
    create_readings,
    create_sharing_operation,
)
from tests.factories.subscription_factory import create_community, create_subscription

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PERIOD = {"period_start": "2025-02-01", "period_end": "2025-02-28"}


def _admin_headers(community: Community) -> dict[str, str]:
    return {
        "x-user-id": "test|admin",
        "x-community-id": community.auth_community_id,
        "x-user-role": "ADMIN",
    }


async def _community_with_subscription(db_session) -> Community:
    community = await create_community(db_session)
    await create_subscription(db_session, id_community=community.id, is_active=True)
    return community


async def _operation_with_data(
    db_session,
    community: Community,
    *,
    consumer_skip: set[int] | None = None,
    duplicate: bool = False,
    injection: float = 40.0,
) -> int:
    """One consuming meter + one PV meter, four quarter-hours in Feb 2025."""
    op = await create_sharing_operation(db_session, id_community=community.id)
    await create_meter(db_session, ean="541448000000000001", id_community=community.id)
    await create_meter(db_session, ean="541448000000000002", id_community=community.id)

    await create_readings(
        db_session,
        ean="541448000000000001",
        id_community=community.id,
        id_sharing_operation=op,
        gross=10.0,
        inj_gross=0.0,
        skip=consumer_skip,
    )
    if duplicate:
        # A second import of the same quarters — the exact shape that has no
        # unique constraint to stop it in production.
        await create_readings(
            db_session,
            ean="541448000000000001",
            id_community=community.id,
            id_sharing_operation=op,
            gross=10.0,
            inj_gross=0.0,
            skip=consumer_skip,
        )
    await create_readings(
        db_session,
        ean="541448000000000002",
        id_community=community.id,
        id_sharing_operation=op,
        gross=0.0,
        inj_gross=injection,
    )
    return op


def _from_crm_body(op: int, **overrides) -> dict:
    body = {
        "name": "february run",
        "algorithm_name": "brute_force",
        "inputs": {"iterations": 1},
        "id_sharing_operation": op,
        **_PERIOD,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# 1. GET /crm-data-preview
# ---------------------------------------------------------------------------


async def test_preview_reports_participants_and_totals(client, db_session):
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community)

    response = await client.get(
        "/crm-data-preview",
        params={"id_sharing_operation": op, **_PERIOD},
        headers=_admin_headers(community),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["can_generate"] is True
    # Only the drawing meter is a participant; the PV site just contributes.
    assert data["meter_count"] == 1
    assert data["reading_count"] == 8
    assert data["total_consumption_kwh"] == 40.0
    assert data["total_injection_kwh"] == 160.0
    assert data["blockers"] == []
    assert data["incomplete_meters"] == []


async def test_preview_is_reachable_and_not_swallowed_by_the_id_route(client, db_session):
    # `GET /{id}` is declared after this route; if the order regressed, the
    # path would be parsed as an integer id and 422.
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community)

    response = await client.get(
        "/crm-data-preview",
        params={"id_sharing_operation": op, **_PERIOD},
        headers=_admin_headers(community),
    )

    assert response.status_code == 200


async def test_preview_reports_gaps_as_warnings_without_blocking(client, db_session):
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community, consumer_skip={1, 2})

    response = await client.get(
        "/crm-data-preview",
        params={"id_sharing_operation": op, **_PERIOD},
        headers=_admin_headers(community),
    )

    data = response.json()["data"]
    assert data["can_generate"] is True, "a gap must warn, not block"
    assert data["incomplete_meters"] == [
        {"ean": "541448000000000001", "readings": 2, "expected": 4, "missing": 2}
    ]


async def test_preview_blocks_on_duplicate_readings(client, db_session):
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community, duplicate=True)

    response = await client.get(
        "/crm-data-preview",
        params={"id_sharing_operation": op, **_PERIOD},
        headers=_admin_headers(community),
    )

    data = response.json()["data"]
    assert data["can_generate"] is False
    codes = {b["error_code"] for b in data["blockers"]}
    assert errors.generation.CRM_DUPLICATE_READINGS.code in codes
    # The message is localised, not a bare translation key.
    blocker = next(b for b in data["blockers"] if b["error_code"] in codes)
    assert not blocker["message"].startswith("ERRORS.")


async def test_preview_blocks_on_empty_period(client, db_session):
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community)

    response = await client.get(
        "/crm-data-preview",
        params={
            "id_sharing_operation": op,
            "period_start": "2024-01-01",
            "period_end": "2024-01-31",
        },
        headers=_admin_headers(community),
    )

    data = response.json()["data"]
    assert data["can_generate"] is False
    assert [b["error_code"] for b in data["blockers"]] == [errors.generation.CRM_NO_DATA.code]


async def test_preview_rejects_inverted_period(client, db_session):
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community)

    response = await client.get(
        "/crm-data-preview",
        params={
            "id_sharing_operation": op,
            "period_start": "2025-02-28",
            "period_end": "2025-02-01",
        },
        headers=_admin_headers(community),
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == errors.generation.INVALID_PERIOD.code


async def test_preview_cannot_reach_another_communitys_operation(client, db_session):
    owner = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, owner)
    intruder = await _community_with_subscription(db_session)

    response = await client.get(
        "/crm-data-preview",
        params={"id_sharing_operation": op, **_PERIOD},
        headers=_admin_headers(intruder),
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == errors.generation.SHARING_OPERATION_NOT_FOUND.code


# ---------------------------------------------------------------------------
# 2. POST /from-crm
# ---------------------------------------------------------------------------


@patch("api.generation.service.get_jetstream", MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_from_crm_persists_a_crm_sourced_row(send_event, client, db_session):
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community)

    response = await client.post(
        "/from-crm", json=_from_crm_body(op), headers=_admin_headers(community)
    )

    assert response.status_code == 200, response.text
    generation_id = response.json()["data"]["id"]
    assert response.json()["data"]["status"] == GenerationStatus.PENDING

    row = (
        await db_session.execute(select(GenerationModel).where(GenerationModel.id == generation_id))
    ).scalar_one()
    assert row.source == DataSource.CRM
    assert row.id_sharing_operation == op
    assert row.period_start == datetime.date(2025, 2, 1)
    assert row.period_end == datetime.date(2025, 2, 28)
    # No file was uploaded, so the file columns stay empty — which the
    # ck_generation_source CHECK only permits for source = CRM.
    assert row.file_storage_key is None
    assert row.file_name is None
    assert row.injection_name is None
    assert row.data_warnings is None
    send_event.assert_awaited_once()


@patch("api.generation.service.get_jetstream", MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_from_crm_persists_gap_warnings(send_event, client, db_session):
    # "Warn but allow" is only honest if the warning outlives the preview.
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community, consumer_skip={1})

    response = await client.post(
        "/from-crm", json=_from_crm_body(op), headers=_admin_headers(community)
    )

    assert response.status_code == 200, response.text
    row = (
        await db_session.execute(
            select(GenerationModel).where(GenerationModel.id == response.json()["data"]["id"])
        )
    ).scalar_one()
    assert row.data_warnings == {
        "incomplete_meters": [
            {"ean": "541448000000000001", "readings": 3, "expected": 4, "missing": 1}
        ]
    }


@patch("api.generation.service.get_jetstream", MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_from_crm_refuses_duplicate_readings(send_event, client, db_session):
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community, duplicate=True)

    response = await client.post(
        "/from-crm", json=_from_crm_body(op), headers=_admin_headers(community)
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == errors.generation.CRM_DUPLICATE_READINGS.code
    send_event.assert_not_awaited()


@patch("api.generation.service.get_jetstream", MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_from_crm_refuses_a_period_with_no_injection(send_event, client, db_session):
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community, injection=0.0)

    response = await client.post(
        "/from-crm", json=_from_crm_body(op), headers=_admin_headers(community)
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == errors.generation.CRM_NO_INJECTION.code
    send_event.assert_not_awaited()


@patch("api.generation.service.get_jetstream", MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_from_crm_cannot_use_another_communitys_operation(
    send_event, client, db_session
):
    owner = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, owner)
    intruder = await _community_with_subscription(db_session)

    response = await client.post(
        "/from-crm", json=_from_crm_body(op), headers=_admin_headers(intruder)
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == errors.generation.SHARING_OPERATION_NOT_FOUND.code
    send_event.assert_not_awaited()


@patch("api.generation.service.get_jetstream", MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_from_crm_rejects_an_unknown_algorithm(send_event, client, db_session):
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community)

    response = await client.post(
        "/from-crm",
        json=_from_crm_body(op, algorithm_name="does_not_exist"),
        headers=_admin_headers(community),
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == errors.generation.ALGORITHM_NOT_FOUND.code


@patch("api.generation.service.get_jetstream", MagicMock())
@patch("api.generation.service.send_event", new_callable=AsyncMock)
async def test_start_from_crm_rejects_invalid_algorithm_inputs(send_event, client, db_session):
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community)

    response = await client.post(
        "/from-crm",
        json=_from_crm_body(op, inputs={"iterations": 99}),
        headers=_admin_headers(community),
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == errors.generation.INVALID_ALGORITHM_INPUTS.code


async def test_start_from_crm_body_is_json_not_query_params(client, db_session):
    # Guards the with_default_error / `from __future__ import annotations` trap:
    # if the route module ever gains that import, the Pydantic body is demoted
    # to query params and every well-formed request 422s with loc=[query, body].
    community = await _community_with_subscription(db_session)
    op = await _operation_with_data(db_session, community)

    response = await client.post(
        "/from-crm", json=_from_crm_body(op), headers=_admin_headers(community)
    )

    assert response.status_code != 422, response.text
