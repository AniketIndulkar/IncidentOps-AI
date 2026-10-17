"""Verifying bearer JWTs from the identity provider (AIENG-59).

The accepted algorithm follows the configured key: HS256 with a shared secret, or RS256 with a PEM
public key, never both, never `none`. A token signed with HS256 using the RS256 public key as its
secret (the key-confusion attack) is therefore refused. Required claims: exp, iat, sub, iss, aud,
and the tenant (`tid`). Roles IncidentOps does not know are dropped: they grant nothing.
"""

import time

import jwt

from incidentops_ai.auth.principal import ROLES, Principal

AUDIENCE = "incidentops"
LEEWAY_S = 30


class InvalidToken(Exception):
    """Why is deliberately vague: the caller learns only that the token was not accepted."""


class TokenVerifier:
    def __init__(
        self,
        *,
        secret: str | None = None,
        public_key: str | None = None,
        issuer: str,
        audience: str = AUDIENCE,
    ) -> None:
        if (secret is None) == (public_key is None):
            raise ValueError("configure exactly one of a JWT secret or a JWT public key")
        self._key = secret if secret is not None else public_key
        self._algorithms = ["HS256"] if secret is not None else ["RS256"]
        self._issuer, self._audience = issuer, audience

    def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                leeway=LEEWAY_S,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as error:
            raise InvalidToken("token not accepted") from error
        tenant = claims.get("tid")
        roles = claims.get("roles", [])
        if not isinstance(tenant, str) or not tenant or not isinstance(roles, list):
            raise InvalidToken("token not accepted")
        return Principal(
            user_id=str(claims["sub"]),
            name=str(claims.get("name") or claims["sub"]),
            tenant_id=tenant,
            roles=frozenset(r for r in roles if r in ROLES),
        )


def mint(
    secret: str,
    *,
    sub: str,
    name: str,
    tenant: str,
    roles: list[str],
    issuer: str,
    ttl_s: int = 3600,
) -> str:
    """An HS256 token for local development and tests (production tokens come from the IdP)."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "name": name,
        "tid": tenant,
        "roles": roles,
        "iss": issuer,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + ttl_s,
    }
    return jwt.encode(payload, secret, algorithm="HS256")
