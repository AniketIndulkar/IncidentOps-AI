from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.errors import register_error_handlers
from app.rate_limit import SlidingWindowLimiter
from app.routes import analysis, incidents
from incidentops_ai.config import Settings
from incidentops_ai.db import create_engine, create_session_factory
from incidentops_ai.llm.factory import build_model_config, build_provider
from incidentops_ai.llm.provider import LLMProvider


def create_app(
    settings: Settings | None = None, *, llm_provider: LLMProvider | None = None
) -> FastAPI:
    """`llm_provider` lets tests inject a FakeProvider; otherwise the real one is built from
    settings, or left off (LLM endpoints answer 503) when no key or model is configured."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # One engine (connection pool) per process, created on startup and closed on shutdown.
        engine = create_engine(settings)
        app.state.session_factory = create_session_factory(engine)
        # A provider handed in by the caller is theirs to close; one built here is ours.
        owned_provider = None
        if llm_provider is None and settings.openai_api_key:
            owned_provider = build_provider(settings)
        app.state.llm_provider = llm_provider or owned_provider
        app.state.llm_config = build_model_config(settings) if settings.llm_model else None
        app.state.api_token = settings.api_token
        app.state.llm_limiter = SlidingWindowLimiter(settings.llm_rate_limit_per_minute)
        try:
            yield
        finally:
            try:
                if owned_provider is not None:
                    await owned_provider.aclose()
            finally:
                await engine.dispose()

    app = FastAPI(title="IncidentOps AI", version="0.0.1", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    register_error_handlers(app)
    app.include_router(incidents.router)
    app.include_router(analysis.router)
    return app


app = create_app()
