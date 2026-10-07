"""Incident domain types and API contracts.

IncidentCreate is what a client may send; IncidentRead is what the server returns.
Server-owned fields (id, status, timestamps) only exist on IncidentRead, so clients cannot set them.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    SEV1 = "SEV1"  # critical: full outage / data loss
    SEV2 = "SEV2"  # major: significant degradation
    SEV3 = "SEV3"  # minor: limited impact, workaround exists
    SEV4 = "SEV4"  # low: cosmetic / no user impact


class Environment(StrEnum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"


Title = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(min_length=1, max_length=10_000)]
# Lowercase kebab-case service id, e.g. "checkout-api". Matches how services are named in the graph later.
ServiceName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]


class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: Title
    description: Description
    service: ServiceName
    severity: Severity
    environment: Environment


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    service: str
    severity: Severity
    environment: Environment
    status: IncidentStatus
    created_at: datetime
    updated_at: datetime
