import datetime
from typing import cast

import factory

from shared.const import GenerationStatus
from shared.models.local_models import (
    AllocationKeyGeneratedModel,
    ConsumerGeneratedModel,
    GenerationModel,
    IterationGeneratedModel,
)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


class GenerationFactory(factory.Factory):
    class Meta:
        model = GenerationModel

    name = factory.Sequence(lambda n: f"Generation {n}")
    file_storage_key = factory.Sequence(lambda n: f"allocations/1/test-{n}/data.csv")
    file_name = "data.csv"
    injection_name = "production"
    algorithm_name = "brute_force"
    algorithm_version = "1.0"
    inputs = factory.LazyFunction(lambda: {"iterations": 1})
    status = GenerationStatus.PENDING
    created_at = factory.LazyFunction(_now)
    updated_at = factory.LazyFunction(_now)


class AllocationKeyGeneratedFactory(factory.Factory):
    class Meta:
        model = AllocationKeyGeneratedModel

    name = factory.Sequence(lambda n: f"Key {n}")
    description = factory.Sequence(lambda n: f"Description {n}")
    surplus_total = 0.0
    created_at = factory.LazyFunction(_now)
    updated_at = factory.LazyFunction(_now)


class IterationGeneratedFactory(factory.Factory):
    class Meta:
        model = IterationGeneratedModel

    number = factory.Sequence(lambda n: n)
    energy_allocated_percentage = 50.0
    surplus_total = 1.0
    created_at = factory.LazyFunction(_now)
    updated_at = factory.LazyFunction(_now)


class ConsumerGeneratedFactory(factory.Factory):
    class Meta:
        model = ConsumerGeneratedModel

    name = factory.Sequence(lambda n: f"Consumer {n}")
    energy_allocated_percentage = 25.0
    created_at = factory.LazyFunction(_now)
    updated_at = factory.LazyFunction(_now)


# ---------------------------------------------------------------------------
# async helpers — flush only, never commit (the db_session fixture owns the
# outer transaction and rolls it back at end of test).
# ---------------------------------------------------------------------------


async def create_generation(session, *, id_community: int, **kwargs) -> GenerationModel:
    gen = cast(GenerationModel, GenerationFactory.build(id_community=id_community, **kwargs))
    session.add(gen)
    await session.flush()
    return gen


async def create_allocation_key_generated(
    session,
    *,
    id_community: int,
    id_generation: int | None = None,
    **kwargs,
) -> AllocationKeyGeneratedModel:
    """Create an AllocationKeyGenerated row.

    If `id_generation` is not provided, a parent GenerationModel is created
    on the fly (FK auto-creation per fastapi-testing skill §10.3).
    """
    if id_generation is None:
        gen = await create_generation(session, id_community=id_community)
        id_generation = gen.id

    key = cast(
        AllocationKeyGeneratedModel,
        AllocationKeyGeneratedFactory.build(
            id_community=id_community,
            id_generation=id_generation,
            **kwargs,
        ),
    )
    session.add(key)
    await session.flush()
    return key


async def create_iteration_generated(
    session,
    *,
    id_community: int,
    id_allocation_key: int,
    **kwargs,
) -> IterationGeneratedModel:
    iteration = cast(
        IterationGeneratedModel,
        IterationGeneratedFactory.build(
            id_community=id_community,
            id_allocation_key=id_allocation_key,
            **kwargs,
        ),
    )
    session.add(iteration)
    await session.flush()
    return iteration


async def create_consumer_generated(
    session,
    *,
    id_community: int,
    id_iteration: int,
    **kwargs,
) -> ConsumerGeneratedModel:
    consumer = cast(
        ConsumerGeneratedModel,
        ConsumerGeneratedFactory.build(
            id_community=id_community,
            id_iteration=id_iteration,
            **kwargs,
        ),
    )
    session.add(consumer)
    await session.flush()
    return consumer


async def create_full_key_tree(
    session,
    *,
    id_community: int,
    id_generation: int | None = None,
    iterations: int = 2,
    consumers_per_iteration: int = 2,
    iteration_surplus: float = 1.5,
    **key_kwargs,
) -> AllocationKeyGeneratedModel:
    """Build an AllocationKey + N iterations + M consumers per iteration.

    Used by GET /generation/key/{id_key}, GET /generation/{id}, and
    POST /generation/save tests that need the full nested shape.

    The returned key has a plain-list `iteration_ids` attribute set for
    tests that need to query child rows post-delete — accessing
    `key.iterations` is a lazy-load and would trigger a sync IO call
    inside an async test (MissingGreenlet).
    """
    key = await create_allocation_key_generated(
        session,
        id_community=id_community,
        id_generation=id_generation,
        surplus_total=iteration_surplus * iterations,
        **key_kwargs,
    )

    iteration_ids: list[int] = []
    for i in range(iterations):
        iteration = await create_iteration_generated(
            session,
            id_community=id_community,
            id_allocation_key=key.id,
            number=i,
            surplus_total=iteration_surplus,
        )
        iteration_ids.append(iteration.id)
        for _ in range(consumers_per_iteration):
            await create_consumer_generated(
                session,
                id_community=id_community,
                id_iteration=iteration.id,
            )

    # Attach the ids as plain attributes so tests can reference them without
    # touching the lazy-loaded `key.iterations` relationship. The ORM model
    # doesn't declare iteration_ids, so suppress the attribute-defined check.
    key.iteration_ids = iteration_ids  # type: ignore[attr-defined]
    return key
