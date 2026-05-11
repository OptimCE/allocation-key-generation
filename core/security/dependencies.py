# core/security/dependencies.py
import logging

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.context_vars import current_user_id, current_community_id, current_user_role
from core.database.database import get_crm_session
from core.errors.errors import ErrorException
from core.security.user_context import Role, ROLE_HIERARCHY
from shared.const import FeatureName
from shared.custom_errors import errors

logger = logging.getLogger(__name__)


async def require_authenticated() -> str:
    user_id = current_user_id.get()
    if not user_id:
        raise ErrorException(errors.auth.UNAUTHORIZED)
    return user_id


async def require_community() -> str:
    await require_authenticated()
    community_id = current_community_id.get()
    if not community_id:
        raise ErrorException(errors.auth.AUTHORIZATION_MISSING)
    return community_id


def require_min_role(min_role: Role):
    min_rank = ROLE_HIERARCHY[min_role]

    async def _check() -> str:
        user_id = await require_authenticated()
        await require_community()
        role_str = current_user_role.get()
        if not role_str:
            raise ErrorException(errors.auth.FORBIDDEN)
        try:
            role = Role(role_str)
        except ValueError:
            raise ErrorException(errors.auth.FORBIDDEN)
        if ROLE_HIERARCHY.get(role, 0) < min_rank:
            logger.warning(
                "Min role check failed",
                extra={
                    "user_id": user_id,
                    "user_role": role_str,
                    "required_min": min_role.value,
                },
            )
            raise ErrorException(errors.auth.FORBIDDEN)
        return user_id

    return _check


async def _ensure_active_subscription(
    feature: FeatureName, crm_session: AsyncSession
) -> None:
    # Imported here to avoid a circular import: api.subscription.repository
    # imports from core.database, which transitively pulls this module.
    from api.subscription.repository import SubscriptionRepository

    await require_community()
    repo = SubscriptionRepository(crm_session)
    sub = await repo.get_subscription(feature)
    if sub is None or not sub.is_active:
        raise ErrorException(errors.subscription.NOT_SUBSCRIBED, status_code=403)


def require_feature(feature: FeatureName):
    async def _check(crm_session: AsyncSession = Depends(get_crm_session)) -> None:
        await _ensure_active_subscription(feature, crm_session)

    return _check


async def require_subscribed_feature(
    feature: FeatureName = Query(...),
    crm_session: AsyncSession = Depends(get_crm_session),
) -> None:
    await _ensure_active_subscription(feature, crm_session)
