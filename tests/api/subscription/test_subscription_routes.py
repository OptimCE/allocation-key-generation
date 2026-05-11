"""
Integration tests for api/subscription/routes.py.

These tests run the full ASGI stack against a real Postgres. Auth is done by
sending the same headers KrakenD would forward in production
(x-user-id, x-community-id, x-user-role); GatewayScopeMiddleware reads them
into ContextVars and the per-route require_min_role(ADMIN) dependency picks
them up. resolve_internal_community then resolves the auth community id to
the internal community.id row created by the factory.
"""

from sqlalchemy import select

from core.database.models import Community, CommunitySubscription
from tests.factories.subscription_factory import (
    create_community,
    create_subscription,
)


def _admin_headers(community: Community) -> dict[str, str]:
    return {
        "x-user-id": "test|admin",
        "x-community-id": community.auth_community_id,
        "x-user-role": "ADMIN",
    }


async def _fetch_subscription(
    db_session, id_community: int
) -> CommunitySubscription | None:
    stmt = select(CommunitySubscription).where(
        CommunitySubscription.id_community == id_community
    )
    result = await db_session.execute(stmt)
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# subscribe
# ---------------------------------------------------------------------------


async def test_subscribe_creates_new_subscription_returns_200(client, db_session):
    community = await create_community(db_session)

    response = await client.get(
        "/subscribe",
        params={"feature": "algorithm"},
        headers=_admin_headers(community),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error_code"] == 0
    assert body["data"] == "success"

    row = await _fetch_subscription(db_session, community.id)
    assert row is not None
    assert row.is_active is True
    assert row.feature == "algorithm"


async def test_subscribe_reactivates_inactive_subscription_returns_200(
    client, db_session
):
    community = await create_community(db_session)
    sub = await create_subscription(
        db_session, id_community=community.id, is_active=False
    )

    response = await client.get(
        "/subscribe",
        params={"feature": "algorithm"},
        headers=_admin_headers(community),
    )

    assert response.status_code == 200
    assert response.json()["error_code"] == 0

    await db_session.refresh(sub)
    assert sub.is_active is True


async def test_subscribe_already_active_returns_error_code_1002(client, db_session):
    community = await create_community(db_session)
    await create_subscription(db_session, id_community=community.id, is_active=True)

    response = await client.get(
        "/subscribe",
        params={"feature": "algorithm"},
        headers=_admin_headers(community),
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == 1002  # ALREADY_SUBSCRIBED


# ---------------------------------------------------------------------------
# unsubscribe
# ---------------------------------------------------------------------------


async def test_unsubscribe_active_subscription_returns_200(client, db_session):
    community = await create_community(db_session)
    sub = await create_subscription(
        db_session, id_community=community.id, is_active=True
    )

    response = await client.get(
        "/unsubscribe",
        params={"feature": "algorithm"},
        headers=_admin_headers(community),
    )

    assert response.status_code == 200
    assert response.json()["error_code"] == 0

    await db_session.refresh(sub)
    assert sub.is_active is False


async def test_unsubscribe_inactive_subscription_returns_error_code_1003(
    client, db_session
):
    community = await create_community(db_session)
    await create_subscription(db_session, id_community=community.id, is_active=False)

    response = await client.get(
        "/unsubscribe",
        params={"feature": "algorithm"},
        headers=_admin_headers(community),
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == 1003  # NOT_SUBSCRIBED


async def test_unsubscribe_missing_subscription_returns_error_code_1003(
    client, db_session
):
    community = await create_community(db_session)

    response = await client.get(
        "/unsubscribe",
        params={"feature": "algorithm"},
        headers=_admin_headers(community),
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == 1003


# ---------------------------------------------------------------------------
# Validation & auth
# ---------------------------------------------------------------------------


async def test_subscribe_invalid_feature_returns_422(client, db_session):
    community = await create_community(db_session)

    response = await client.get(
        "/subscribe",
        params={"feature": "does_not_exist"},
        headers=_admin_headers(community),
    )

    assert response.status_code == 422


async def test_subscribe_as_non_admin_returns_forbidden(client, db_session):
    community = await create_community(db_session)
    headers = _admin_headers(community) | {"x-user-role": "MEMBER"}

    response = await client.get(
        "/subscribe",
        params={"feature": "algorithm"},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == 2  # auth.FORBIDDEN


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------


async def test_subscribe_isolation_between_communities(client, db_session):
    community_a = await create_community(db_session)
    community_b = await create_community(db_session)

    response = await client.get(
        "/subscribe",
        params={"feature": "algorithm"},
        headers=_admin_headers(community_a),
    )
    assert response.status_code == 200

    row_a = await _fetch_subscription(db_session, community_a.id)
    row_b = await _fetch_subscription(db_session, community_b.id)
    assert row_a is not None and row_a.is_active is True
    assert row_b is None
