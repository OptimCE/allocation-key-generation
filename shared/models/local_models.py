import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, TIMESTAMP, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database.database import LocalBase
from shared.const import GenerationStatus


class GenerationModel(LocalBase):
    __tablename__ = "generation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Display + ownership ---
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    id_community: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- Source data ---
    file_url: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Name of the column inside the uploaded file that holds the shared
    # production profile (the "injection"). Shared across all algorithms.
    injection_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # --- Algorithm snapshot ---
    # Keyed to algorithms.registry at creation time; version is snapshotted
    # so old rows remain interpretable after a metadata bump. ``inputs`` is
    # the full validated payload (Pydantic dump of the algorithm's input
    # schema) and backs both replay and UI display of parameters.
    algorithm_name: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # --- Execution state ---
    status: Mapped[GenerationStatus] = mapped_column(
        Integer, nullable=False, default=GenerationStatus.PENDING
    )
    # Populated by the worker on failure so the UI can surface the cause
    # without needing to hit logs. Nullable because success rows have none.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
    )


class AllocationKeyGeneratedModel(LocalBase):
    __tablename__ = "allocation_key_generated"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)

    # Denormalised total surplus across all iterations of this key.
    # Used by ``PartialAllocationKeyGenerated`` for listing/sorting without
    # having to load the iteration subtree.
    surplus_total: Mapped[float] = mapped_column(Float, nullable=False)

    id_generation: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("generation.id", ondelete="CASCADE"),
        nullable=False,
    )
    id_community: Mapped[int] = mapped_column(Integer, nullable=False)

    iterations: Mapped[list["IterationGeneratedModel"]] = relationship(
        "IterationGeneratedModel",
        lazy="select",
        back_populates="allocation_key",
        cascade="all, delete-orphan",
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
    )


class IterationGeneratedModel(LocalBase):
    __tablename__ = "iteration_generated"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    energy_allocated_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    surplus_total: Mapped[float] = mapped_column(Float, nullable=False)

    id_allocation_key: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("allocation_key_generated.id", ondelete="CASCADE"),
        nullable=False,
    )
    id_community: Mapped[int] = mapped_column(Integer, nullable=False)

    allocation_key: Mapped[AllocationKeyGeneratedModel] = relationship(
        "AllocationKeyGeneratedModel",
        lazy="select",
        back_populates="iterations",
    )
    consumers: Mapped[list["ConsumerGeneratedModel"]] = relationship(
        "ConsumerGeneratedModel",
        lazy="select",
        back_populates="iteration",
        cascade="all, delete-orphan",
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
    )


class ConsumerGeneratedModel(LocalBase):
    __tablename__ = "consumer_generated"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    energy_allocated_percentage: Mapped[float] = mapped_column(Float, nullable=False)

    id_iteration: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("iteration_generated.id", ondelete="CASCADE"),
        nullable=False,
    )
    id_community: Mapped[int] = mapped_column(Integer, nullable=False)

    iteration: Mapped["IterationGeneratedModel"] = relationship(
        "IterationGeneratedModel", lazy="select", back_populates="consumers"
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
    )
