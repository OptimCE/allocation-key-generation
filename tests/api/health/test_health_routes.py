"""Integration tests for api/health/routes.py.

The readiness probe is what Kubernetes calls to decide whether to route
traffic to a pod. A regression there silently breaks rollouts, so these
tests cover:

* **Route-level** (``readiness`` orchestration): single-axis and multi-axis
  failures, the ``/health`` alias, and the ``/liveness`` shortcut. The
  module-level ``check_db`` / ``check_redis`` / ``check_nats`` functions
  are mocked here so the route's status-code + JSON-shape logic is
  exercised in isolation.
* **Helper-level** (the three ``check_*`` functions themselves): every
  branch — happy path, transport-level failure, and the "client not
  initialised / not connected" guards — that the route tests can't reach
  while the helpers are stubbed out.

The router is mounted in ``main.py`` with ``prefix="/health"``, so the
actual paths are ``/health/liveness``, ``/health/readiness``, ``/health/health``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from api.health import routes as health_routes


async def test_liveness_returns_200_with_alive_status(client):
    response = await client.get("/health/liveness")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_readiness_returns_200_when_all_checks_pass(client, monkeypatch):
    monkeypatch.setattr(
        "api.health.routes.check_db",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_redis",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_nats",
        AsyncMock(return_value={"ok": True}),
    )

    response = await client.get("/health/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert set(body["checks"].keys()) == {"database", "redis", "nats"}
    for component in ("database", "redis", "nats"):
        assert body["checks"][component]["ok"] is True


async def test_readiness_returns_503_when_db_check_fails(client, monkeypatch):
    monkeypatch.setattr(
        "api.health.routes.check_db",
        AsyncMock(return_value={"ok": False, "error": "connection refused"}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_redis",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_nats",
        AsyncMock(return_value={"ok": True}),
    )

    response = await client.get("/health/readiness")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["database"]["ok"] is False
    assert body["checks"]["database"]["error"] == "connection refused"
    assert body["checks"]["redis"]["ok"] is True
    assert body["checks"]["nats"]["ok"] is True


async def test_readiness_returns_503_when_redis_check_fails(client, monkeypatch):
    monkeypatch.setattr(
        "api.health.routes.check_db",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_redis",
        AsyncMock(return_value={"ok": False, "error": "Redis client not initialised"}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_nats",
        AsyncMock(return_value={"ok": True}),
    )

    response = await client.get("/health/readiness")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["redis"]["ok"] is False


async def test_readiness_returns_503_when_nats_check_fails(client, monkeypatch):
    monkeypatch.setattr(
        "api.health.routes.check_db",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_redis",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_nats",
        AsyncMock(return_value={"ok": False, "error": "NATS client not connected"}),
    )

    response = await client.get("/health/readiness")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["nats"]["ok"] is False


async def test_health_alias_returns_same_payload_as_readiness(client, monkeypatch):
    monkeypatch.setattr(
        "api.health.routes.check_db",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_redis",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_nats",
        AsyncMock(return_value={"ok": True}),
    )

    response = await client.get("/health/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert set(body["checks"].keys()) == {"database", "redis", "nats"}


async def test_readiness_aggregates_errors_when_multiple_checks_fail(
    client, monkeypatch
):
    """Two axes failing simultaneously must both surface in the payload.

    K8s ops triage starts here: if only one error is reported when two
    are down, the second outage gets discovered the slow way.
    """
    monkeypatch.setattr(
        "api.health.routes.check_db",
        AsyncMock(return_value={"ok": False, "error": "db down"}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_redis",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "api.health.routes.check_nats",
        AsyncMock(return_value={"ok": False, "error": "nats down"}),
    )

    response = await client.get("/health/readiness")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["database"] == {"ok": False, "error": "db down"}
    assert body["checks"]["redis"] == {"ok": True}
    assert body["checks"]["nats"] == {"ok": False, "error": "nats down"}


# ---------------------------------------------------------------------------
# Helper-level unit tests — exercise the branches inside check_db / check_redis
# / check_nats that the route tests bypass by mocking these functions out.
# ---------------------------------------------------------------------------


async def test_check_db_returns_ok_when_query_succeeds():
    """Real DB hit: the conftest already runs Postgres on port 5433."""
    result = await health_routes.check_db()

    assert result == {"ok": True}


async def test_check_db_returns_error_when_engine_raises(monkeypatch):
    """SQLAlchemyError on connect must be caught and translated to a dict.

    Replaces the whole ``crm_engine`` module binding (its ``connect``
    attribute is read-only on a real ``AsyncEngine``). The ``connect()``
    call happens before the ``async with`` enters, so a synchronous raise
    is enough to land inside the helper's ``except`` block.
    """
    fake_engine = MagicMock()
    fake_engine.connect = MagicMock(side_effect=SQLAlchemyError("connection refused"))
    monkeypatch.setattr("api.health.routes.crm_engine", fake_engine)

    result = await health_routes.check_db()

    assert result["ok"] is False
    assert "connection refused" in result["error"]


async def test_check_redis_returns_error_when_client_not_initialised(monkeypatch):
    """``redis_client`` is bound at module import time; in some deploy
    failure modes it is ``None`` (init crashed). Verify the explicit guard.
    """
    monkeypatch.setattr(health_routes, "redis_client", None)

    result = await health_routes.check_redis()

    assert result == {"ok": False, "error": "Redis client not initialised"}


async def test_check_redis_returns_ok_when_ping_succeeds(monkeypatch, redis):
    """FakeRedis from the conftest plays the role of a healthy client."""
    monkeypatch.setattr(health_routes, "redis_client", redis)

    result = await health_routes.check_redis()

    assert result == {"ok": True}


async def test_check_redis_returns_error_when_ping_raises(monkeypatch):
    fake = MagicMock()
    fake.ping = AsyncMock(side_effect=RedisError("connection lost"))
    monkeypatch.setattr(health_routes, "redis_client", fake)

    result = await health_routes.check_redis()

    assert result["ok"] is False
    assert "connection lost" in result["error"]


async def test_check_nats_returns_ok_when_client_is_connected(monkeypatch):
    """``get_nats`` is imported lazily inside ``check_nats``, so the
    monkeypatch target is the originating module.
    """
    monkeypatch.setattr(
        "core.queue.init.get_nats",
        lambda: SimpleNamespace(is_connected=True),
    )

    result = await health_routes.check_nats()

    assert result == {"ok": True}


async def test_check_nats_returns_error_when_client_not_connected(monkeypatch):
    monkeypatch.setattr(
        "core.queue.init.get_nats",
        lambda: SimpleNamespace(is_connected=False),
    )

    result = await health_routes.check_nats()

    assert result == {"ok": False, "error": "NATS client not connected"}


async def test_check_nats_returns_error_when_get_nats_raises(monkeypatch):
    """``get_nats`` raises ``RuntimeError`` when called before ``init_nats``.
    The helper must translate that into a structured error, not propagate.
    """

    def _raise():
        raise RuntimeError("NATS not initialised")

    monkeypatch.setattr("core.queue.init.get_nats", _raise)

    result = await health_routes.check_nats()

    assert result["ok"] is False
    assert "NATS not initialised" in result["error"]
