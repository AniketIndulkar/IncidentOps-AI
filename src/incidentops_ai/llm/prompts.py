"""Versioned prompts, shipped inside the package: prompts/<name>/<version>.md.

A change to a prompt is a new version file, never an edit, so every recorded run can be traced
to the exact text that produced it.
"""

from importlib import resources

from incidentops_ai.llm.provider import Prompt


class PromptNotFoundError(LookupError):
    pass


def load_prompt(name: str, version: str, *, input_text: str) -> Prompt:
    path = resources.files("incidentops_ai") / "prompts" / name / f"{version}.md"
    if not path.is_file():
        raise PromptNotFoundError(f"No prompt '{name}' version '{version}'.")
    return Prompt(
        name=name,
        version=version,
        instructions=path.read_text(encoding="utf-8").strip(),
        input=input_text,
    )
