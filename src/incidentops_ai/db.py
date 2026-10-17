from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from incidentops_ai.config import Settings


class Base(DeclarativeBase):
    """Declarative base for all ORM tables. Alembic reads Base.metadata for autogenerate."""


def create_engine(settings: Settings) -> AsyncEngine:
    # pool_pre_ping drops dead connections (e.g. after a Postgres restart) instead of failing a request.
    # Pool sizes are settings since W12 (defaults: SQLAlchemy's 5 + 10 overflow). The load test
    # measured no gain from doubling them: requests queue on CPU before they wait for a connection.
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: ORM objects stay readable after commit without a second round trip.
    return async_sessionmaker(engine, expire_on_commit=False)
