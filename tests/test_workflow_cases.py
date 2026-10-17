"""AIENG-44: the workflow evaluation set, committed before any workflow code."""

import json
from collections import Counter
from pathlib import Path

from incidentops_ai.incidents.models import IncidentCreate
from incidentops_ai.retrieval.documents import load_corpus

ROOT = Path(__file__).resolve().parent.parent
CASES = [json.loads(line) for line in (ROOT / "evals/workflow/cases.jsonl").read_text().splitlines()]
DOC_IDS = {d.id for d in load_corpus(ROOT / "data" / "corpus")}


def test_every_case_is_a_valid_incident_with_known_documents_and_files():
    for case in CASES:
        IncidentCreate(
            title=case["title"],
            description=case["description"],
            service=case["service"],
            severity=case["severity"],
            environment=case["environment"],
        )
        assert set(case["expected_docs"]) <= DOC_IDS, case["id"]
        for name in case["attachments"]:
            assert (ROOT / "evals/multimodal/assets" / name).is_file(), case["id"]


def test_the_set_matches_the_design():
    assert len(CASES) == 12
    assert Counter(c["expected_route"] for c in CASES) == {"recommend": 7, "review": 5}
    assert sum(1 for c in CASES if c["severity"] == "SEV1") == 2
    assert all(c["expected_docs"] for c in CASES if c["expected_route"] == "recommend")
