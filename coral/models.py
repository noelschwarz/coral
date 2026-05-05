"""SQLAlchemy models mirroring coral-engineering-spec §4.1.

The vault uses SQLCipher; schema creation is executed through :mod:`coral.vault`.
These models are the canonical description of tables and columns for tooling/tests.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for Coral ORM models."""


class SessionRow(Base):
    """Captured browser session state stored in the vault."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    origin: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    last_used_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    state_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    metadata_json: Mapped[str] = mapped_column("metadata", Text, nullable=False, default="{}")

    audit_events: Mapped[list[AuditLogRow]] = relationship(back_populates="session")


class PolicyRow(Base):
    """Per-site YAML policy blob."""

    __tablename__ = "policies"

    origin: Mapped[str] = mapped_column(String, primary_key=True)
    yaml_body: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class AuditLogRow(Base):
    """Append-only audit log entries."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_timestamp", "timestamp"),
        Index("idx_audit_session", "session_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("sessions.id"),
        nullable=True,
    )
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    origin: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False)

    session: Mapped[SessionRow | None] = relationship(back_populates="audit_events")


class ApiTokenRow(Base):
    """HTTP API bearer tokens (hashed at rest)."""

    __tablename__ = "api_tokens"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    last_used_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)


def orm_metadata() -> MetaData:
    """Return SQLAlchemy metadata for all Coral tables."""
    return Base.metadata


def schema_table_names() -> list[str]:
    """Return schema table names in a stable order for migrations."""
    # SQLAlchemy preserves class definition order for metadata.sorted_tables in many cases,
    # but we keep an explicit list for clarity.
    return ["sessions", "policies", "audit_log", "api_tokens"]


def model_dict_for_testing() -> dict[str, Any]:
    """Lightweight introspection helper for smoke tests."""
    return {"tables": schema_table_names()}
