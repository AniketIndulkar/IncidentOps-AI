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
from incidentops_ai.retrieval.factory import build_embedder
from incidentops_ai.retrieval.rerank import CrossEncoderReranker
from incidentops_ai.retrieval.retriever import Retriever
from incidentops_ai.retrieval.stack import build_retriever


def create_app(
    settings: Settings | None = None,
    *,
    llm_provider: LLMProvider | None = None,
    retriever: Retriever | None = None,
) -> FastAPI:
    """`llm_provider` and `retriever` let tests inject fakes; otherwise the real ones are built
    from settings, or left off (the endpoints that need them answer 503) when no key is set."""
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
        # The retrieval stack embeds questions with OpenAI, so it needs the key too.
        owned_embedder = None
        if retriever is None and settings.openai_api_key:
            owned_embedder = build_embedder(settings)
            reranker = (
                CrossEncoderReranker() if settings.retrieval_stack == "hybrid-rerank" else None
            )
            app.state.retriever = build_retriever(
                settings.retrieval_stack, app.state.session_factory, owned_embedder, reranker
            )
        else:
            app.state.retriever = retriever
        app.state.llm_config = build_model_config(settings) if settings.llm_model else None
        app.state.api_token = settings.api_token
        app.state.llm_limiter = SlidingWindowLimiter(settings.llm_rate_limit_per_minute)
        try:
            yield
        finally:
            try:
                if owned_provider is not None:
                    await owned_provider.aclose()
                if owned_embedder is not None:
                    await owned_embedder.aclose()
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
