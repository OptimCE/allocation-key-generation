"""Persistence helpers for the worker process.

Pure database I/O — neither NATS nor algorithms live here. The dispatcher
calls ``save_success`` after a successful algorithm run and ``save_failure``
on any deterministic failure (bad input data, bad inputs, algorithm crash).

Each helper opens its own ``AsyncSessionLocalFactory`` session so it's safe
to call from any branch of the dispatcher without worrying about leaking
half-finished transactions from earlier steps.

Idempotency under JetStream redelivery
--------------------------------------
The dispatcher already short-circuits when ``status != PENDING`` is
observed up-front, but a long-running algorithm can exceed ack_wait while
running, leading to a *concurrent* second delivery to a different worker.
Both workers would then read ``status=PENDING`` and race to write results.

To make this safe at the DB layer, both helpers perform a conditional
``UPDATE generation SET status=... WHERE id=? AND status=PENDING`` and use
the affected-row count as a lock: only the worker whose UPDATE actually
flipped the row proceeds to write the result tree. The other one logs and
exits without inserting anything, so duplicate result rows are impossible.
"""

from __future__ import annotations

import logging

from sqlalchemy import update

from algorithms.base import AlgorithmResult
from core import metrics as app_metrics
from core.audit_log import AuditActions, AuditLogInput, AuditLogService
from core.database.database import AsyncSessionCRMFactory, AsyncSessionLocalFactory
from core.realtime import CommunityAudience, Tier, emit
from shared.const import GenerationStatus
from shared.models.local_models import (
    AllocationKeyGeneratedModel,
    ConsumerGeneratedModel,
    GenerationModel,
    IterationGeneratedModel,
)

logger = logging.getLogger(__name__)

# Cap on stored error messages so a runaway traceback can't blow up the
# row size. Two thousand chars covers any realistic algorithm failure
# while still fitting comfortably in a TEXT column.
_ERROR_MESSAGE_MAX_LEN = 2000


async def save_success(generation_id: int, result: AlgorithmResult) -> None:
    """Persist a successful run: build the result tree and flip status.

    Uses SQLAlchemy relationship cascades — assigning ``key.iterations =
    [...]`` (each with its own ``consumers = [...]``) and adding only the
    top-level keys lets the unit-of-work flush the whole subtree in one go.

    Idempotent: if the row is no longer PENDING (concurrent redelivery
    already wrote results, or the row was deleted), this is a no-op.
    """
    async with AsyncSessionLocalFactory() as session:
        generation = await session.get(GenerationModel, generation_id)
        if generation is None:
            logger.warning("save_success called for missing generation id=%d", generation_id)
            return

        # Atomic claim: row-level lock + status check happen together. If
        # rowcount is 0 we lost the race (or the row already moved past
        # PENDING for some other reason) — exit without touching results.
        result_proxy = await session.execute(
            update(GenerationModel)
            .where(
                GenerationModel.id == generation_id,
                GenerationModel.status == GenerationStatus.PENDING,
            )
            .values(status=GenerationStatus.SUCCESS, error_message=None)
        )
        if result_proxy.rowcount == 0:
            logger.info(
                "save_success no-op for generation %d (already non-PENDING)",
                generation_id,
            )
            return

        community_id = generation.id_community
        key_rows: list[AllocationKeyGeneratedModel] = []
        for key in result.keys:
            iteration_rows: list[IterationGeneratedModel] = []
            for iteration in key.iterations:
                consumer_rows = [
                    ConsumerGeneratedModel(
                        name=consumer.name,
                        energy_allocated_percentage=consumer.energy_allocated_percentage,
                        id_community=community_id,
                    )
                    for consumer in iteration.consumers
                ]
                iteration_rows.append(
                    IterationGeneratedModel(
                        number=iteration.number,
                        energy_allocated_percentage=iteration.energy_allocated_percentage,
                        surplus_total=iteration.surplus_total,
                        id_community=community_id,
                        consumers=consumer_rows,
                    )
                )
            key_rows.append(
                AllocationKeyGeneratedModel(
                    name=key.name,
                    description=key.description,
                    surplus_total=sum(it.surplus_total for it in key.iterations),
                    id_generation=generation_id,
                    id_community=community_id,
                    iterations=iteration_rows,
                )
            )

        session.add_all(key_rows)
        await session.commit()
        app_metrics.generations_completed.add(
            1,
            {"algorithm": generation.algorithm_name, "status": "success"},
        )

    # Audit after the local result is committed. The audit service swallows
    # its own errors, so a CRM hiccup never rolls back the generation result.
    async with AsyncSessionCRMFactory() as crm_session:
        await AuditLogService(crm_session).log(
            AuditLogInput(
                action=AuditActions.GENERATION_SUCCEEDED,
                entity_type="generation",
                entity_id=str(generation_id),
                payload={
                    "algorithm_name": generation.algorithm_name,
                    "key_count": len(result.keys),
                },
            ),
            id_community=community_id,
        )
        await crm_session.commit()

    # Realtime hint, AFTER both commits. Fire-and-forget: if no manager has the
    # hub open it is dropped, which is correct — the row is already durable and
    # the hub's own poller converges regardless.
    #
    # Audience is the community's MANAGER tier, not a user: `generation` carries
    # only `id_community`, and the hub route is manager-gated anyway
    # (annexes-services.routes.ts, minRole GESTIONNAIRE). That is the whole point
    # of the community channel family — a worker with no request context and no
    # user attribution addresses exactly the right people with zero lookups.
    #
    # No error message and no key count in the envelope: it is a hint, and the
    # client refetches through the gateway (which re-authorizes the read).
    await emit(
        topic="generation.finished",
        audience=CommunityAudience(community_id=community_id, tier=Tier.MANAGER),
        resource=("generation", generation_id),
        scope_community_id=community_id,
        hint={"status": "success"},
    )


async def save_failure(generation_id: int, error_message: str) -> None:
    """Mark a generation FAILED with the given message.

    Idempotent: only flips status when the row is still PENDING. A second
    call (from the same or a concurrent delivery) is a no-op.
    """
    async with AsyncSessionLocalFactory() as session:
        generation = await session.get(GenerationModel, generation_id)
        if generation is None:
            logger.warning("save_failure called for missing generation id=%d", generation_id)
            return

        result_proxy = await session.execute(
            update(GenerationModel)
            .where(
                GenerationModel.id == generation_id,
                GenerationModel.status == GenerationStatus.PENDING,
            )
            .values(
                status=GenerationStatus.FAILED,
                error_message=error_message[:_ERROR_MESSAGE_MAX_LEN],
            )
        )
        if result_proxy.rowcount == 0:
            logger.info(
                "save_failure no-op for generation %d (already non-PENDING)",
                generation_id,
            )
            return

        await session.commit()
        algorithm_name = generation.algorithm_name
        id_community = generation.id_community
        app_metrics.generations_completed.add(
            1,
            {"algorithm": algorithm_name, "status": "failed"},
        )

    # Audit after the local commit. Same rationale as save_success: a CRM
    # hiccup here must not mask the local FAILED state.
    async with AsyncSessionCRMFactory() as crm_session:
        await AuditLogService(crm_session).log(
            AuditLogInput(
                action=AuditActions.GENERATION_FAILED,
                entity_type="generation",
                entity_id=str(generation_id),
                payload={
                    "algorithm_name": algorithm_name,
                    "error_message": error_message[:_ERROR_MESSAGE_MAX_LEN],
                },
            ),
            id_community=id_community,
        )
        await crm_session.commit()

    # Same contract as save_success. `id_community` was captured before the
    # session closed; both factories set expire_on_commit=False, so reading it
    # here does not re-issue a query against a closed session.
    await emit(
        topic="generation.finished",
        audience=CommunityAudience(community_id=id_community, tier=Tier.MANAGER),
        resource=("generation", generation_id),
        scope_community_id=id_community,
        hint={"status": "failed"},
    )

