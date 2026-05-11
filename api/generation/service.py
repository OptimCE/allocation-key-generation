import logging

from pydantic import ValidationError

from algorithms.registry import registry
from api.generation.mappers import (
    to_generation_schema,
    to_allocation_key_generated_schema,
    to_partial_allocation_key_generated_schema,
    to_allocation_key_crm,
)
from api.generation.repository import GenerationRepository
from api.generation.schemas import (
    AllocationKeyGenerated,
    Generation,
    GenerateRequest,
    GenerateResponse,
    PartialAllocationKeyGenerated,
    SaveKey,
)
from core import metrics as app_metrics
from core.api_response import Pagination
from core.database.database import AsyncSessionLocalFactory
from core.errors.errors import ErrorException
from core.queue.helper import Event, send_event
from core.queue.init import get_jetstream
from shared.const import GenerationStatus
from shared.crm_repository import CRMRepository
from shared.custom_errors import errors
from shared.models.local_models import GenerationModel

logger = logging.getLogger(__name__)


class GenerationService:
    def __init__(self, local_session, crm_session):
        self.local_session = local_session
        self.crm_session = crm_session
        self.repository = GenerationRepository(local_session)
        self.crm_repository = CRMRepository(crm_session)

    async def get_generations(
        self, page: int, page_size: int, query_param: dict
    ) -> tuple[list[Generation], Pagination]:
        rows, total = await self.repository.get_list_generations(
            page, page_size, query_param
        )
        data = [to_generation_schema(n) for n in rows]
        pagination = Pagination(
            page=page, limit=page_size, total=total, total_pages=-(-total // page_size)
        )
        return data, pagination

    async def get_allocation_keys(
        self, id: int, page: int, page_size: int, query_param: dict
    ) -> tuple[list[PartialAllocationKeyGenerated], Pagination]:
        rows, total = await self.repository.get_allocation_keys_list(
            id, page, page_size, query_param
        )
        data = [to_partial_allocation_key_generated_schema(n) for n in rows]
        pagination = Pagination(
            page=page, limit=page_size, total=total, total_pages=-(-total // page_size)
        )
        return data, pagination

    async def get_allocation_key(self, id_key: int) -> AllocationKeyGenerated:
        data = await self.repository.get_allocation_key(id_key)
        if not data:
            raise ErrorException(
                error=errors.generation.ALLOCATION_KEY_NOT_FOUND, status_code=400
            )
        return to_allocation_key_generated_schema(data)

    async def start_generation(
        self, req: GenerateRequest, community_id: int
    ) -> GenerateResponse:
        """Create a new generation row, commit it, then publish a NATS event.

        Ordering: commit-then-publish. If the publish fails after the commit,
        we open a fresh session and mark the row FAILED with an error message,
        then re-raise. This makes the failure visible to the user instead of
        leaving an orphaned PENDING row.
        """
        # 1. Algorithm lookup — must be in the registry.
        if req.algorithm_name not in registry:
            raise ErrorException(
                error=errors.generation.ALGORITHM_NOT_FOUND,
                status_code=404,
            )
        meta = registry.metadata(req.algorithm_name)

        # 2. Validate inputs against the algorithm's own input schema.
        try:
            validated_inputs = meta.input_schema.model_validate(req.inputs)
        except ValidationError as e:
            logger.info("Invalid inputs for algorithm '%s': %s", req.algorithm_name, e)
            raise ErrorException(
                error=errors.generation.INVALID_ALGORITHM_INPUTS,
                status_code=422,
            ) from e

        # 3. Build and persist the generation row.
        model = GenerationModel(
            name=req.name,
            id_community=community_id,
            file_url=req.file_url,
            file_name=req.file_name,
            injection_name=req.injection_name,
            algorithm_name=meta.name,
            algorithm_version=meta.version,
            inputs=validated_inputs.model_dump(mode="json"),
            status=GenerationStatus.PENDING,
        )
        await self.repository.create_generation(model)
        await self.local_session.commit()
        generation_id = model.id
        app_metrics.generations_created.add(1, {"algorithm": meta.name})

        # 4. Publish event to the algorithm's queue. On failure, mark the
        # row FAILED in a separate transaction so the user sees what happened.
        event = Event(
            type="generation.requested",
            data={"generation_id": generation_id},
        )
        try:
            await send_event(get_jetstream(), meta.queue, event)
        except Exception as exc:
            logger.exception(
                "Failed to publish generation %d to %s", generation_id, meta.queue
            )
            await self._mark_failed_to_queue(generation_id, str(exc))
            raise ErrorException(
                error=errors.generation.START_GENERATION,
                status_code=500,
            ) from exc

        return GenerateResponse(id=generation_id, status=GenerationStatus.PENDING)

    @staticmethod
    async def _mark_failed_to_queue(generation_id: int, reason: str) -> None:
        """Mark a generation FAILED after a publish failure.

        Uses a fresh session because the request session may already be in
        an inconsistent state by the time we get here.
        """
        async with AsyncSessionLocalFactory() as session:
            row = await session.get(GenerationModel, generation_id)
            if row is None:
                return
            row.status = GenerationStatus.FAILED
            row.error_message = f"failed_to_queue: {reason}"[:2000]
            await session.commit()
            app_metrics.generations_completed.add(
                1,
                {"algorithm": row.algorithm_name, "status": "failed"},
            )

    async def save_key(self, saved_key: SaveKey):
        # Retrieve it in this database
        key = await self.repository.get_allocation_key(saved_key.id_key)
        if not key:
            raise ErrorException(
                error=errors.generation.ALLOCATION_KEY_NOT_FOUND, status_code=400
            )
        # Refactor it
        allocation_key = to_allocation_key_crm(key)
        # Save it in crm database
        await self.crm_repository.save_allocation_key(allocation_key)
        await self.crm_session.commit()

    async def delete_generation(self, id_generation):
        generation = await self.repository.get_generation(id_generation)
        if not generation:
            raise ErrorException(
                error=errors.generation.GENERATION_NOT_FOUND, status_code=400
            )
        await self.repository.delete_generation(generation)
        await self.local_session.commit()

    async def delete_key(self, id_key):
        key = await self.repository.get_allocation_key(id_key)
        if not key:
            raise ErrorException(
                error=errors.generation.ALLOCATION_KEY_NOT_FOUND, status_code=400
            )
        await self.repository.delete_key(key)
        await self.local_session.commit()
