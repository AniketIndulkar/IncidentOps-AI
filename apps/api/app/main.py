from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from neo4j import AsyncGraphDatabase
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import create_async_engine

from app.errors import register_error_handlers
from app.rate_limit import SlidingWindowLimiter
from app.routes import analysis, attachments, incidents, memory, ops, workflow
from incidentops_ai.auth.tokens import TokenVerifier
from incidentops_ai.config import Settings
from incidentops_ai.db import create_engine, create_session_factory
from incidentops_ai.graph.neo4j_store import Neo4jGraphStore
from incidentops_ai.graph.retriever import AnchoredRetriever, GraphNeighbourhood, OwnDocuments
from incidentops_ai.graph.store import GraphStore
from incidentops_ai.integrations.cmdb import CmdbClient
from incidentops_ai.integrations.jira import JiraClient, JiraTicketSink
from incidentops_ai.llm.factory import build_model_config, build_provider
from incidentops_ai.llm.gateway import Gateway
from incidentops_ai.llm.provider import LLMProvider
from incidentops_ai.multimodal.attachments import AttachmentProcessor
from incidentops_ai.multimodal.extract import OcrExtractor, PdfTextExtractor, VlmTranscriber
from incidentops_ai.ops.database import OpsDatabase
from incidentops_ai.retrieval.factory import build_embedder
from incidentops_ai.retrieval.rerank import CrossEncoderReranker
from incidentops_ai.retrieval.retriever import Retriever
from incidentops_ai.retrieval.stack import build_retriever
from incidentops_ai.security.guard import GuardedProvider
from incidentops_ai.tickets.client import TicketActions, TicketClient
from incidentops_ai.workflow.graph import WorkflowDeps
from incidentops_ai.workflow.runner import WorkflowRunner, checkpoint_url


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
    attachment_processor: AttachmentProcessor | None = None,
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
        resources = AsyncExitStack()  # opened here, closed at shutdown (W07: the checkpointer)
        try:
            if llm_provider is None and settings.openai_api_key:
                owned_provider = build_provider(settings)
            # W10: every model call goes through the guard (canary, output PII), whoever made the
            # provider; closing it stays with whoever made it.
            # W11: through the gateway (routes, fallback, breaker), inside the guard, so the
            # canary is checked whichever model answered.
            inner = llm_provider or owned_provider
            app.state.llm_provider = (
                GuardedProvider(
                    Gateway(
                        inner,
                        settings.llm_routes,
                        per_model_per_minute=settings.llm_per_model_per_minute,
                    )
                )
                if inner is not None
                else None
            )
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
            # Attachments (W06): images get a vision transcript when a model is configured.
            vlm = (
                VlmTranscriber(app.state.llm_provider, app.state.llm_config)
                if app.state.llm_provider is not None and app.state.llm_config is not None
                else None
            )
            app.state.attachment_processor = attachment_processor or AttachmentProcessor(
                pdf=PdfTextExtractor(), ocr=OcrExtractor(), vlm=vlm
            )
            # The workflow (W07) needs a model and retrieval; its checkpoints live in PostgreSQL.
            app.state.workflow_runner = None
            if vlm is not None and app.state.retriever is not None:
                # The pool connects in the background (open without waiting), so like the
                # SQLAlchemy engine it does not stop the server when the database is down.
                pool = AsyncConnectionPool(
                    checkpoint_url(settings.database_url),
                    min_size=1,
                    max_size=5,
                    timeout=5,
                    open=False,
                    kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
                )
                await pool.open(wait=False)
                resources.push_async_callback(pool.close)
                saver = AsyncPostgresSaver(pool)
                deps = WorkflowDeps(
                    sessions=app.state.session_factory,
                    provider=app.state.llm_provider,
                    config=app.state.llm_config,
                    retriever=app.state.retriever,
                    graph=app.state.graph_store,
                )
                # setup() creates or migrates LangGraph's own checkpoint tables, on first use.
                if settings.cmdb_url and settings.cmdb_api_key:
                    cmdb = CmdbClient(settings.cmdb_url, settings.cmdb_api_key.get_secret_value())
                    resources.push_async_callback(cmdb.aclose)
                    deps.cmdb = cmdb
                if settings.jira_url and settings.jira_token:
                    jira = JiraClient(
                        settings.jira_url,
                        settings.jira_token.get_secret_value(),
                        project=settings.jira_project,
                    )
                    resources.push_async_callback(jira.aclose)
                    deps.tickets = JiraTicketSink(jira)
                elif settings.tickets_db and settings.approval_secret:
                    # The ticket MCP server runs as a child process for the app's lifetime.
                    secret = settings.approval_secret.get_secret_value()
                    tickets = await resources.enter_async_context(
                        TicketClient.connect(db_path=settings.tickets_db, secret=secret)
                    )
                    deps.tickets = TicketActions(tickets, secret)
                app.state.workflow_runner = WorkflowRunner(deps, saver, prepare=saver.setup)
            # The ops database (W09) is read through its own engines, one reader login per tenant
            # (W10): row-level security admits each login's tenant only.
            ops_urls = dict(settings.ops_database_urls)
            if settings.ops_database_url:
                ops_urls.setdefault("default", settings.ops_database_url)
            app.state.ops_databases = {}
            for tenant, url in ops_urls.items():
                ops_engine = create_async_engine(url, pool_pre_ping=True)
                resources.push_async_callback(ops_engine.dispose)
                app.state.ops_databases[tenant] = OpsDatabase(ops_engine)
            app.state.api_token = settings.api_token
            app.state.token_verifier = (
                TokenVerifier(
                    secret=settings.jwt_secret.get_secret_value() if settings.jwt_secret else None,
                    public_key=settings.jwt_public_key,
                    issuer=settings.jwt_issuer,
                )
                if settings.jwt_secret or settings.jwt_public_key
                else None
            )
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
                await resources.aclose()
            finally:
                await engine.dispose()

    app = FastAPI(title="IncidentOps AI", version="0.0.1", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    register_error_handlers(app)
    app.include_router(incidents.router)
    app.include_router(analysis.router)
    app.include_router(attachments.router)
    app.include_router(workflow.router)
    app.include_router(memory.router)
    app.include_router(ops.router)
    return app


app = create_app()
