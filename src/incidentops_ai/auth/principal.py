"""The authenticated caller, the roles they hold and what the roles allow (AIENG-60)."""

from dataclasses import dataclass

PERMISSIONS = (
    "incidents:read",
    "incidents:write",
    "analysis:run",
    "triage:read",
    "triage:run",
    "triage:decide",
    "triage:resume",
    "memory:read",
    "memory:write",
    "ops:query",
)

_VIEWER = frozenset({"incidents:read", "triage:read", "memory:read"})
_RESPONDER = _VIEWER | {"incidents:write", "analysis:run", "triage:run", "ops:query"}
_APPROVER = _RESPONDER | {"triage:decide", "memory:write"}
ROLES: dict[str, frozenset[str]] = {
    "viewer": _VIEWER,
    "responder": frozenset(_RESPONDER),
    "approver": frozenset(_APPROVER),
    "admin": frozenset(PERMISSIONS),
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    name: str
    tenant_id: str
    roles: frozenset[str]

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset().union(*(ROLES[r] for r in self.roles if r in ROLES))


def can(principal: Principal, permission: str) -> bool:
    return permission in principal.permissions
