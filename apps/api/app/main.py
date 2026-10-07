from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.errors import register_error_handlers
from app.routes import incidents
from incidentops_ai.config import Settings
from incidentops_ai.db import create_engine, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # One engine (connection pool) per process, created on startup and closed on shutdown.
        engine = create_engine(settings)
        app.state.session_factory = create_session_factory(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="IncidentOps AI", version="0.0.1", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    register_error_handlers(app)
    app.include_router(incidents.router)
    return app


app = create_app()
