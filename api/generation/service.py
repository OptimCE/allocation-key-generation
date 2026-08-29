import datetime
import logging
from uuid import uuid4

from fastapi import UploadFile
from pydantic import ValidationError

from algorithms.registry import registry
from api.generation.mappers import (
    to_allocation_key_crm,
    to_allocation_key_generated_schema,
    to_generation_schema,
    to_partial_allocation_key_generated_schema,
)
from api.generation.repository import GenerationRepository
from api.generation.schemas import (
    AllocationKeyGenerated,
    GenerateFromCrmRequest,
    GenerateRequest,
    GenerateResponse,
    Generation,
    PartialAllocationKeyGenerated,
    SaveKey,
)
from core import metrics as app_metrics
from core import storage
from core.api_response import Pagination
from core.audit_log import AuditActions, AuditLogInput, AuditLogService
from core.database.database import AsyncSessionCRMFactory, AsyncSessionLocalFactory
from core.errors.errors import ErrorException
from core.middleware.request_limits import UPLOAD_MAX_BODY_BYTES
from core.queue.helper import Event, send_event
from core.queue.init import get_jetstream
from shared import crm_preflight
from shared.const import DataSource, GenerationStatus
from shared.crm_meter_repository import CrmMeterRepository
from shared.crm_preflight import Preflight
from shared.crm_repository import CRMRepository
from shared.custom_errors import errors
from shared.models.local_models import GenerationModel

logger = logging.getLogger(__name__)


# Read the upload in bounded chunks instead of one unbounded ``file.read()``.
_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024  # 1 MB


async def _read_within_cap(file: UploadFile, max_bytes: int) -> bytes:
    """Read ``file`` fully into memory, aborting with 413 past ``max_bytes``.

    RequestLimitsMiddleware rejects oversized uploads up front via the
    Content-Length header, but a chunked request (Transfer-Encoding: chunked)
    carries no Content-Length and slips past that gate — and the multipart
    parser streams file parts to a disk-backed spool with no size cap. Reading
    here in bounded chunks keeps peak memory at roughly ``max_bytes`` and
    rejects the body before the whole thing is pulled into a single ``bytes``,
    closing the memory-exhaustion bypass. A body of exactly ``max_bytes`` is
    accepted; the first byte over is rejected.
    """
    buffer = bytearray()
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        if len(buffer) + len(chunk) > max_bytes:
            raise ErrorException(error=errors.generation.FILE_TOO_LARGE, status_code=413)
        buffer.extend(chunk)
    return bytes(buffer)


class GenerationService:
    def __init__(self, local_session, crm_session):
        self.local_session = local_session
        self.crm_session = crm_session
        self.repository = GenerationRepository(local_session)
        self.crm_repository = CRMRepository(crm_session)
        self.audit_log_service = AuditLogService(crm_session)

    async def get_generations(
        self, page: int, page_size: int, query_param: dict
    ) -> tuple[list[Generation], Pagination]:
        rows, total = await self.repository.get_list_generations(page, page_size, query_param)
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
            raise ErrorException(error=errors.generation.ALLOCATION_KEY_NOT_FOUND, status_code=400)
        return to_allocation_key_generated_schema(data)

    async def start_generation(
        self, req: GenerateRequest, file: UploadFile, community_id: int
    ) -> GenerateResponse:
        """Upload the source file to MinIO, persist the row, publish a NATS event.

        Ordering: upload-then-commit-then-publish. Uploading first means a
        DB failure leaves only a transient orphan in MinIO (cleaned up in
        the rollback branch). The alternative — commit first — would leave
        a row pointing at a non-existent key.

        On any failure after a successful upload, the object is deleted
        best-effort so the bucket does not accumulate orphans.
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

        # 3. Read the upload body. Reject empty files so the worker doesn't
        # waste a slot on a guaranteed parse failure. The bounded read caps
        # peak memory and rejects oversized chunked bodies that slip past
        # RequestLimitsMiddleware's Content-Length gate.
        content = await _read_within_cap(file, UPLOAD_MAX_BODY_BYTES)
        if not content:
            raise ErrorException(
                error=errors.generation.INVALID_FILE,
                status_code=422,
            )
        file_name = file.filename or "uploaded-file"

        # 4. Upload to MinIO. The community_id keeps keys browsable per
        # tenant; the UUID guarantees no collisions when two requests use
        # the same filename.
        storage_key = f"allocations/{community_id}/{uuid4()}/{file_name}"
        try:
            await storage.upload(storage_key, content, content_type=file.content_type)
        except Exception as exc:
            logger.exception(
                "Storage upload failed for community %d key=%s",
                community_id,
                storage_key,
            )
            raise ErrorException(
                error=errors.generation.STORAGE_UPLOAD_FAILED,
                status_code=502,
            ) from exc

        # 5. Build and persist the generation row. If the DB write fails,
        # the uploaded object becomes orphaned — clean it up.
        model = GenerationModel(
            name=req.name,
            id_community=community_id,
            file_storage_key=storage_key,
            file_name=file_name,
            injection_name=req.injection_name,
            algorithm_name=meta.name,
            algorithm_version=meta.version,
            inputs=validated_inputs.model_dump(mode="json"),
            status=GenerationStatus.PENDING,
        )
        try:
            await self.repository.create_generation(model)
            await self.local_session.commit()
        except Exception:
            await _best_effort_delete(storage_key)
            raise
        generation_id = model.id
        app_metrics.generations_created.add(1, {"algorithm": meta.name})
        await self.audit_log_service.log(
            AuditLogInput(
                action=AuditActions.GENERATION_CREATED,
                entity_type="generation",
                entity_id=str(generation_id),
                payload={
                    "name": req.name,
                    "algorithm_name": meta.name,
                    "algorithm_version": meta.version,
                    "file_name": file_name,
                    "injection_name": req.injection_name,
                },
            )
        )

        # 6. Publish event to the algorithm's queue. On failure, mark the
        # row FAILED in a separate transaction and delete the orphan object.
        event = Event(
            type="generation.requested",
            data={"generation_id": generation_id},
        )
        try:
            await send_event(get_jetstream(), meta.queue, event)
        except Exception as exc:
            logger.exception("Failed to publish generation %d to %s", generation_id, meta.queue)
            await self._mark_failed_to_queue(generation_id, str(exc))
            await _best_effort_delete(storage_key)
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
        algorithm_name: str | None = None
        id_community: int | None = None
        async with AsyncSessionLocalFactory() as session:
            row = await session.get(GenerationModel, generation_id)
            if row is None:
                return
            row.status = GenerationStatus.FAILED
            row.error_message = f"failed_to_queue: {reason}"[:2000]
            await session.commit()
            algorithm_name = row.algorithm_name
            id_community = row.id_community
            app_metrics.generations_completed.add(
                1,
                {"algorithm": row.algorithm_name, "status": "failed"},
            )

        # Use a fresh CRM session for the audit row — same rationale as the
        # local session above.
        async with AsyncSessionCRMFactory() as crm_session:
            await AuditLogService(crm_session).log(
                AuditLogInput(
                    action=AuditActions.GENERATION_QUEUE_FAILED,
                    entity_type="generation",
                    entity_id=str(generation_id),
                    payload={
                        "reason": reason[:500],
                        "algorithm_name": algorithm_name,
                    },
                ),
                id_community=id_community,
            )
            await crm_session.commit()

    # ------------------------------------------------------------------
    # CRM-sourced generation
    # ------------------------------------------------------------------

    async def preview_crm_data(
        self,
        *,
        id_sharing_operation: int,
        period_start: datetime.date,
        period_end: datetime.date,
        community_id: int,
    ) -> Preflight:
        """Aggregate the requested period and classify it, without running anything.

        Also the pre-flight for ``start_generation_from_crm`` — one code path, so
        the answer the manager saw and the answer that gates the run cannot drift.
        """
        if period_start > period_end:
            raise ErrorException(error=errors.generation.INVALID_PERIOD, status_code=422)

        crm_meters = CrmMeterRepository(self.crm_session)
        # Explicit tenant check: without it a foreign operation id is
        # indistinguishable from an empty period, which is a confusing 422 for a
        # legitimate user and a soft information leak for everyone else.
        if not await crm_meters.sharing_operation_exists(
            id_community=community_id, id_sharing_operation=id_sharing_operation
        ):
            raise ErrorException(
                error=errors.generation.SHARING_OPERATION_NOT_FOUND, status_code=404
            )

        summary = await crm_meters.summarize(
            id_community=community_id,
            id_sharing_operation=id_sharing_operation,
            period_start=period_start,
            period_end=period_end,
        )
        return crm_preflight.evaluate(summary)

    async def start_generation_from_crm(
        self, req: GenerateFromCrmRequest, community_id: int
    ) -> GenerateResponse:
        """Queue a generation that reads its input from the CRM.

        Same ordering as the file path minus the upload: validate, commit, then
        publish. There is no object to roll back, so the ``_best_effort_delete``
        branches have no counterpart here.
        """
        if req.algorithm_name not in registry:
            raise ErrorException(error=errors.generation.ALGORITHM_NOT_FOUND, status_code=404)
        meta = registry.metadata(req.algorithm_name)

        try:
            validated_inputs = meta.input_schema.model_validate(req.inputs)
        except ValidationError as e:
            logger.info("Invalid inputs for algorithm '%s': %s", req.algorithm_name, e)
            raise ErrorException(
                error=errors.generation.INVALID_ALGORITHM_INPUTS, status_code=422
            ) from e

        # Re-run the pre-flight rather than trusting whatever the client saw:
        # the preview may be minutes old, and an import can have landed since.
        preflight = await self.preview_crm_data(
            id_sharing_operation=req.id_sharing_operation,
            period_start=req.period_start,
            period_end=req.period_end,
            community_id=community_id,
        )
        if preflight.blockers:
            first = preflight.blockers[0]
            logger.info(
                "CRM generation refused for community %d op %d: %s",
                community_id,
                req.id_sharing_operation,
                first.detail,
            )
            raise ErrorException(error=first.error, status_code=422)

        model = GenerationModel(
            name=req.name,
            id_community=community_id,
            source=DataSource.CRM,
            id_sharing_operation=req.id_sharing_operation,
            period_start=req.period_start,
            period_end=req.period_end,
            algorithm_name=meta.name,
            algorithm_version=meta.version,
            inputs=validated_inputs.model_dump(mode="json"),
            status=GenerationStatus.PENDING,
            data_warnings=preflight.warnings,
        )
        await self.repository.create_generation(model)
        await self.local_session.commit()
        generation_id = model.id
        app_metrics.generations_created.add(1, {"algorithm": meta.name})
        await self.audit_log_service.log(
            AuditLogInput(
                action=AuditActions.GENERATION_CREATED,
                entity_type="generation",
                entity_id=str(generation_id),
                payload={
                    "name": req.name,
                    "algorithm_name": meta.name,
                    "algorithm_version": meta.version,
                    "source": DataSource.CRM.name,
                    "id_sharing_operation": req.id_sharing_operation,
                    "period_start": req.period_start.isoformat(),
                    "period_end": req.period_end.isoformat(),
                },
            )
        )

        event = Event(type="generation.requested", data={"generation_id": generation_id})
        try:
            await send_event(get_jetstream(), meta.queue, event)
        except Exception as exc:
            logger.exception("Failed to publish generation %d to %s", generation_id, meta.queue)
            await self._mark_failed_to_queue(generation_id, str(exc))
            raise ErrorException(error=errors.generation.START_GENERATION, status_code=500) from exc

        return GenerateResponse(id=generation_id, status=GenerationStatus.PENDING)

    async def save_key(self, saved_key: SaveKey):
        # Retrieve it in this database
        key = await self.repository.get_allocation_key(saved_key.id_key)
        if not key:
            raise ErrorException(error=errors.generation.ALLOCATION_KEY_NOT_FOUND, status_code=400)
        # Refactor it
        allocation_key = to_allocation_key_crm(key)
        # Save it in crm database
        await self.crm_repository.save_allocation_key(allocation_key)
        await self.crm_session.commit()
        await self.audit_log_service.log(
            AuditLogInput(
                action=AuditActions.ALLOCATION_KEY_SAVED,
                entity_type="allocation_key",
                entity_id=str(allocation_key.id),
                payload={
                    "local_key_id": saved_key.id_key,
                    "name": allocation_key.name,
                },
            )
        )

    async def delete_generation(self, id_generation):
        generation = await self.repository.get_generation(id_generation)
        if not generation:
            raise ErrorException(error=errors.generation.GENERATION_NOT_FOUND, status_code=400)
        algorithm_name = generation.algorithm_name
        status_name = GenerationStatus(generation.status).name
        await self.repository.delete_generation(generation)
        await self.local_session.commit()
        await self.audit_log_service.log(
            AuditLogInput(
                action=AuditActions.GENERATION_DELETED,
                entity_type="generation",
                entity_id=str(id_generation),
                payload={
                    "algorithm_name": algorithm_name,
                    "status": status_name,
                },
            )
        )

    async def delete_key(self, id_key):
        key = await self.repository.get_allocation_key(id_key)
        if not key:
            raise ErrorException(error=errors.generation.ALLOCATION_KEY_NOT_FOUND, status_code=400)
        key_name = key.name
        id_generation = key.id_generation
        await self.repository.delete_key(key)
        await self.local_session.commit()
        await self.audit_log_service.log(
            AuditLogInput(
                action=AuditActions.ALLOCATION_KEY_GENERATED_DELETED,
                entity_type="allocation_key_generated",
                entity_id=str(id_key),
                payload={
                    "name": key_name,
                    "id_generation": id_generation,
                },
            )
        )


async def _best_effort_delete(storage_key: str) -> None:
    """Wrap ``storage.delete`` for rollback paths so the caller is unaware.

    ``storage.delete`` already swallows its own errors and is idempotent;
    this exists so future instrumentation (e.g. counting rollback orphans)
    has a single place to hook in.
    """
    await storage.delete(storage_key)
