from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import CommunitySubscription
from shared.const import FeatureName
from shared.database.with_community import with_community_scope


class SubscriptionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_subscription(
        self, feature: FeatureName
    ) -> CommunitySubscription | None:
        stmt = with_community_scope(
            select(CommunitySubscription), CommunitySubscription
        ).where(CommunitySubscription.feature == feature)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_subscription(
        self, new_subscription: CommunitySubscription
    ) -> CommunitySubscription:
        self.session.add(new_subscription)
        await self.session.flush()
        return new_subscription
