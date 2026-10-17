"""AIENG-59 / H41: tokens are verified on the server; every forged or stale token is refused."""

import base64
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from incidentops_ai.auth.principal import PERMISSIONS, Principal, can
from incidentops_ai.auth.tokens import InvalidToken, TokenVerifier, mint

SECRET = "test-jwt-secret-0123456789abcdef0123"
ISSUER = "https://idp.test"

pytestmark = pytest.mark.redteam


def claims(**overrides) -> dict:
    now = int(time.time())
    return {
        "sub": "u-1",
        "name": "Amara Okafor",
        "tid": "default",
        "roles": ["approver"],
        "iss": ISSUER,
        "aud": "incidentops",
        "iat": now,
        "exp": now + 300,
    } | overrides


@pytest.fixture(scope="module")
def rsa_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public_pem.decode()


def test_a_valid_token_becomes_a_principal():
    principal = TokenVerifier(secret=SECRET, issuer=ISSUER).verify(
        jwt.encode(claims(), SECRET, algorithm="HS256")
    )

    assert principal == Principal("u-1", "Amara Okafor", "default", frozenset({"approver"}))


def test_an_rs256_token_verifies_with_the_public_key(rsa_keys):
    private, public = rsa_keys

    principal = TokenVerifier(public_key=public, issuer=ISSUER).verify(
        jwt.encode(claims(), private, algorithm="RS256")
    )

    assert principal.user_id == "u-1"


def unsigned(payload: dict) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(payload)}."


def tampered() -> str:
    header, _, signature = jwt.encode(claims(), SECRET, algorithm="HS256").split(".")
    escalated = claims(roles=["admin"], tid="acme")
    body = base64.urlsafe_b64encode(json.dumps(escalated).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


@pytest.mark.parametrize(
    "make",
    [
        lambda keys: unsigned(claims()),
        lambda keys: jwt.encode(claims(exp=int(time.time()) - 3600), SECRET, algorithm="HS256"),
        lambda keys: jwt.encode(claims(aud="another-app"), SECRET, algorithm="HS256"),
        lambda keys: jwt.encode(claims(iss="https://evil.test"), SECRET, algorithm="HS256"),
        lambda keys: tampered(),
        lambda keys: jwt.encode(
            {k: v for k, v in claims().items() if k != "tid"}, SECRET, algorithm="HS256"
        ),
    ],
    ids=["alg-none", "expired", "wrong-audience", "wrong-issuer", "tampered", "no-tenant"],
)
def test_forged_and_stale_tokens_are_refused(rsa_keys, make):
    with pytest.raises(InvalidToken):
        TokenVerifier(secret=SECRET, issuer=ISSUER).verify(make(rsa_keys))


def test_the_key_confusion_attack_is_refused(rsa_keys):
    # With RS256 configured, an attacker signs HS256 using the (public) key as the HMAC secret.
    _, public = rsa_keys
    import hashlib
    import hmac

    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=")
    body = base64.urlsafe_b64encode(json.dumps(claims(roles=["admin"])).encode()).rstrip(b"=")
    signing_input = header + b"." + body
    signature = base64.urlsafe_b64encode(
        hmac.new(public.encode(), signing_input, hashlib.sha256).digest()
    ).rstrip(b"=")
    forged = (signing_input + b"." + signature).decode()

    with pytest.raises(InvalidToken):
        TokenVerifier(public_key=public, issuer=ISSUER).verify(forged)


def test_unknown_roles_grant_nothing():
    principal = TokenVerifier(secret=SECRET, issuer=ISSUER).verify(
        jwt.encode(claims(roles=["superuser", "viewer"]), SECRET, algorithm="HS256")
    )

    assert principal.roles == frozenset({"viewer"})


def test_mint_makes_tokens_the_verifier_accepts():
    token = mint(
        SECRET, sub="u-2", name="Lena Fischer", tenant="acme", roles=["viewer"], issuer=ISSUER
    )

    assert TokenVerifier(secret=SECRET, issuer=ISSUER).verify(token).tenant_id == "acme"


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("viewer", {"incidents:read", "triage:read", "memory:read"}),
        (
            "responder",
            {
                "incidents:read",
                "incidents:write",
                "analysis:run",
                "triage:read",
                "triage:run",
                "memory:read",
                "ops:query",
            },
        ),
        (
            "approver",
            {
                "incidents:read",
                "incidents:write",
                "analysis:run",
                "triage:read",
                "triage:run",
                "triage:decide",
                "memory:read",
                "memory:write",
                "ops:query",
            },
        ),
        ("admin", set(PERMISSIONS)),
    ],
)
def test_the_permission_table(role, allowed):
    principal = Principal("u", "U", "default", frozenset({role}))

    assert {p for p in PERMISSIONS if can(principal, p)} == allowed
