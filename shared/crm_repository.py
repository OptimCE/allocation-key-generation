from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.crm_models import AllocationKeyModel


class CRMRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_allocation_key(self, allocation_key: AllocationKeyModel) -> AllocationKeyModel:
        self.session.add(allocation_key)
        await self.session.flush()
        return allocation_key
