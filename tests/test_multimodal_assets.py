"""AIENG-39: the multimodal benchmark is committed before any extraction code and is reproducible."""

import json
from collections import Counter
from pathlib import Path

from incidentops_ai.multimodal.assets import ASSETS, write_benchmark

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "evals" / "multimodal"


def cases() -> list[dict]:
    return [json.loads(line) for line in (BENCH / "cases.jsonl").read_text().splitlines()]


def test_the_committed_files_are_exactly_what_the_generator_draws(tmp_path: Path):
    write_benchmark(tmp_path)

    for asset in ASSETS:
        committed = (BENCH / "assets" / asset.filename).read_bytes()
        assert (tmp_path / "assets" / asset.filename).read_bytes() == committed, asset.id
    assert (tmp_path / "cases.jsonl").read_text() == (BENCH / "cases.jsonl").read_text()


def test_the_benchmark_has_the_kinds_and_sizes_written_in_the_design():
    records = cases()

    assert Counter(r["kind"] for r in records) == {
        "screenshot": 4,
        "chart": 3,
        "diagram": 3,
        "pdf-text": 2,
        "pdf-scanned": 2,
    }
    assert sum(len(r["questions"]) for r in records) == 28


def test_every_question_has_an_answer_and_every_file_exists():
    for record in cases():
        assert (BENCH / record["file"]).is_file(), record["id"]
        for question in record["questions"]:
            assert question["question"].endswith("?")
            assert question["expected"] and all(term.strip() for term in question["expected"])


def test_files_are_what_their_kind_says():
    for record in cases():
        head = (BENCH / record["file"]).read_bytes()[:5]
        expected = b"%PDF-" if record["kind"].startswith("pdf") else b"\x89PNG\r"
        assert head == expected, record["id"]
