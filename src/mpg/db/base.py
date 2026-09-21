"""Declarative base and shared column types."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


#: JSONB on PostgreSQL, plain JSON elsewhere, so the suite can run on SQLite
#: without a server while production keeps the indexable Postgres type.
JSONType = JSONB().with_variant(JSON(), "sqlite")
