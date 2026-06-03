from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.database import get_crm_session
from shared.audit_log.service import AuditLogService


async def get_audit_log_service(
    crm_session: Annotated[AsyncSession, Depends(get_crm_session)],
) -> AuditLogService:
    return AuditLogService(crm_session)
