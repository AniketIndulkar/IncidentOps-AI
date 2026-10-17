"""Login users for the ops reader role, one per tenant, as a deployment would create them."""

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

READER_USER, READER_PASSWORD = "ops_reader_test", "ops-reader-test-password"
LOGINS = {"default": READER_USER, "acme": "ops_reader_acme"}


async def reader_url(admin_url: str, tenant: str = "default") -> str:
    user = LOGINS[tenant]
    engine = create_async_engine(admin_url)
    async with engine.begin() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": user}
        )
        if not exists:
            await conn.execute(text(f"CREATE ROLE {user} LOGIN PASSWORD '{READER_PASSWORD}'"))
        await conn.execute(text(f"GRANT incidentops_ops_reader TO {user}"))
        await conn.execute(text(f"ALTER ROLE {user} SET default_transaction_read_only = on"))
        await conn.execute(text(f"ALTER ROLE {user} SET statement_timeout = '2s'"))
        await conn.execute(
            text(
                "INSERT INTO ops.tenant_readers VALUES (:user, :tenant) "
                "ON CONFLICT (rolname) DO UPDATE SET tenant_id = excluded.tenant_id"
            ),
            {"user": user, "tenant": tenant},
        )
    await engine.dispose()
    url = make_url(admin_url).set(username=user, password=READER_PASSWORD)
    return url.render_as_string(hide_password=False)
