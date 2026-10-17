"""A model stand-in for load tests: real latencies and token counts, valid answers (AIENG-69).

`INCIDENTOPS_LLM_PROVIDER=simulated`. Each call waits a latency drawn from the step timings of the
real W07 run (gpt-6-luna, 17 Oct 2026) for that prompt, reports the real average token counts of
those calls (2,010 in, 449 out), and returns a valid structured reply: the extraction of the
incident as written, and a diagnosis citing the first ten words of evidence E1 (or
`insufficient_evidence` when there is none). It is for measuring the system around the model,
never for judging answers.
"""

import asyncio
import random
import re
import time

from incidentops_ai.diagnosis.models import ModelCause, ModelCitation, ModelDiagnosis
from incidentops_ai.extraction.models import IncidentExtraction
from incidentops_ai.llm.provider import (
    LLMStatus,
    ModelConfig,
    Prompt,
    RunMetadata,
    StructuredResult,
    ToolTurn,
)

# Milliseconds, from evals/workflow/runs/2026-10-17-workflow-benchmark.json (24 runs each).
RECORDED_MS = {
    "extract_incident": [
        4611,
        4860,
        4869,
        5065,
        5111,
        5264,
        5712,
        5877,
        5988,
        6052,
        6168,
        6226,
        6366,
        6876,
        7159,
        7484,
        7591,
        7671,
        7692,
        7820,
        7998,
        8446,
        8673,
        9248,
    ],
    "diagnose_incident": [
        1149,
        1269,
        1270,
        1721,
        1745,
        1839,
        2063,
        2210,
        2240,
        2475,
        2510,
        2591,
        2653,
        2846,
        2894,
        3016,
        3176,
        3267,
        3831,
        4197,
        4300,
        4544,
        5259,
        8183,
    ],
}
DEFAULT_MS = RECORDED_MS["diagnose_incident"]
TOKENS = (2010, 449)  # mean input and output tokens per call in that run

_FIRST_EVIDENCE = re.compile(
    r"\[(E1)\][^\n]*\n-----BEGIN EXCERPT \w+-----\n(?P<text>.*?)\n-----END", re.DOTALL
)


def _extraction() -> IncidentExtraction:
    return IncidentExtraction.model_validate(
        {
            "title": "Simulated extraction",
            "service": {"name": None, "stated_explicitly": False},
            "symptoms": [{"description": "as reported", "started_at": None}],
            "error_codes": [],
            "environment": "production",
            "severity_candidate": {"level": "SEV3", "rationale": "Simulated."},
            "entities": [],
            "missing_information": [],
        }
    )


def _diagnosis(prompt_input: str) -> ModelDiagnosis:
    found = _FIRST_EVIDENCE.search(prompt_input)
    if not found:
        return ModelDiagnosis(answer_status="insufficient_evidence", causes=[])
    quote = " ".join(found["text"].split()[:10])
    return ModelDiagnosis(
        answer_status="answered",
        causes=[
            ModelCause(
                claim="Simulated cause from the first evidence excerpt.",
                citations=[ModelCitation(evidence_id="E1", quote=quote)],
            )
        ],
    )


class SimulatedProvider:
    def __init__(self, *, latency_scale: float = 1.0, seed: int | None = None) -> None:
        self._scale = latency_scale
        self._random = random.Random(seed)

    def _metadata(self, prompt: Prompt, config: ModelConfig, started: float) -> RunMetadata:
        return RunMetadata(
            provider="simulated",
            model=config.model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            attempts=1,
            latency_ms=round((time.perf_counter() - started) * 1000),
            input_tokens=TOKENS[0],
            output_tokens=TOKENS[1],
            attempt_statuses=[LLMStatus.SUCCEEDED],
        )

    async def _wait(self, prompt: Prompt) -> None:
        ms = self._random.choice(RECORDED_MS.get(prompt.name, DEFAULT_MS))
        await asyncio.sleep(ms / 1000 * self._scale)

    async def generate_structured(self, *, prompt: Prompt, schema, config: ModelConfig):
        started = time.perf_counter()
        await self._wait(prompt)
        if schema is IncidentExtraction:
            value = _extraction()
        elif schema is ModelDiagnosis:
            value = _diagnosis(prompt.input)
        else:
            return StructuredResult[schema](
                status=LLMStatus.REJECTED,
                error_detail="the simulated provider has no answer for this schema",
                metadata=self._metadata(prompt, config, started),
            )
        return StructuredResult[schema](
            status=LLMStatus.SUCCEEDED,
            value=value,
            metadata=self._metadata(prompt, config, started),
        )

    async def tool_call(
        self, *, prompt: Prompt, tools, config: ModelConfig, transcript=None, outputs=()
    ):
        started = time.perf_counter()
        await self._wait(prompt)
        return ToolTurn(
            status=LLMStatus.SUCCEEDED,
            final_text="No related incidents (simulated).",
            metadata=self._metadata(prompt, config, started),
        )

    async def aclose(self) -> None:
        return None
