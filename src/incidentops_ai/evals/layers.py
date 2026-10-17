"""The layers the CI gate can measure without an API key (AIENG-64, AIENG-68).

Retrieval (BM25 over the corpus), workflow routing (behaving scripted models), security (the SQL
validator, the injection detector and the PII detectors on labelled sets), and generation as
recorded by the last real-model run. Each returns plain numbers the gate compares to thresholds.
"""

import hashlib
import json
import uuid
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from incidentops_ai.diagnosis.models import ModelCause, ModelCitation, ModelDiagnosis
from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.graph.catalogue import load_catalogue
from incidentops_ai.graph.memory import InMemoryGraphStore
from incidentops_ai.incidents.models import IncidentCreate
from incidentops_ai.incidents.repository import IncidentRepository
from incidentops_ai.llm.fake_provider import FakeProvider
from incidentops_ai.llm.provider import ModelConfig
from incidentops_ai.ops.sql_guard import SqlRefused, validate
from incidentops_ai.retrieval.documents import load_corpus
from incidentops_ai.retrieval.evalset import load_questions
from incidentops_ai.retrieval.evaluate import evaluate_retriever
from incidentops_ai.retrieval.metrics import summarize
from incidentops_ai.security.injection import scan
from incidentops_ai.security.pii import redact
from incidentops_ai.workflow.benchmark import (
    CATALOGUE,
    CORPUS,
    Scripted,
    corpus_bm25,
    load_cases,
    run_graph,
    task_success,
)
from incidentops_ai.workflow.graph import WorkflowDeps

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
QUESTIONS = Path("evals/retrieval/questions.jsonl")


def _lines(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


async def retrieval_layer(retriever=None) -> dict:
    documents = load_corpus(CORPUS)
    evaluation = await evaluate_retriever(
        retriever or corpus_bm25(), documents, load_questions(QUESTIONS), k=10
    )
    summary = summarize(evaluation.scores)
    return {"recall_at_5": round(summary.recall[5], 3), "mrr_at_10": round(summary.mrr, 3)}


def security_layer() -> dict:
    def refused(sql: str) -> bool:
        try:
            validate(sql)
        except SqlRefused:
            return True
        return False

    attacks = _lines("evals/sql/attacks.jsonl")
    references = _lines("evals/sql/questions.jsonl")
    injections = _lines("evals/security/injection_set.jsonl")
    pii = _lines("evals/security/pii_set.jsonl")
    positives = [r for r in injections if r["injection"]]
    pii_positive = [r for r in pii if r["kind"]]
    return {
        "sql_attacks_refused": sum(refused(a["sql"]) for a in attacks),
        "sql_references_accepted": sum(not refused(q["reference_sql"]) for q in references),
        "injection_recall": round(
            sum(bool(scan(r["text"])) for r in positives) / len(positives), 3
        ),
        "injection_false_positives": sum(
            bool(scan(r["text"])) for r in injections if not r["injection"]
        ),
        "pii_recall": round(
            sum(f"[{r['kind']}_1]" in redact(r["text"]).text for r in pii_positive)
            / len(pii_positive),
            3,
        ),
        "pii_false_positives": sum(bool(redact(r["text"]).findings) for r in pii if not r["kind"]),
    }


class Behaving:
    """A scripted model that does what a correct one would for a W07 case (as in H24)."""

    def __init__(self, case, chunks_by_title: dict[str, str]) -> None:
        self._case = case
        self._titles = chunks_by_title

    async def generate_structured(self, *, prompt, schema, config):
        if schema is IncidentExtraction:
            vague = self._case.id == "w12"
            service = {
                "name": None if vague else self._case.service,
                "stated_explicitly": not vague,
            }
            reply = Scripted.EXTRACTION.model_copy(
                update={"service": Scripted.EXTRACTION.service.model_copy(update=service)}
            )
        else:
            reply = ModelDiagnosis(answer_status="insufficient_evidence", causes=[])
            for block in prompt.input.split("\n\n["):
                header, _, rest = block.partition("\n")
                evidence_id = header.lstrip("[").split("]")[0]
                title = header.split("] ", 1)[-1].split(" (page")[0].split(" > ")[0]
                if self._titles.get(title) in self._case.expected_docs:
                    words = rest.split("-----\n", 1)[-1].split()
                    quote = " ".join(words[:10])
                    reply = ModelDiagnosis(
                        answer_status="answered",
                        causes=[
                            ModelCause(
                                claim="Per the documented cause.",
                                citations=[ModelCitation(evidence_id=evidence_id, quote=quote)],
                            )
                        ],
                    )
                    break
        return await FakeProvider([reply]).generate_structured(
            prompt=prompt, schema=schema, config=config
        )

    async def aclose(self) -> None:
        return None


async def workflow_layer(sessions) -> dict:
    titles = {d.title: d.id for d in load_corpus(CORPUS)}
    graph = InMemoryGraphStore([load_catalogue(CATALOGUE)])
    correct = 0
    for case in load_cases():
        async with sessions() as session:
            created = await IncidentRepository(session).create(
                IncidentCreate(
                    title=case.title,
                    description=case.description,
                    service=case.service,
                    severity=case.severity,
                    environment=case.environment,
                ),
                tenant_id=f"gate-{uuid.uuid4().hex[:8]}",
            )
        tenant = created.incident.tenant_id
        deps = WorkflowDeps(
            sessions,
            Behaving(case, titles),
            ModelConfig(model="scripted"),
            _Corpus(),
            graph,
            tenant_id=tenant,
        )
        state = await run_graph(deps, created.incident.id, checkpointer=InMemorySaver())
        correct += task_success(case, state)
    return {"routes_correct": correct}


class _Corpus:
    """BM25 over the default-tenant corpus, for runs in the gate's own tenants."""

    name = "bm25"

    def __init__(self) -> None:
        self._inner = corpus_bm25()

    async def retrieve(self, query, *, k, filters, hints=None):
        from incidentops_ai.retrieval.retriever import RetrievalFilter

        return await self._inner.retrieve(
            query, k=k, filters=RetrievalFilter(tenant_id="default"), hints=hints
        )


def generation_layer(run_path: Path) -> dict:
    run = json.loads(Path(run_path).read_text())
    metrics = {k: v for k, v in run["metrics"].items() if k != "per_case"}
    verdicts = run.get("verdicts_v2") or {}
    if verdicts:
        metrics["judge_faithfulness"] = round(
            sum(v["supported"] for v in verdicts.values()) / len(verdicts), 3
        )
        calibration = run.get("calibration_v2", {}).get("supported", {})
        metrics["judge_agreement"] = calibration.get("agreement")
    metrics["model"] = run.get("model")
    return metrics


def fingerprint(prompts: Path = PROMPTS) -> str:
    """Every prompt file's name and content: a changed prompt changes the fingerprint."""
    digest = hashlib.sha256()
    for path in sorted(prompts.rglob("*.md")):
        digest.update(str(path.relative_to(prompts)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
