"""Signed approvals for the ticket write (AIENG-52).

The host (IncidentOps) signs what a person approved: the run, the decision's audit event, the
reviewer, and a fingerprint of the exact ticket arguments. The ticket server recomputes the
fingerprint from the arguments it received and checks the signature. So the write runs only for
the arguments that were approved, and only with an approval the host recorded: a model never holds
the secret, and a changed title or body breaks the signature.
"""

import hashlib
import hmac
import json

from pydantic import BaseModel

TICKET_FIELDS = ("title", "body", "service", "severity", "idempotency_key")


class Approval(BaseModel):
    run_id: str
    decision_id: str
    reviewer: str
    signature: str


def ticket_fingerprint(arguments: dict) -> str:
    canonical = json.dumps(
        {field: arguments[field] for field in TICKET_FIELDS}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _message(run_id: str, decision_id: str, reviewer: str, fingerprint: str) -> bytes:
    return json.dumps([run_id, decision_id, reviewer, fingerprint]).encode()


def sign(secret: str, *, run_id: str, decision_id: str, reviewer: str, arguments: dict) -> Approval:
    message = _message(run_id, decision_id, reviewer, ticket_fingerprint(arguments))
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return Approval(run_id=run_id, decision_id=decision_id, reviewer=reviewer, signature=signature)


def verify(secret: str, approval: Approval, arguments: dict) -> bool:
    """True only for a signature over these exact arguments, for the run they are keyed on."""
    if approval.run_id != arguments["idempotency_key"]:
        return False
    expected = sign(
        secret,
        run_id=approval.run_id,
        decision_id=approval.decision_id,
        reviewer=approval.reviewer,
        arguments=arguments,
    )
    return hmac.compare_digest(expected.signature, approval.signature)
