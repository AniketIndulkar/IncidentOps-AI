"""AIENG-69: the simulated provider gives valid answers with recorded shapes; the app can run on it."""

from httpx import ASGITransport, AsyncClient
from test_analysis_api import INCIDENT

from app.main import create_app
from incidentops_ai.diagnosis.models import ModelDiagnosis
from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.llm.provider import LLMStatus, ModelConfig, Prompt
from incidentops_ai.llm.simulated import TOKENS, SimulatedProvider


async def test_answers_are_valid_and_cite_the_first_excerpt():
    provider = SimulatedProvider(latency_scale=0)
    evidence = "[E1] t (page 1, runbook)\n-----BEGIN EXCERPT ab12-----\nThe pool is exhausted by idle sessions.\n-----END EXCERPT ab12-----"
    config = ModelConfig(model="simulated")

    extraction = await provider.generate_structured(
        prompt=Prompt(name="extract_incident", version="v2", instructions="i", input="x"),
        schema=IncidentExtraction,
        config=config,
    )
    diagnosis = await provider.generate_structured(
        prompt=Prompt(name="diagnose_incident", version="v3", instructions="i", input=evidence),
        schema=ModelDiagnosis,
        config=config,
    )

    assert extraction.status is LLMStatus.SUCCEEDED
    assert diagnosis.value.causes[0].citations[0].quote == "The pool is exhausted by idle sessions."
    assert (diagnosis.metadata.input_tokens, diagnosis.metadata.output_tokens) == TOKENS


async def test_the_app_runs_a_triage_on_the_simulated_model_with_bm25(settings):
    simulated = settings.model_copy(
        update={
            "llm_provider": "simulated",
            "simulated_latency_scale": 0.0,
            "retrieval_stack": "bm25",
        }
    )
    app = create_app(simulated)
    headers = {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers=headers) as c,
    ):
        incident = (await c.post("/incidents", json=INCIDENT)).json()
        triage = await c.post(f"/incidents/{incident['id']}/triage")

    assert triage.status_code == 200 and triage.json()["run"]["status"] == "awaiting_approval"
