from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from neo4j import AsyncGraphDatabase

from app.errors import register_error_handlers
from app.rate_limit import SlidingWindowLimiter
from app.routes import analysis, incidents
from incidentops_ai.config import Settings
from incidentops_ai.db import create_engine, create_session_factory
from incidentops_ai.graph.neo4j_store import Neo4jGraphStore
from incidentops_ai.graph.retriever import AnchoredRetriever, GraphNeighbourhood, OwnDocuments
from incidentops_ai.graph.store import GraphStore
from incidentops_ai.llm.factory import build_model_config, build_provider
from incidentops_ai.llm.provider import LLMProvider
from incidentops_ai.retrieval.factory import build_embedder
from incidentops_ai.retrieval.rerank import CrossEncoderReranker
from incidentops_ai.retrieval.retriever import Retriever
from incidentops_ai.retrieval.stack import build_retriever


def _with_graph_scope(
    retriever: Retriever | None, settings: Settings, graph_store: GraphStore | None
) -> Retriever | None:
    """Wrap the retrieval stack in the scope INCIDENTOPS_GRAPH_RETRIEVAL asks for."""
    if retriever is None or settings.graph_retrieval == "off":
        return retriever
    if settings.graph_retrieval == "anchor":
        return AnchoredRetriever(retriever, OwnDocuments())
    if graph_store is None:
        raise ValueError(
            "INCIDENTOPS_GRAPH_RETRIEVAL=graph needs INCIDENTOPS_NEO4J_URI and "
            "INCIDENTOPS_NEO4J_PASSWORD"
        )
    return AnchoredRetriever(retriever, GraphNeighbourhood(graph_store, hops=1))


def create_app(
    settings: Settings | None = None,
    *,
    llm_provider: LLMProvider | None = None,
    retriever: Retriever | None = None,
    graph_store: GraphStore | None = None,
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
        owned_embedder = None
        owned_graph_driver = None
        try:
            if llm_provider is None and settings.openai_api_key:
                owned_provider = build_provider(settings)
            app.state.llm_provider = llm_provider or owned_provider
            # The retrieval stack embeds questions with OpenAI, so it needs the key too.
            if retriever is None and settings.openai_api_key:
                owned_embedder = build_embedder(settings)
                reranker = None
                if settings.retrieval_stack == "hybrid-rerank":
                    reranker = CrossEncoderReranker()
                    reranker.check_available()  # a mis-set stack stops the server at boot
                app.state.retriever = build_retriever(
                    settings.retrieval_stack, app.state.session_factory, owned_embedder, reranker
                )
            else:
                app.state.retriever = retriever
            # The knowledge graph (W05) is optional context; with Neo4j settings it is built here.
            store = graph_store
            if store is None and settings.neo4j_uri and settings.neo4j_password:
                owned_graph_driver = AsyncGraphDatabase.driver(
                    settings.neo4j_uri,
                    auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
                )
                store = Neo4jGraphStore(owned_graph_driver)
            app.state.graph_store = store
            app.state.retriever = _with_graph_scope(app.state.retriever, settings, store)
            app.state.llm_config = build_model_config(settings) if settings.llm_model else None
            app.state.api_token = settings.api_token
            app.state.llm_limiter = SlidingWindowLimiter(settings.llm_rate_limit_per_minute)
            yield
        finally:
            try:
                if owned_provider is not None:
                    await owned_provider.aclose()
                if owned_embedder is not None:
                    await owned_embedder.aclose()
                if owned_graph_driver is not None:
                    await owned_graph_driver.close()
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
