import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, BigInteger, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database.database import CrmBase


class AllocationKeyModel(CrmBase):
    __tablename__ = "allocation_key"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    iterations: Mapped[list["IterationModel"]] = relationship(
        "IterationModel", lazy="select", back_populates="allocation_key"
    )
    id_community: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )


class IterationModel(CrmBase):
    __tablename__ = "iteration"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
    )
    number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    energy_allocated_percentage: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    consumers: Mapped[list["ConsumerModel"]] = relationship(
        "ConsumerModel", lazy="select", back_populates="iteration"
    )

    id_key: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("allocation_key.id"),
        nullable=False,
    )
    allocation_key: Mapped[AllocationKeyModel] = relationship(
        "AllocationKeyModel", lazy="select", back_populates="iterations"
    )
    id_community: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )


class ConsumerModel(CrmBase):
    __tablename__ = "consumer"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    energy_allocated_percentage: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    id_iteration: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("iteration.id"),
        nullable=False,
    )
    iteration: Mapped["IterationModel"] = relationship(
        "IterationModel", lazy="select", back_populates="consumers"
    )
    id_community: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )


class AppUserModel(CrmBase):
    # Partial mapping of the CRM `app_user` table: only the columns the audit
    # log service needs to denormalise the writer's identity onto each row.
    __tablename__ = "app_user"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    auth_user_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(256), nullable=False)


class AuditLogModel(CrmBase):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    id_community: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
