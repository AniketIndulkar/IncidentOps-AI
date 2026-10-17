"""add the ops schema (read-only text-to-SQL source) and its reader role

Revision ID: b8f0a2c4e6d8
Revises: a6e8c0d2f4b6
Create Date: 2026-10-17 21:00:00.000000

The `ops` schema stands in for an operations database the team already has: services,
deployments, alerts, on-call shifts and error budgets, seeded with fixed data (the W09 SQL
questions are answered from it). `incidentops_ops_reader` (NOLOGIN) may read it and nothing else;
a login user is granted that role in deployment (see docs/notes/integration-contracts.md).
Alembic does not autogenerate this schema; this migration is its only definition.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8f0a2c4e6d8"
down_revision: str | Sequence[str] | None = "a6e8c0d2f4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READER = "incidentops_ops_reader"

SERVICES = [
    ("orders-api", 1, "commerce", "Amara Okafor"),
    ("checkout-api", 1, "commerce", "Amara Okafor"),
    ("payments-gateway", 1, "payments", "Lena Fischer"),
    ("billing-service", 1, "payments", "Lena Fischer"),
    ("notification-worker", 2, "platform", "Kenji Watanabe"),
    ("session-service", 2, "identity", "Priya Raman"),
    ("internal-auth-proxy", 2, "identity", "Priya Raman"),
    ("search-indexer", 3, "discovery", "Diego Alvarez"),
    ("web-dashboard", 3, "discovery", "Diego Alvarez"),
]

DEPLOYMENTS = [
    ("orders-api", "v4.10.0", "2026-10-02 10:00", "sam.lee", "succeeded"),
    ("orders-api", "v4.11.0", "2026-10-09 14:30", "sam.lee", "succeeded"),
    ("orders-api", "v4.12.0", "2026-10-21 09:15", "nora.kim", "failed"),
    ("orders-api", "v4.12.1", "2026-10-21 11:40", "nora.kim", "succeeded"),
    ("orders-api", "v4.13.0", "2026-11-03 10:05", "sam.lee", "succeeded"),
    ("checkout-api", "v2.29.0", "2026-09-28 16:00", "omar.haddad", "rolled_back"),
    ("checkout-api", "v2.30.0", "2026-10-06 13:00", "omar.haddad", "succeeded"),
    ("checkout-api", "v2.31.0", "2026-10-15 15:20", "omar.haddad", "rolled_back"),
    ("checkout-api", "v2.31.1", "2026-10-16 10:10", "sam.lee", "succeeded"),
    ("checkout-api", "v2.32.0", "2026-10-28 12:00", "omar.haddad", "succeeded"),
    ("payments-gateway", "v7.4.0", "2026-10-05 08:30", "lena.fischer", "succeeded"),
    ("payments-gateway", "v7.5.0", "2026-10-19 09:00", "lena.fischer", "succeeded"),
    ("payments-gateway", "v7.5.1", "2026-10-22 17:45", "ivan.petrov", "succeeded"),
    ("payments-gateway", "v7.6.0", "2026-10-29 08:15", "ivan.petrov", "failed"),
    ("payments-gateway", "v7.6.1", "2026-10-30 09:30", "ivan.petrov", "succeeded"),
    ("billing-service", "v3.2.0", "2026-10-08 11:00", "ivan.petrov", "succeeded"),
    ("billing-service", "v3.3.0", "2026-10-26 10:30", "ivan.petrov", "succeeded"),
    ("notification-worker", "v1.18.0", "2026-10-03 07:00", "kenji.watanabe", "succeeded"),
    ("notification-worker", "v1.19.0", "2026-10-24 07:30", "kenji.watanabe", "rolled_back"),
    ("session-service", "v5.1.0", "2026-10-12 13:45", "priya.raman", "succeeded"),
    ("internal-auth-proxy", "v2.0.3", "2026-10-11 09:00", "priya.raman", "succeeded"),
    ("search-indexer", "v0.9.0", "2026-10-14 10:00", "diego.alvarez", "failed"),
    ("search-indexer", "v0.9.1", "2026-10-14 15:00", "diego.alvarez", "succeeded"),
    ("web-dashboard", "v12.0.0", "2026-10-20 16:00", "diego.alvarez", "succeeded"),
]

ALERTS = [
    ("orders-api", "critical", "DB pool exhausted", "2026-10-21 09:40", "2026-10-21 10:30"),
    ("orders-api", "warning", "p95 latency above 2 s", "2026-10-21 09:20", "2026-10-21 09:50"),
    ("checkout-api", "critical", "5xx rate above 5%", "2026-10-15 15:40", "2026-10-15 16:30"),
    ("checkout-api", "warning", "Readiness probe flapping", "2026-10-15 15:35", "2026-10-15 17:00"),
    (
        "payments-gateway",
        "critical",
        "Card authorisation timeouts in eu-west-1",
        "2026-10-29 08:40",
        None,
    ),
    (
        "payments-gateway",
        "warning",
        "Acquirer latency high",
        "2026-10-22 18:00",
        "2026-10-22 18:20",
    ),
    (
        "billing-service",
        "critical",
        "Duplicate charges detected",
        "2026-10-26 11:15",
        "2026-10-26 13:00",
    ),
    ("notification-worker", "critical", "Kafka brokers unreachable", "2026-10-24 07:45", None),
    (
        "notification-worker",
        "info",
        "Consumer lag above 1k",
        "2026-10-24 07:40",
        "2026-10-24 09:00",
    ),
    ("search-indexer", "warning", "Pods OOMKilled", "2026-10-14 10:20", "2026-10-14 10:50"),
    (
        "session-service",
        "warning",
        "Session store errors in eu-central",
        "2026-10-12 14:00",
        "2026-10-12 16:30",
    ),
    ("web-dashboard", "info", "Static asset 404s", "2026-10-20 16:30", "2026-10-20 16:45"),
    (
        "internal-auth-proxy",
        "critical",
        "TLS certificate expires in 3 days",
        "2026-11-02 09:00",
        None,
    ),
    ("orders-api", "warning", "Pool usage above 80%", "2026-11-03 02:10", "2026-11-03 02:40"),
    ("checkout-api", "info", "Deployment started", "2026-10-28 12:00", "2026-10-28 12:01"),
    ("payments-gateway", "warning", "Error budget burn rate high", "2026-10-30 10:00", None),
]

ROTAS = {
    "commerce": ("sam.lee", "omar.haddad"),
    "payments": ("lena.fischer", "ivan.petrov"),
    "platform": ("kenji.watanabe", "mia.chen"),
    "identity": ("priya.raman", "tom.berg"),
    "discovery": ("diego.alvarez", "nora.kim"),
}

# (service, SLO, September availability, October availability)
BUDGETS = [
    ("orders-api", 0.999, 0.9995, 0.9985),
    ("checkout-api", 0.999, 0.9993, 0.9991),
    ("payments-gateway", 0.9995, 0.9997, 0.9990),
    ("billing-service", 0.9995, 0.9998, 0.9996),
    ("notification-worker", 0.995, 0.9981, 0.9962),
    ("session-service", 0.995, 0.9975, 0.9970),
    ("internal-auth-proxy", 0.999, 0.9998, 0.9995),
    ("search-indexer", 0.99, 0.9955, 0.9940),
    ("web-dashboard", 0.99, 0.9990, 0.9980),
]


def _ts(value: str | None) -> str | None:
    return None if value is None else f"{value}+00"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA ops")
    services = op.create_table(
        "services",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("team", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        schema="ops",
    )
    deployments = op.create_table(
        "deployments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service", sa.Text(), sa.ForeignKey("ops.services.name"), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deployed_by", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        schema="ops",
    )
    alerts = op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service", sa.Text(), sa.ForeignKey("ops.services.name"), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        schema="ops",
    )
    shifts = op.create_table(
        "oncall_shifts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team", sa.Text(), nullable=False),
        sa.Column("engineer", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        schema="ops",
    )
    budgets = op.create_table(
        "error_budget",
        sa.Column("service", sa.Text(), sa.ForeignKey("ops.services.name"), primary_key=True),
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("slo_target", sa.Numeric(6, 4), nullable=False),
        sa.Column("availability", sa.Numeric(6, 4), nullable=False),
        sa.Column("budget_remaining", sa.Numeric(6, 2), nullable=False),
        schema="ops",
    )

    op.bulk_insert(
        services, [dict(zip(("name", "tier", "team", "owner"), s, strict=True)) for s in SERVICES]
    )
    op.execute(
        deployments.insert().values(
            [
                {
                    "id": i,
                    "service": s,
                    "version": v,
                    "deployed_at": sa.text(f"'{_ts(at)}'::timestamptz"),
                    "deployed_by": by,
                    "status": status,
                }
                for i, (s, v, at, by, status) in enumerate(DEPLOYMENTS, start=1)
            ]
        )
    )
    op.execute(
        alerts.insert().values(
            [
                {
                    "id": i,
                    "service": s,
                    "severity": sev,
                    "title": title,
                    "fired_at": sa.text(f"'{_ts(fired)}'::timestamptz"),
                    "resolved_at": sa.text(f"'{_ts(done)}'::timestamptz") if done else None,
                }
                for i, (s, sev, title, fired, done) in enumerate(ALERTS, start=1)
            ]
        )
    )
    rows, number = [], 0
    for team, (first, second) in ROTAS.items():
        for week in range(10):  # weekly shifts, Monday 09:00 UTC, 28 Sep to 7 Dec 2026
            start = datetime(2026, 9, 28, 9, 0, tzinfo=UTC) + timedelta(days=7 * week)
            number += 1
            rows.append(
                {
                    "id": number,
                    "team": team,
                    "engineer": (first, second)[week % 2],
                    "starts_at": sa.text(f"'{start:%Y-%m-%d %H:%M}+00'::timestamptz"),
                    "ends_at": sa.text(
                        f"'{start + timedelta(days=7):%Y-%m-%d %H:%M}+00'::timestamptz"
                    ),
                }
            )
    op.execute(shifts.insert().values(rows))
    budget_rows = []
    for service, slo, september, october in BUDGETS:
        for month, availability in ((date(2026, 9, 1), september), (date(2026, 10, 1), october)):
            remaining = round(1 - (1 - availability) / (1 - slo), 2)
            budget_rows.append(
                {
                    "service": service,
                    "month": month,
                    "slo_target": slo,
                    "availability": availability,
                    "budget_remaining": remaining,
                }
            )
    op.bulk_insert(budgets, budget_rows)

    # The reader may see the ops schema and nothing else of ours.
    op.execute(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{READER}') "
        f"THEN CREATE ROLE {READER} NOLOGIN; END IF; END $$"
    )
    op.execute(f"GRANT USAGE ON SCHEMA ops TO {READER}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA ops TO {READER}")


def downgrade() -> None:
    """Downgrade schema. The role is cluster-wide and may be used by other databases: it stays."""
    op.execute("DROP SCHEMA ops CASCADE")
