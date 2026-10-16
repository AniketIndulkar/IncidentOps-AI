"""Which retrieval stack the API uses (W04 findings: hybrid by default)."""

import pytest
from pydantic import ValidationError

from incidentops_ai.config import Settings


def test_the_retrieval_stack_defaults_to_hybrid():
    assert Settings(_env_file=None).retrieval_stack == "hybrid"


@pytest.mark.parametrize("stack", ["dense", "hybrid", "hybrid-rerank"])
def test_each_known_stack_can_be_chosen(stack: str):
    assert Settings(_env_file=None, retrieval_stack=stack).retrieval_stack == stack


def test_an_unknown_stack_is_refused_at_startup():
    with pytest.raises(ValidationError, match="retrieval_stack"):
        Settings(_env_file=None, retrieval_stack="magic")


def test_the_stack_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INCIDENTOPS_RETRIEVAL_STACK", "dense")

    assert Settings(_env_file=None).retrieval_stack == "dense"
