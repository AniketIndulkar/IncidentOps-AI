"""Run the sample incidents through the real model and print a result table.

    uv run python -m incidentops_ai.extraction.run_samples [path/to/samples.jsonl]

Needs OPENAI_API_KEY and INCIDENTOPS_LLM_MODEL (in .env). Makes one paid API call per sample.
Exit code 0 only if every sample parsed into a valid typed result.
"""

import asyncio
import json
import sys
from pathlib import Path

from incidentops_ai.config import Settings
from incidentops_ai.errors import LLMNotConfiguredError
from incidentops_ai.extraction.service import extract_incident
from incidentops_ai.llm.factory import build_model_config, build_provider
from incidentops_ai.llm.provider import LLMStatus

DEFAULT_SAMPLES = Path("evals/extraction/samples.jsonl")
CONCURRENCY = 3


async def main(path: Path) -> int:
    settings = Settings()
    try:
        provider = build_provider(settings)
        config = build_model_config(settings)
    except LLMNotConfiguredError as exc:
        print(f"{exc.message} Add it to .env first.", file=sys.stderr)
        return 2

    samples = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    limit = asyncio.Semaphore(CONCURRENCY)

    async def run(sample: dict):
        async with limit:
            return sample, await extract_incident(provider, sample["text"], config)

    results = await asyncio.gather(*(run(s) for s in samples))

    print(f"model={config.model}  samples={len(samples)}\n")
    header = (
        f"{'id':<16}{'status':<16}{'service':<22}{'svc ok':<8}{'env':<13}{'sev':<6}"
        f"{'sym':>4}{'err':>4}{'miss':>5}{'in':>7}{'out':>6}{'ms':>7}{'tries':>6}"
    )
    print(header)
    print("-" * len(header))
    failed = 0
    tokens_in = tokens_out = 0
    for sample, result in results:
        meta = result.metadata
        tokens_in += meta.input_tokens or 0
        tokens_out += meta.output_tokens or 0
        if result.status is not LLMStatus.SUCCEEDED or result.value is None:
            failed += 1
            tried = " -> ".join(meta.attempt_statuses)
            print(f"{sample['id']:<16}{result.status:<16}{result.error_detail or ''}  [{tried}]")
            continue
        v = result.value
        service_ok = "yes" if v.service.name == sample.get("expected_service") else "NO"
        print(
            f"{sample['id']:<16}{result.status:<16}{v.service.name or '-':<22}{service_ok:<8}"
            f"{v.environment or '-':<13}{v.severity_candidate.level:<6}"
            f"{len(v.symptoms):>4}{len(v.error_codes):>4}{len(v.missing_information):>5}"
            f"{meta.input_tokens or 0:>7}{meta.output_tokens or 0:>6}{meta.latency_ms:>7}{meta.attempts:>6}"
        )
    print(
        f"\n{len(samples) - failed}/{len(samples)} parsed into valid typed results  "
        f"(tokens in={tokens_in} out={tokens_out})"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SAMPLES)))
