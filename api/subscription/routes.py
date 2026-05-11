from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.subscription.services import SubscriptionService
from core.api_response import ApiResponse
from core.database.database import get_crm_session
from core.errors.with_default_error import with_default_error
from core.security.community_scope import resolve_internal_community
from core.security.dependencies import require_min_role, require_subscribed_feature
from core.security.user_context import Role
from shared.const import FeatureName
from shared.custom_errors import errors

subscription_routes = APIRouter(dependencies=[Depends(resolve_internal_community)])


@subscription_routes.get(
    "/subscribe",
    response_model=ApiResponse[str],
    dependencies=[Depends(require_min_role(Role.ADMIN))],
)
@with_default_error(default_error=errors.subscription.SUBSCRIPTION)
async def subscribe_to_service(
    feature: Annotated[FeatureName, Query(...)],
    session: Annotated[AsyncSession, Depends(get_crm_session)],
):
    service = SubscriptionService(session)
    await service.subscribe(feature)
    return ApiResponse[str](data="success")


@subscription_routes.get(
    "/unsubscribe",
    response_model=ApiResponse[str],
    dependencies=[
        Depends(require_min_role(Role.ADMIN)),
        Depends(require_subscribed_feature),
    ],
)
@with_default_error(default_error=errors.subscription.UNSUBSCRIBED)
async def unsubscribe_from_service(
    feature: Annotated[FeatureName, Query(...)],
    session: Annotated[AsyncSession, Depends(get_crm_session)],
):
    service = SubscriptionService(session)
    await service.unsubscribe(feature)
    return ApiResponse[str](data="success")
