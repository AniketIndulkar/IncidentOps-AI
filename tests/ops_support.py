"""A login user for the ops reader role, as a deployment would create one."""

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

READER_USER, READER_PASSWORD = "ops_reader_test", "ops-reader-test-password"


async def reader_url(admin_url: str) -> str:
    engine = create_async_engine(admin_url)
    async with engine.begin() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": READER_USER}
        )
        if not exists:
            await conn.execute(
                text(f"CREATE ROLE {READER_USER} LOGIN PASSWORD '{READER_PASSWORD}'")
            )
        await conn.execute(text(f"GRANT incidentops_ops_reader TO {READER_USER}"))
        await conn.execute(text(f"ALTER ROLE {READER_USER} SET default_transaction_read_only = on"))
        await conn.execute(text(f"ALTER ROLE {READER_USER} SET statement_timeout = '2s'"))
    await engine.dispose()
    url = make_url(admin_url).set(username=READER_USER, password=READER_PASSWORD)
    return url.render_as_string(hide_password=False)
