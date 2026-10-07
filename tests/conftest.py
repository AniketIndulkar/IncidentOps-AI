import os
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.main import create_app
from incidentops_ai.config import Settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://localhost:5432/incidentops_test",
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL)


@pytest.fixture(scope="session", autouse=True)
def migrated_db(settings: Settings) -> None:
    """Apply real Alembic migrations once, so tests exercise the same schema as prod."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
async def clean_tables(settings: Settings) -> AsyncIterator[None]:
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE incidents CASCADE"))
    await engine.dispose()
    yield


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
