import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from incidentops_ai.db import Base
from incidentops_ai.incidents.models import Environment, IncidentStatus, Severity


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _str_enum(enum_cls: type, name: str) -> Enum:
    # native_enum=False stores plain VARCHAR + CHECK constraint: adding a value later is a
    # simple migration, unlike ALTER TYPE on a Postgres enum.
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class IncidentRow(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    service: Mapped[str] = mapped_column(String(63), index=True)
    severity: Mapped[Severity] = mapped_column(_str_enum(Severity, "incident_severity"))
    environment: Mapped[Environment] = mapped_column(_str_enum(Environment, "incident_environment"))
    status: Mapped[IncidentStatus] = mapped_column(
        _str_enum(IncidentStatus, "incident_status"), default=IncidentStatus.OPEN
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )
