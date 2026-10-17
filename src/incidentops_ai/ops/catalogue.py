"""What the text-to-SQL path may see (AIENG-56): an allow-list, not an introspection.

Only these tables and columns are described to the model and accepted by the validator. A column
added to the database is invisible here until someone adds it on purpose.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Table:
    name: str  # schema-qualified
    description: str
    columns: dict[str, str]


TABLES = (
    Table(
        "ops.services",
        "One row per service.",
        {
            "name": "service name, e.g. orders-api",
            "tier": "1 (most critical) to 3",
            "team": "owning team, e.g. payments",
            "owner": "the engineering manager responsible",
        },
    ),
    Table(
        "ops.deployments",
        "Every deployment of a service.",
        {
            "id": "deployment id",
            "service": "service name (ops.services.name)",
            "version": "version deployed, e.g. v4.12.0",
            "deployed_at": "when it was deployed (timestamptz, UTC)",
            "deployed_by": "engineer handle, e.g. sam.lee",
            "status": "succeeded, failed or rolled_back",
        },
    ),
    Table(
        "ops.alerts",
        "Alerts fired by monitoring.",
        {
            "id": "alert id",
            "service": "service name (ops.services.name)",
            "severity": "critical, warning or info",
            "title": "what fired",
            "fired_at": "when it fired (timestamptz, UTC)",
            "resolved_at": "when it was resolved (timestamptz, UTC); NULL while open",
        },
    ),
    Table(
        "ops.oncall_shifts",
        "Weekly on-call shifts per team, Monday 09:00 UTC to the next Monday 09:00 UTC.",
        {
            "id": "shift id",
            "team": "team name (ops.services.team)",
            "engineer": "engineer handle",
            "starts_at": "shift start (timestamptz, UTC), inclusive",
            "ends_at": "shift end (timestamptz, UTC), exclusive",
        },
    ),
    Table(
        "ops.error_budget",
        "Monthly SLO results per service.",
        {
            "service": "service name (ops.services.name)",
            "month": "first day of the month (date)",
            "slo_target": "availability target, e.g. 0.999",
            "availability": "measured availability, e.g. 0.9985",
            "budget_remaining": "share of the error budget left; 0 or below means used up",
        },
    ),
)

ALLOWED: dict[str, frozenset[str]] = {t.name: frozenset(t.columns) for t in TABLES}

# Real columns the model never sees nor selects (W10): row-level security filters on the tenant,
# so a query has no reason to name it, and naming it is refused.
HIDDEN_COLUMNS = frozenset({"tenant_id"})


def schema_text() -> str:
    """The allow-listed schema as the model sees it."""
    blocks = []
    for table in TABLES:
        columns = "\n".join(f"  - {name}: {about}" for name, about in table.columns.items())
        blocks.append(f"{table.name}: {table.description}\n{columns}")
    return "\n\n".join(blocks)
