"""Mint a development token: uses INCIDENTOPS_JWT_SECRET and INCIDENTOPS_JWT_ISSUER.

    uv run python -m incidentops_ai.auth.dev_token --sub u-1 --name "Amara Okafor" \
        --tenant default --role approver
"""

import argparse

from incidentops_ai.auth.tokens import mint
from incidentops_ai.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint an HS256 token for local development.")
    parser.add_argument("--sub", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--role", action="append", required=True, dest="roles")
    parser.add_argument("--ttl", type=int, default=3600)
    args = parser.parse_args()
    settings = Settings()
    if settings.jwt_secret is None:
        raise SystemExit("INCIDENTOPS_JWT_SECRET is not set")
    print(
        mint(
            settings.jwt_secret.get_secret_value(),
            sub=args.sub,
            name=args.name,
            tenant=args.tenant,
            roles=args.roles,
            issuer=settings.jwt_issuer,
            ttl_s=args.ttl,
        )
    )


if __name__ == "__main__":
    main()
