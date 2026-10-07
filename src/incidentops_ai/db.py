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
    return create_async_engine(
        settings.database_url, echo=settings.database_echo, pool_pre_ping=True
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: ORM objects stay readable after commit without a second round trip.
    return async_sessionmaker(engine, expire_on_commit=False)
