from api.generation.schemas import (
    AllocationKeyGenerated,
    ConsumerGenerated,
    CrmDataPreview,
    Generation,
    IncompleteMeter,
    IterationGenerated,
    PartialAllocationKeyGenerated,
    PreviewBlocker,
)
from core.i18n import translate
from shared.crm_preflight import Preflight
from shared.models.crm_models import AllocationKeyModel, ConsumerModel, IterationModel
from shared.models.local_models import (
    AllocationKeyGeneratedModel,
    ConsumerGeneratedModel,
    GenerationModel,
    IterationGeneratedModel,
)


def to_generation_schema(generation: GenerationModel) -> Generation:
    return Generation(
        id=generation.id,
        name=generation.name,
        status=generation.status,
    )


def to_consumer_generated_schema(consumer: ConsumerGeneratedModel) -> ConsumerGenerated:
    return ConsumerGenerated(
        id=consumer.id,
        name=consumer.name,
        energy_allocated_percentage=consumer.energy_allocated_percentage,
    )


def to_iteration_generated_schema(
    iteration: IterationGeneratedModel,
) -> IterationGenerated:
    return IterationGenerated(
        id=iteration.id,
        energy_allocated_percentage=iteration.energy_allocated_percentage,
        consumers=[to_consumer_generated_schema(c) for c in iteration.consumers],
        surplus_total=iteration.surplus_total,
        number=iteration.number,
    )


def to_partial_allocation_key_generated_schema(
    allocation_key: AllocationKeyGeneratedModel,
) -> PartialAllocationKeyGenerated:
    return PartialAllocationKeyGenerated(
        id=allocation_key.id,
        name=allocation_key.name,
        description=allocation_key.description,
        surplus_total=sum([i.surplus_total for i in allocation_key.iterations]),
    )


def to_allocation_key_generated_schema(
    allocation_key: AllocationKeyGeneratedModel,
) -> AllocationKeyGenerated:
    return AllocationKeyGenerated(
        id=allocation_key.id,
        name=allocation_key.name,
        description=allocation_key.description,
        iterations=[to_iteration_generated_schema(i) for i in allocation_key.iterations],
        surplus_total=sum([i.surplus_total for i in allocation_key.iterations]),
    )


def to_consumer_crm(consumer: ConsumerGeneratedModel) -> ConsumerModel:
    return ConsumerModel(
        name=consumer.name,
        energy_allocated_percentage=consumer.energy_allocated_percentage,
        id_community=consumer.id_community,
    )


def to_iteration_crm(iteration: IterationGeneratedModel) -> IterationModel:
    return IterationModel(
        number=iteration.number,
        energy_allocated_percentage=iteration.energy_allocated_percentage,
        id_community=iteration.id_community,
        consumers=[to_consumer_crm(c) for c in iteration.consumers],
    )


def to_allocation_key_crm(
    allocation_key: AllocationKeyGeneratedModel,
) -> AllocationKeyModel:
    return AllocationKeyModel(
        name=allocation_key.name,
        description=allocation_key.description,
        id_community=allocation_key.id_community,
        iterations=[to_iteration_crm(i) for i in allocation_key.iterations],
    )


def to_crm_data_preview(preflight: Preflight, locale: str) -> CrmDataPreview:
    """Render a pre-flight verdict for the manager's screen.

    Blocker messages are localised here rather than in the service so that
    translation stays at the API edge — the worker reaches the same verdict via
    ``Preflight`` and needs no locale at all.
    """
    summary = preflight.summary
    return CrmDataPreview(
        can_generate=preflight.ok,
        # The participant count, not the raw meter count: injection-only sites
        # contribute production but never receive a share.
        meter_count=len(preflight.consumer_eans),
        reading_count=summary.total_rows,
        first_timestamp=summary.first_timestamp,
        last_timestamp=summary.last_timestamp,
        total_consumption_kwh=summary.total_consumption_kwh,
        total_injection_kwh=summary.total_injection_kwh,
        incomplete_meters=[
            IncompleteMeter(
                ean=e.ean,
                readings=e.distinct_ts,
                expected=summary.grid_size,
                missing=summary.grid_size - e.distinct_ts,
            )
            for e in summary.incomplete
        ],
        blockers=[
            PreviewBlocker(
                error_code=b.error.code,
                message=translate(b.error.key, locale=locale),
                detail=b.detail,
            )
            for b in preflight.blockers
        ],
    )
