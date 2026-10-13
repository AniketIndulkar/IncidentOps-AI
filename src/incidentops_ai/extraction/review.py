"""Decides whether a valid extraction still needs a human to look at it.

"Low confidence" is decided here by fixed rules over the typed result, not by asking the model
how confident it feels: a model's self-reported confidence is not calibrated, while these rules
are deterministic, testable and explainable to the person doing the review.

This only produces the decision. Routing to a reviewer and blocking on approval is W08.
"""

from enum import StrEnum

from pydantic import BaseModel

from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.incidents.models import Severity

MANY_UNKNOWNS = 4  # this many open questions means the report is too thin to act on


class ReviewReason(StrEnum):
    NO_SERVICE = "no_service_identified"
    SERVICE_INFERRED = "service_inferred_not_stated"
    NO_ENVIRONMENT = "environment_not_stated"
    MANY_UNKNOWNS = "many_open_questions"
    HIGH_SEVERITY = "high_severity_needs_confirmation"
    NO_SYMPTOMS = "no_symptoms_found"


class ReviewDecision(BaseModel):
    required: bool
    reasons: list[ReviewReason]


def assess(extraction: IncidentExtraction) -> ReviewDecision:
    reasons: list[ReviewReason] = []
    if extraction.service.name is None:
        reasons.append(ReviewReason.NO_SERVICE)
    elif not extraction.service.stated_explicitly:
        reasons.append(ReviewReason.SERVICE_INFERRED)
    if extraction.environment is None:
        reasons.append(ReviewReason.NO_ENVIRONMENT)
    if not extraction.symptoms:
        reasons.append(ReviewReason.NO_SYMPTOMS)
    if len(extraction.missing_information) >= MANY_UNKNOWNS:
        reasons.append(ReviewReason.MANY_UNKNOWNS)
    # A SEV1 suggestion pages people; a model never makes that call alone.
    if extraction.severity_candidate.level is Severity.SEV1:
        reasons.append(ReviewReason.HIGH_SEVERITY)
    return ReviewDecision(required=bool(reasons), reasons=reasons)
