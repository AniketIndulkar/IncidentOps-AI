"""Versioned prompts, shipped inside the package: prompts/<name>/<version>.md.

A change to a prompt is a new version file, never an edit, so every recorded run can be traced
to the exact text that produced it.
"""

from functools import cache
from importlib import resources

from incidentops_ai.llm.provider import Prompt


class PromptNotFoundError(LookupError):
    pass


@cache
def _instructions(name: str, version: str) -> str:
    """Read once per process: a version file never changes while the app runs."""
    path = resources.files("incidentops_ai") / "prompts" / name / f"{version}.md"
    if not path.is_file():
        raise PromptNotFoundError(f"No prompt '{name}' version '{version}'.")
    return path.read_text(encoding="utf-8").strip()


def load_prompt(name: str, version: str, *, input_text: str) -> Prompt:
    return Prompt(
        name=name,
        version=version,
        instructions=_instructions(name, version),
        input=input_text,
    )
