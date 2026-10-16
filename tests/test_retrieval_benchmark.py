import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.retrieval.benchmark import (
    render_benchmark_report,
    run_benchmark,
    write_benchmark,
)
from incidentops_ai.retrieval.chunking import SectionChunker
from incidentops_ai.retrieval.documents import load_corpus
from incidentops_ai.retrieval.embeddings import FakeEmbedder
from incidentops_ai.retrieval.evalset import load_questions
from incidentops_ai.retrieval.ingest import ingest_documents
from incidentops_ai.retrieval.memory import InMemoryDenseRetriever
from incidentops_ai.retrieval.store import PgVectorRetriever, index_chunks

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = load_corpus(ROOT / "data" / "corpus")
QUESTIONS = load_questions(ROOT / "evals" / "retrieval" / "questions.jsonl")
CHUNKER = SectionChunker(max_size=1500)


async def memory_retriever() -> InMemoryDenseRetriever:
    chunks = [c for d in DOCUMENTS for c in CHUNKER.chunk(d)]
    return await InMemoryDenseRetriever.build(chunks, FakeEmbedder(), with_context=False)


async def test_a_benchmark_scores_every_question_and_times_each_query():
    result = await run_benchmark(await memory_retriever(), DOCUMENTS, QUESTIONS)

    assert len(result.scores) == len(result.latencies_ms) == 30
    assert all(ms >= 0 for ms in result.latencies_ms)
    assert result.summary.n == 30
    assert result.retriever == "dense-memory"
    assert all(len(ranked) == 10 for ranked in result.rankings.values())


async def test_misses_are_the_questions_whose_evidence_is_not_all_in_the_top_five():
    result = await run_benchmark(await memory_retriever(), DOCUMENTS, QUESTIONS)

    expected = {s.question_id for s in result.scores if s.recall[5] < 1.0}
    assert {m.question.id for m in result.misses} == expected
    assert expected, "the fake embedder should miss something, or this test proves nothing"
    for miss in result.misses:
        assert 1 <= len(miss.missing) <= len(miss.question.evidence)
        assert set(miss.missing) <= set(miss.question.evidence)
        assert len(miss.top) == 3


async def test_the_report_has_the_metrics_latency_styles_and_misses():
    result = await run_benchmark(await memory_retriever(), DOCUMENTS, QUESTIONS)

    report = render_benchmark_report(result, embedding_model="text-embedding-3-small", run_date="d")

    for expected in (
        "Recall@5",
        "Precision@5",
        "MRR",
        "p50",
        "p95",
        "## By question style",
        "## Misses",
    ):
        assert expected in report
    assert "text-embedding-3-small" in report
    assert "OFFLINE" not in report
    assert all(m.question.id in report for m in result.misses)


async def test_a_run_with_fake_embeddings_says_it_proves_nothing_about_meaning():
    result = await run_benchmark(await memory_retriever(), DOCUMENTS, QUESTIONS)

    report = render_benchmark_report(result, embedding_model="fake-hash-1536", run_date="d")

    assert "OFFLINE WIRING CHECK" in report


async def test_the_data_file_lists_each_questions_top_ten_and_is_repeatable(tmp_path: Path):
    retriever = await memory_retriever()
    first = await run_benchmark(retriever, DOCUMENTS, QUESTIONS)
    second = await run_benchmark(retriever, DOCUMENTS, QUESTIONS)

    a = write_benchmark(first, tmp_path / "a", embedding_model="m", run_date="d")
    b = write_benchmark(second, tmp_path / "b", embedding_model="m", run_date="d")

    data = json.loads(a.data_path.read_text())
    q01 = data["questions"]["q01"]
    assert len(q01["top"]) == 10
    assert set(q01["top"][0]) == {"rank", "chunk_id", "doc_id", "section", "score"}
    assert "latency_ms" in data
    # Everything except wall-clock timings is identical between runs.
    a_data = {k: v for k, v in data.items() if k != "latency_ms"}
    b_data = {k: v for k, v in json.loads(b.data_path.read_text()).items() if k != "latency_ms"}
    assert a_data == b_data


async def test_the_pgvector_retriever_can_be_benchmarked(
    session_factory: async_sessionmaker[AsyncSession],
):
    async with session_factory() as session, session.begin():
        await ingest_documents(session, DOCUMENTS, CHUNKER)
        await index_chunks(session, FakeEmbedder())
    retriever = PgVectorRetriever(session_factory, FakeEmbedder())

    result = await run_benchmark(retriever, DOCUMENTS, QUESTIONS)
    reference = await run_benchmark(await memory_retriever(), DOCUMENTS, QUESTIONS)

    assert result.retriever == "dense-pgvector"
    assert result.summary.mrr == pytest.approx(reference.summary.mrr, abs=1e-9)


async def test_benchmarking_with_no_questions_is_an_error():
    with pytest.raises(ValueError):
        await run_benchmark(await memory_retriever(), DOCUMENTS, [])


async def test_the_report_compares_with_and_without_the_service_filter():
    retriever = await memory_retriever()
    plain = await run_benchmark(retriever, DOCUMENTS, QUESTIONS)
    filtered = await run_benchmark(retriever, DOCUMENTS, QUESTIONS, use_service_filter=True)

    report = render_benchmark_report(plain, embedding_model="m", run_date="d", filtered=filtered)

    assert "## Service filter" in report
    assert "| service filter |" in report
    assert "## Service filter" not in render_benchmark_report(
        plain, embedding_model="m", run_date="d"
    )
