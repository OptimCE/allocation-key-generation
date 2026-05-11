"""
Unit tests for api/subscription/services.py.

Service is instantiated directly with an AsyncMock session and the repository
attribute replaced by an AsyncMock. No DB, no HTTP, no FastAPI DI. Each test
exercises a single branch of subscribe()/unsubscribe(), including commit and
exception side effects.
"""

from unittest.mock import AsyncMock

import pytest

from api.subscription.services import SubscriptionService
from core.context_vars import current_internal_community_id
from core.database.models import CommunitySubscription
from core.errors.errors import ErrorException
from shared.const import FeatureName
from shared.custom_errors import errors


def _make_service() -> SubscriptionService:
    session = AsyncMock()
    service = SubscriptionService(session=session)
    service.repository = AsyncMock()
    return service


def _make_subscription(*, is_active: bool) -> CommunitySubscription:
    sub = CommunitySubscription()
    sub.id_community = 42
    sub.feature = FeatureName.ALGORITHM
    sub.is_active = is_active
    return sub


# ---------------------------------------------------------------------------
# subscribe
# ---------------------------------------------------------------------------


async def test_subscribe_creates_new_when_no_existing():
    service = _make_service()
    service.repository.get_subscription.return_value = None

    token = current_internal_community_id.set(42)
    try:
        await service.subscribe(FeatureName.ALGORITHM)
    finally:
        current_internal_community_id.reset(token)

    service.repository.create_subscription.assert_awaited_once()
    created = service.repository.create_subscription.call_args.args[0]
    assert isinstance(created, CommunitySubscription)
    assert created.id_community == 42
    assert created.feature == FeatureName.ALGORITHM
    assert created.is_active is True
    service.session.commit.assert_awaited_once()


async def test_subscribe_reactivates_inactive_existing():
    service = _make_service()
    existing = _make_subscription(is_active=False)
    service.repository.get_subscription.return_value = existing

    await service.subscribe(FeatureName.ALGORITHM)

    assert existing.is_active is True
    service.repository.create_subscription.assert_not_awaited()
    service.session.commit.assert_awaited_once()


async def test_subscribe_raises_already_subscribed_when_active():
    service = _make_service()
    existing = _make_subscription(is_active=True)
    service.repository.get_subscription.return_value = existing

    with pytest.raises(ErrorException) as exc_info:
        await service.subscribe(FeatureName.ALGORITHM)

    assert exc_info.value.error is errors.subscription.ALREADY_SUBSCRIBED
    service.repository.create_subscription.assert_not_awaited()
    service.session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# unsubscribe
# ---------------------------------------------------------------------------


async def test_unsubscribe_deactivates_active_subscription():
    service = _make_service()
    existing = _make_subscription(is_active=True)
    service.repository.get_subscription.return_value = existing

    await service.unsubscribe(FeatureName.ALGORITHM)

    assert existing.is_active is False
    service.session.commit.assert_awaited_once()


async def test_unsubscribe_raises_not_subscribed_when_missing():
    service = _make_service()
    service.repository.get_subscription.return_value = None

    with pytest.raises(ErrorException) as exc_info:
        await service.unsubscribe(FeatureName.ALGORITHM)

    assert exc_info.value.error is errors.subscription.NOT_SUBSCRIBED
    service.session.commit.assert_not_awaited()


async def test_unsubscribe_raises_not_subscribed_when_inactive():
    service = _make_service()
    existing = _make_subscription(is_active=False)
    service.repository.get_subscription.return_value = existing

    with pytest.raises(ErrorException) as exc_info:
        await service.unsubscribe(FeatureName.ALGORITHM)

    assert exc_info.value.error is errors.subscription.NOT_SUBSCRIBED
    service.session.commit.assert_not_awaited()
