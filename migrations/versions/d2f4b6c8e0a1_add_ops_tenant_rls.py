"""ops tables per tenant, with row-level security keyed on the database login

Revision ID: d2f4b6c8e0a1
Revises: c1e3a5b7d9f0
Create Date: 2026-10-17 23:00:00.000000

Every ops table gains `tenant_id`, and a second tenant ("acme") is seeded. Reader logins are mapped
to tenants in `ops.tenant_readers`; `ops.reader_tenant()` (SECURITY DEFINER) returns the tenant of
the session's login, and a policy on each table admits only that tenant's rows to the reader role.
The policy reads `session_user`, which no query can change (W09 showed `set_config` runs as the
reader, so a session setting would not do). Mapping a login is a deployment step:
    INSERT INTO ops.tenant_readers VALUES ('<login>', '<tenant>');
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2f4b6c8e0a1"
down_revision: str | Sequence[str] | None = "c1e3a5b7d9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READER = "incidentops_ops_reader"
TABLES = ("services", "deployments", "alerts", "oncall_shifts", "error_budget")

ACME = """
INSERT INTO ops.services (name, tier, team, owner, tenant_id) VALUES
    ('acme-storefront', 1, 'web', 'Rosa Diaz', 'acme'),
    ('acme-payments', 1, 'payments', 'Rosa Diaz', 'acme');
INSERT INTO ops.deployments (id, service, version, deployed_at, deployed_by, status, tenant_id) VALUES
    (101, 'acme-storefront', 'v9.1.0', '2026-10-10 10:00+00', 'jo.park', 'succeeded', 'acme'),
    (102, 'acme-payments', 'v3.0.0', '2026-10-18 12:00+00', 'jo.park', 'rolled_back', 'acme');
INSERT INTO ops.alerts (id, service, severity, title, fired_at, resolved_at, tenant_id) VALUES
    (101, 'acme-payments', 'critical', 'ACME card declines spiking', '2026-10-18 12:20+00', NULL,
     'acme');
INSERT INTO ops.oncall_shifts (id, team, engineer, starts_at, ends_at, tenant_id) VALUES
    (101, 'payments', 'jo.park', '2026-10-12 09:00+00', '2026-10-19 09:00+00', 'acme');
INSERT INTO ops.error_budget (service, month, slo_target, availability, budget_remaining,
                              tenant_id) VALUES
    ('acme-payments', '2026-10-01', 0.999, 0.9970, -2.00, 'acme');
"""


def upgrade() -> None:
    """Upgrade schema."""
    for table in TABLES:
        op.execute(f"ALTER TABLE ops.{table} ADD COLUMN tenant_id text NOT NULL DEFAULT 'default'")
    for statement in ACME.split(";"):  # asyncpg runs one statement per call
        if statement.strip():
            op.execute(statement)
    op.execute(
        "CREATE TABLE ops.tenant_readers (rolname text PRIMARY KEY, tenant_id text NOT NULL)"
    )
    op.execute(
        "CREATE FUNCTION ops.reader_tenant() RETURNS text LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path = ops, pg_temp AS "
        "$$ SELECT tenant_id FROM ops.tenant_readers WHERE rolname = session_user $$"
    )
    op.execute("REVOKE ALL ON FUNCTION ops.reader_tenant() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION ops.reader_tenant() TO {READER}")
    for table in TABLES:
        op.execute(f"ALTER TABLE ops.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON ops.{table} FOR SELECT TO {READER} "
            "USING (tenant_id = ops.reader_tenant())"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON ops.{table}")
        op.execute(f"ALTER TABLE ops.{table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION ops.reader_tenant()")
    op.execute("DROP TABLE ops.tenant_readers")
    op.execute("DELETE FROM ops.error_budget WHERE tenant_id = 'acme'")
    op.execute("DELETE FROM ops.oncall_shifts WHERE tenant_id = 'acme'")
    op.execute("DELETE FROM ops.alerts WHERE tenant_id = 'acme'")
    op.execute("DELETE FROM ops.deployments WHERE tenant_id = 'acme'")
    op.execute("DELETE FROM ops.services WHERE tenant_id = 'acme'")
    for table in TABLES:
        op.execute(f"ALTER TABLE ops.{table} DROP COLUMN tenant_id")
