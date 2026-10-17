"""The routing rule (AIENG-44): recommend, or send to a person, decided by code, not by a model.

Every reason is a stable code a reviewer can read. The rule is the one written in
docs/roadmap-w07.md before the workflow existed.
"""

from collections.abc import Mapping

REVIEW_REASONS = (
    "model_failed",  # extraction or diagnosis did not succeed
    "no_answer",  # the diagnosis is an explicit no-answer
    "unsupported_causes",  # the model proposed causes that failed the citation check
    "extraction_needs_review",  # the W02 review rules flag the extraction
    "sev1",  # high impact: a person sees it before anyone acts
)


def review_reasons(state: Mapping) -> list[str]:
    reasons: list[str] = []
    if (
        state.get("extraction_status") != "succeeded"
        or state.get("diagnosis_status") != "succeeded"
    ):
        reasons.append("model_failed")
    diagnosis = state.get("diagnosis")
    if diagnosis and diagnosis["status"] == "no_answer":
        reasons.append("no_answer")
    if diagnosis and diagnosis["dropped_causes"] > 0:
        reasons.append("unsupported_causes")
    review = state.get("extraction_review")
    if review and review["required"]:
        reasons.append("extraction_needs_review")
    if state["incident"]["severity"] == "SEV1":
        reasons.append("sev1")
    return reasons
