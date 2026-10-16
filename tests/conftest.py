import os
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import create_app
from incidentops_ai.config import Settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://localhost:5432/incidentops_test",
)

TEST_API_TOKEN = "test-token-0123456789"


@pytest.fixture(scope="session")
def settings() -> Settings:
    # _env_file=None: tests must never pick up the real API key or model from .env.
    return Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        llm_model="fake-model",
        api_token=TEST_API_TOKEN,
    )


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
        await conn.execute(text("TRUNCATE incidents, llm_runs CASCADE"))
    await engine.dispose()
    yield


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
async def session_factory(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def llm_headers(settings: Settings) -> dict[str, str]:
    """Login for the LLM endpoints."""
    return {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}
