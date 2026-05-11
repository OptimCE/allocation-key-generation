from shared.database.with_community import with_community_scope
from shared.models.local_models import (
    GenerationModel,
    AllocationKeyGeneratedModel,
    IterationGeneratedModel,
)
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload


class GenerationRepository:
    def __init__(self, session):
        self.session = session

    async def create_generation(self, model: GenerationModel) -> GenerationModel:
        """Insert a new GenerationModel and flush so the caller can read its id.

        Caller (the service) owns the commit boundary so the row creation
        and any follow-up work (e.g. publishing the NATS event) share a
        single atomic unit.
        """
        self.session.add(model)
        await self.session.flush()
        return model

    async def get_list_generations(
        self, page: int, page_size: int, query_param: dict
    ) -> tuple[list[GenerationModel], int]:
        stmt = select(GenerationModel)
        stmt = with_community_scope(stmt, GenerationModel)

        if query_param:
            name = query_param.get("name")
            if name:
                stmt = stmt.where(GenerationModel.name.ilike(f"%{name}%"))
            status = query_param.get("status")
            if status:
                stmt = stmt.where(GenerationModel.status == int(status))
            sort_map = {
                "id": GenerationModel.id,
                "name": GenerationModel.name,
                "status": GenerationModel.status,
            }
            sort_clauses = []
            for key, column in sort_map.items():
                direction = query_param.get(f"sort_{key}")
                if not direction:
                    continue
                if direction.lower() == "desc":
                    sort_clauses.append(column.desc())
                elif direction.lower() == "asc":
                    sort_clauses.append(column.asc())
            if sort_clauses:
                stmt = stmt.order_by(*sort_clauses)
            else:
                stmt = stmt.order_by(GenerationModel.id.asc())
        else:
            stmt = stmt.order_by(GenerationModel.id.asc())
        total_result = await self.session.execute(
            select(func.count()).select_from(stmt.subquery())
        )
        total = total_result.scalar_one()

        rows_result = await self.session.execute(
            stmt.offset((page - 1) * page_size).limit(page_size)
        )
        rows = list(rows_result.scalars().all())

        return rows, total

    async def get_allocation_keys_list(
        self, id: int, page: int, page_size: int, query_param: dict
    ) -> tuple[list[AllocationKeyGeneratedModel], int]:
        surplus_subq = (
            select(
                IterationGeneratedModel.id_allocation_key,
                func.coalesce(func.sum(IterationGeneratedModel.surplus_total), 0).label(
                    "surplus"
                ),
            )
            .group_by(IterationGeneratedModel.id_allocation_key)
            .subquery()
        )

        stmt = (
            select(AllocationKeyGeneratedModel)
            .options(selectinload(AllocationKeyGeneratedModel.iterations))
            .where(AllocationKeyGeneratedModel.id_generation == id)
        )
        stmt = with_community_scope(stmt, AllocationKeyGeneratedModel)

        if query_param:
            name = query_param.get("name")
            if name:
                stmt = stmt.where(AllocationKeyGeneratedModel.name.ilike(f"%{name}%"))
            sort_map = {
                "id": AllocationKeyGeneratedModel.id,
                "name": AllocationKeyGeneratedModel.name,
                "surplus": surplus_subq.c.surplus,
            }
            sort_clauses = []
            for key, column in sort_map.items():
                direction = query_param.get(f"sort_{key}")
                if not direction:
                    continue
                if direction.lower() == "desc":
                    sort_clauses.append(column.desc())
                elif direction.lower() == "asc":
                    sort_clauses.append(column.asc())
            if sort_clauses:
                stmt = stmt.order_by(*sort_clauses)
            else:
                stmt = stmt.order_by(AllocationKeyGeneratedModel.id.asc())
        else:
            stmt = stmt.order_by(AllocationKeyGeneratedModel.id.asc())
        total_result = await self.session.execute(
            select(func.count()).select_from(stmt.subquery())
        )
        total = total_result.scalar_one()

        rows_result = await self.session.execute(
            stmt.offset((page - 1) * page_size).limit(page_size)
        )
        rows = list(rows_result.scalars().all())

        return rows, total

    async def get_allocation_key(
        self, id_key: int
    ) -> AllocationKeyGeneratedModel | None:
        stmt = (
            select(AllocationKeyGeneratedModel)
            .options(
                selectinload(AllocationKeyGeneratedModel.iterations).selectinload(
                    IterationGeneratedModel.consumers
                )
            )
            .where(AllocationKeyGeneratedModel.id == id_key)
        )
        stmt = with_community_scope(stmt, AllocationKeyGeneratedModel)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_generation(self, id_generation: int) -> GenerationModel | None:
        stmt = select(GenerationModel).where(GenerationModel.id == id_generation)
        stmt = with_community_scope(stmt, GenerationModel)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_generation(self, generation: GenerationModel) -> None:
        await self.session.delete(generation)
        await self.session.flush()

    async def delete_key(self, key: AllocationKeyGeneratedModel) -> None:
        await self.session.delete(key)
        await self.session.flush()
