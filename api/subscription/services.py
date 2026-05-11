import logging

from sqlalchemy.ext.asyncio import AsyncSession

from api.subscription.repository import SubscriptionRepository
from core.context_vars import current_internal_community_id
from core.database.models import CommunitySubscription
from core.errors.errors import ErrorException
from shared.const import FeatureName
from shared.custom_errors import errors

logger = logging.getLogger(__name__)


class SubscriptionService:
    def __init__(self, session: AsyncSession):
        self.repository = SubscriptionRepository(session)
        self.session = session

    async def subscribe(self, feature: FeatureName):
        existing = await self.repository.get_subscription(feature)
        if existing is not None:
            if existing.is_active:
                logger.info("Community already subscribed to %s", feature.value)
                raise ErrorException(errors.subscription.ALREADY_SUBSCRIBED)
            existing.is_active = True
        else:
            community_id = current_internal_community_id.get()
            if community_id is None:
                raise ErrorException(errors.auth.AUTHORIZATION_MISSING)
            new_subscription = CommunitySubscription()
            new_subscription.id_community = community_id
            new_subscription.feature = feature
            new_subscription.is_active = True
            await self.repository.create_subscription(new_subscription)

        await self.session.commit()

    async def unsubscribe(self, feature: FeatureName):
        existing = await self.repository.get_subscription(feature)
        if existing is None or not existing.is_active:
            raise ErrorException(errors.subscription.NOT_SUBSCRIBED)
        existing.is_active = False
        await self.session.commit()
