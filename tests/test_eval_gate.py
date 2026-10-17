"""AIENG-68 / H49: the gate passes today's tree and fails each deliberate regression."""

import shutil

import pytest

from incidentops_ai.evals import gate, layers
from incidentops_ai.retrieval.retriever import RetrievedChunk


@pytest.fixture
def dashboard(tmp_path):
    return tmp_path / "dashboard.md"


async def test_todays_tree_passes(session_factory, dashboard):
    measured, failures = await gate.run_gate(session_factory, dashboard=dashboard)

    assert failures == [], failures
    assert measured["workflow"]["routes_correct"] == 12
    assert "Gate: pass" in dashboard.read_text()


class Reversed:
    """A retrieval regression: the right chunks, in the wrong order."""

    name = "reversed"

    def __init__(self) -> None:
        self._inner = layers.corpus_bm25()

    async def retrieve(self, query, *, k, filters, hints=None):
        ranked = await self._inner.retrieve(query, k=40, filters=filters, hints=hints)
        worst = list(reversed(ranked))[:k]
        return [
            RetrievedChunk(chunk=r.chunk, score=r.score, rank=i) for i, r in enumerate(worst, 1)
        ]


async def test_a_retrieval_regression_fails_the_gate(session_factory, dashboard):
    _, failures = await gate.run_gate(session_factory, retriever=Reversed(), dashboard=dashboard)

    assert any(f.startswith("retrieval.mrr_at_10") for f in failures)
    assert "Gate: FAIL" in dashboard.read_text()


async def test_a_routing_regression_fails_the_gate(session_factory, dashboard, monkeypatch):
    monkeypatch.setattr("incidentops_ai.workflow.graph.review_reasons", lambda state: [])

    _, failures = await gate.run_gate(session_factory, dashboard=dashboard)

    assert any(f.startswith("workflow.routes_correct") for f in failures)


async def test_a_security_regression_fails_the_gate(session_factory, dashboard, monkeypatch):
    monkeypatch.setattr(layers, "validate", lambda sql: sql)  # a validator that accepts anything

    _, failures = await gate.run_gate(session_factory, dashboard=dashboard)

    assert any(f.startswith("security.sql_attacks_refused") for f in failures)


async def test_a_prompt_change_without_a_new_run_fails_the_gate(
    session_factory, dashboard, tmp_path
):
    prompts = tmp_path / "prompts"
    shutil.copytree(layers.PROMPTS, prompts)
    target = prompts / "diagnose_incident" / "v3.md"
    target.write_text(target.read_text() + "\nAlways propose three causes.\n")

    _, failures = await gate.run_gate(session_factory, prompts=prompts, dashboard=dashboard)

    assert any("prompts changed" in f for f in failures)


def test_the_check_reads_lower_is_better_metrics_the_right_way():
    thresholds = {"security": {"pii_false_positives": 0, "pii_recall": 1.0}}

    assert gate.check({"security": {"pii_false_positives": 0, "pii_recall": 1.0}}, thresholds) == []
    assert (
        len(gate.check({"security": {"pii_false_positives": 2, "pii_recall": 0.9}}, thresholds))
        == 2
    )
