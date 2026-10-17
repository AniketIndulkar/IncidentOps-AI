"""AIENG-40, 41 and 43: which extraction path answers which kind of document, measured.

    uv run --extra ocr python -m incidentops_ai.multimodal.benchmark

For every asset and question: the three citable text paths (`pdf-text`, `ocr`, `vlm-transcript`)
followed by a text-only answer, and `vlm-direct` (image + question) as the ceiling. The per-kind
decision rule and the router rule were fixed in docs/roadmap-w06.md before the first run.
"""

import argparse
import asyncio
import json
import re
import statistics
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from incidentops_ai.config import Settings
from incidentops_ai.llm.prompts import load_prompt
from incidentops_ai.llm.provider import (
    ImageInput,
    LLMProvider,
    LLMStatus,
    ModelConfig,
    Prompt,
    RunMetadata,
    StructuredResult,
)
from incidentops_ai.multimodal.extract import (
    Extraction,
    ExtractionError,
    Extractor,
    OcrExtractor,
    PdfTextExtractor,
    Transcript,
    VlmTranscriber,
    _images,
    contains_terms,
    detect_media_type,
    squeeze,
)

TEXT_PATHS = ("pdf-text", "ocr", "vlm-transcript")  # citable, cheapest first
ALL_PATHS = (*TEXT_PATHS, "vlm-direct")
IMAGE_KINDS = ("screenshot", "chart", "diagram")
PDF_KINDS = ("pdf-text", "pdf-scanned")

# Fixed before the run (docs/roadmap-w06.md, "How the routing is decided").
MIN_PDF_CHARS = 200  # per page, to trust a text layer
MIN_OCR_COVERAGE = 0.08  # below this share of the image covered by text, treat it as visual

# Cost is an estimate: the provider does not publish this model's price to us.
ASSUMED_USD_PER_M_INPUT = 2.50
ASSUMED_USD_PER_M_OUTPUT = 10.00


@dataclass(frozen=True)
class QuestionCase:
    id: str
    question: str
    expected: tuple[str, ...]


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    path: Path
    title: str
    questions: tuple[QuestionCase, ...]


def load_cases(path: Path) -> list[Case]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        questions = tuple(
            QuestionCase(q["id"], q["question"], tuple(q["expected"])) for q in record["questions"]
        )
        cases.append(
            Case(
                record["id"],
                record["kind"],
                path.parent / record["file"],
                record["title"],
                questions,
            )
        )
    return cases


_NUMERIC = re.compile(r"[\d.:,]+")


def _matches(alternative: str, answer: str) -> bool:
    if _NUMERIC.fullmatch(alternative):
        # Numbers must stand alone: "11" is not in "111", and "9" is not "0.9".
        return re.search(rf"(?<![\d.]){re.escape(alternative)}(?!\d)", answer) is not None
    return squeeze(alternative) in squeeze(answer)


def answer_correct(answer: str, expected: Sequence[str]) -> bool:
    """Correct if the answer holds every expected term (any of its `a|b` alternatives)."""
    if squeeze(answer) in ("", "unknown"):
        return False
    return all(any(_matches(alt, answer) for alt in term.split("|")) for term in expected)


def pick_path(correct: dict[str, int]) -> str:
    """The rule: the cheapest path, unless a costlier one answers at least one more question."""
    order = [path for path in TEXT_PATHS if path in correct]
    winner = order[0]
    for path in order[1:]:
        if correct[path] >= correct[winner] + 1:
            winner = path
    return winner


def route(
    media_type: str, *, pdf: Extraction | None, ocr: Extraction | None, vlm_available: bool
) -> str:
    """The automatic router, which does not know the kind of document."""
    has_text_layer = bool(pdf and pdf.chars_per_page) and min(pdf.chars_per_page) >= MIN_PDF_CHARS
    if media_type == "application/pdf" and has_text_layer:
        return "pdf-text"
    coverage = statistics.fmean(ocr.coverage) if ocr and ocr.coverage else 0.0
    if coverage < MIN_OCR_COVERAGE and vlm_available:
        return "vlm-transcript"
    return "ocr"


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


class ScriptedModel:
    """A stand-in model for tests and wiring checks: one fixed answer and one fixed transcript."""

    def __init__(self, *, answer: str, transcript: list[str]) -> None:
        self._answer = answer
        self._transcript = transcript

    async def generate_structured(self, *, prompt: Prompt, schema, config: ModelConfig):
        value = (
            Answer(answer=self._answer)
            if schema is Answer
            else Transcript(lines=list(self._transcript))
        )
        metadata = RunMetadata(
            provider="scripted",
            model=config.model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            attempts=1,
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
        )
        return StructuredResult[schema](status=LLMStatus.SUCCEEDED, value=value, metadata=metadata)

    async def tool_call(self, **_):  # pragma: no cover - not used here
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


@dataclass
class QuestionResult:
    question_id: str
    case_id: str
    kind: str
    path: str
    answer: str
    correct: bool
    in_text: bool | None  # does the extracted text hold the answer at all (text paths only)
    latency_ms: float
    input_tokens: int
    output_tokens: int


@dataclass
class KindSummary:
    correct: int = 0
    total: int = 0
    in_text: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    extraction_ms: list[float] = field(default_factory=list)

    @property
    def usd(self) -> float:
        return (
            self.input_tokens * ASSUMED_USD_PER_M_INPUT
            + self.output_tokens * ASSUMED_USD_PER_M_OUTPUT
        ) / 1_000_000


@dataclass
class BenchmarkRun:
    questions: list[QuestionResult]
    summary: dict[str, dict[str, KindSummary]]
    decision: dict[str, str]
    routes: dict[str, str]
    extractions: dict[str, dict[str, dict]]  # case id -> path -> what was extracted (or the error)
    model: str


async def _ask(
    model: LLMProvider,
    config: ModelConfig,
    prompt: Prompt,
    limiter: asyncio.Semaphore,
) -> tuple[str, float, int, int]:
    async with limiter:
        started = time.perf_counter()
        result = await model.generate_structured(prompt=prompt, schema=Answer, config=config)
    elapsed = (time.perf_counter() - started) * 1000
    tokens = (result.metadata.input_tokens or 0, result.metadata.output_tokens or 0)
    if result.status is not LLMStatus.SUCCEEDED or result.value is None:
        return f"<{result.status}>", elapsed, *tokens
    return result.value.answer, elapsed, *tokens


async def run_benchmark(
    cases: Sequence[Case],
    *,
    ocr: Extractor,
    model: LLMProvider,
    config_model: str,
    pdf: Extractor | None = None,
    concurrency: int = 4,
) -> BenchmarkRun:
    config = ModelConfig(
        model=config_model, max_output_tokens=2000, timeout_s=60, total_timeout_s=120
    )
    pdf = pdf or PdfTextExtractor()
    vlm = VlmTranscriber(model, config)
    limiter = asyncio.Semaphore(concurrency)
    questions: list[QuestionResult] = []
    summary: dict[str, dict[str, KindSummary]] = defaultdict(lambda: defaultdict(KindSummary))
    routes: dict[str, str] = {}
    extracted: dict[str, dict[str, dict]] = {}

    for case in cases:
        data = case.path.read_bytes()
        media_type = detect_media_type(data)
        paths: dict[str, Extraction | None] = {}
        errors: dict[str, str] = {}
        if media_type == "application/pdf":
            paths["pdf-text"] = await pdf.extract(data)
        paths["ocr"] = await ocr.extract(data)
        try:
            async with limiter:
                paths["vlm-transcript"] = await vlm.extract(data)
        except ExtractionError as error:
            paths["vlm-transcript"], errors["vlm-transcript"] = None, str(error)
        routes[case.id] = route(
            media_type, pdf=paths.get("pdf-text"), ocr=paths["ocr"], vlm_available=True
        )
        extracted[case.id] = {
            name: {"error": errors[name]}
            if extraction is None
            else {
                "text": extraction.text,
                "lines": len(extraction.lines),
                "chars_per_page": list(extraction.chars_per_page),
                "coverage": [round(c, 4) for c in extraction.coverage],
                "latency_ms": round(extraction.latency_ms, 1),
            }
            for name, extraction in paths.items()
        }
        for name, extraction in paths.items():
            row = summary[case.kind][name]
            if extraction is not None:
                row.extraction_ms.append(extraction.latency_ms)
                row.input_tokens += extraction.input_tokens or 0
                row.output_tokens += extraction.output_tokens or 0

        images = tuple(ImageInput(media_type=m, data=d) for d, m in _images(data))
        jobs = []
        for question in case.questions:
            for name, extraction in paths.items():
                jobs.append((question, name, extraction))
            jobs.append((question, "vlm-direct", None))

        async def answer(
            question: QuestionCase,
            name: str,
            extraction: Extraction | None,
            images: tuple[ImageInput, ...] = images,
        ):
            if name == "vlm-direct":
                prompt = load_prompt(
                    "answer_from_image", "v1", input_text=f"Question: {question.question}"
                )
                prompt = prompt.model_copy(update={"images": images})
                reply = await _ask(model, config, prompt, limiter)
                return question, name, None, reply
            if extraction is None or not extraction.lines:
                return question, name, False, ("unknown", 0.0, 0, 0)
            in_text = contains_terms(extraction.text, question.expected)
            prompt = load_prompt(
                "answer_from_text",
                "v1",
                input_text=f"Text:\n{extraction.text}\n\nQuestion: {question.question}",
            )
            return question, name, in_text, await _ask(model, config, prompt, limiter)

        for question, name, in_text, (text, ms, tokens_in, tokens_out) in await asyncio.gather(
            *(answer(*job) for job in jobs)
        ):
            correct = answer_correct(text, question.expected)
            questions.append(
                QuestionResult(
                    question.id,
                    case.id,
                    case.kind,
                    name,
                    text,
                    correct,
                    in_text,
                    ms,
                    tokens_in,
                    tokens_out,
                )
            )
            row = summary[case.kind][name]
            row.total += 1
            row.correct += int(correct)
            row.in_text += int(bool(in_text))
            row.input_tokens += tokens_in
            row.output_tokens += tokens_out

    decision = {
        kind: pick_path({p: summary[kind][p].correct for p in TEXT_PATHS if p in summary[kind]})
        for kind in summary
    }
    return BenchmarkRun(
        questions,
        {k: dict(v) for k, v in summary.items()},
        decision,
        routes,
        extracted,
        config_model,
    )


def _agreement(run: BenchmarkRun, kinds: dict[str, str]) -> tuple[int, int]:
    agree = sum(1 for case_id, path in run.routes.items() if path == run.decision[kinds[case_id]])
    return agree, len(run.routes)


def render_report(run: BenchmarkRun, *, run_date: str) -> str:
    kinds = {q.case_id: q.kind for q in run.questions}
    lines = [
        f"# Multimodal extraction benchmark, {run_date}",
        "",
        f"- Model: `{run.model}` (vision and text answers); OCR: RapidOCR, local",
        (
            f"- {len(kinds)} assets, {len({q.question_id for q in run.questions})} questions; "
            "an answer is correct when it holds every expected term"
        ),
        "- Reproduce: `uv run --extra ocr python -m incidentops_ai.multimodal.benchmark`",
        (
            f"- Cost is an *estimate* at an assumed price (${ASSUMED_USD_PER_M_INPUT} per million "
            f"input tokens, ${ASSUMED_USD_PER_M_OUTPUT} per million output tokens); OCR and PDF text "
            "extraction cost no API money."
        ),
        "",
        "## By kind",
        "",
        "| Kind | Path | Correct | Text holds the answer | Extraction ms (mean) | Tokens in / out | est. $ |",
        "|---|---|---|---|---|---|---|",
    ]
    for kind in (*IMAGE_KINDS, *PDF_KINDS):
        for path in ALL_PATHS:
            row = run.summary.get(kind, {}).get(path)
            if row is None or row.total == 0:
                continue
            held = f"{row.in_text}/{row.total}" if path != "vlm-direct" else "n/a"
            ms = f"{statistics.fmean(row.extraction_ms):.0f}" if row.extraction_ms else "-"
            lines.append(
                f"| {kind} | {path} | **{row.correct}/{row.total}** | {held} | {ms} | "
                f"{row.input_tokens:,} / {row.output_tokens:,} | {row.usd:.4f} |"
            )
    agree, total = _agreement(run, kinds)
    lines += [
        "",
        "## Decision",
        "",
        (
            "By the rule fixed in `docs/roadmap-w06.md`: per kind, the cheapest citable path "
            "(`pdf-text` < `ocr` < `vlm-transcript`) unless a costlier one answers at least one more "
            "question. `vlm-direct` is the ceiling and cannot be cited."
        ),
        "",
        "| Kind | Chosen path |",
        "|---|---|",
        *[f"| {kind} | **{path}** |" for kind, path in run.decision.items()],
        "",
        "## Router",
        "",
        (
            f"The automatic router (text layer ≥ {MIN_PDF_CHARS} characters per page → `pdf-text`; "
            f"OCR boxes covering < {MIN_OCR_COVERAGE:.0%} of the image → `vlm-transcript`; "
            f"otherwise `ocr`) agrees with the per-kind choice on **{agree} of {total}** assets."
        ),
        "",
        "| Asset | Kind | OCR coverage | Router | Per-kind choice |",
        "|---|---|---|---|---|",
    ]
    for case_id, path in run.routes.items():
        coverage = run.extractions[case_id].get("ocr", {}).get("coverage", [])
        shown = ", ".join(f"{c:.3f}" for c in coverage) or "-"
        kind = kinds[case_id]
        mark = "" if path == run.decision[kind] else " ✗"
        lines.append(f"| {case_id} | {kind} | {shown} | {path}{mark} | {run.decision[kind]} |")
    lines += [
        "",
        "## Every answer",
        "",
        "| Question | Path | Correct | In text | Answer |",
        "|---|---|---|---|---|",
    ]
    for q in sorted(run.questions, key=lambda r: (r.question_id, ALL_PATHS.index(r.path))):
        held = "-" if q.in_text is None else ("yes" if q.in_text else "no")
        answer = q.answer.replace("|", "/").replace("\n", " ")[:90]
        lines.append(
            f"| {q.question_id} | {q.path} | {'✓' if q.correct else '✗'} | {held} | {answer} |"
        )
    return "\n".join(lines) + "\n"


def write_report(
    run: BenchmarkRun, out_dir: Path, *, run_date: str, name: str
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / f"{run_date}-{name}.md"
    data = out_dir / f"{run_date}-{name}.json"
    report.write_text(render_report(run, run_date=run_date), encoding="utf-8")
    payload = {
        "date": run_date,
        "model": run.model,
        "decision": run.decision,
        "routes": run.routes,
        "summary": {
            kind: {path: {**asdict(row), "usd": round(row.usd, 6)} for path, row in rows.items()}
            for kind, rows in run.summary.items()
        },
        "questions": [asdict(q) for q in run.questions],
        "extractions": run.extractions,
    }
    data.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report, data


class _NoText:
    method = "ocr"

    async def extract(self, data: bytes) -> Extraction:
        return Extraction("ocr", (), 1, coverage=(0.0,))


async def _run(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases)
    if args.fake:
        model: LLMProvider = ScriptedModel(answer="unknown", transcript=[])
        ocr: Extractor = _NoText()
        model_name = "scripted"
    else:
        from incidentops_ai.llm.factory import build_provider

        settings = Settings()
        if not settings.llm_model:
            raise SystemExit("set INCIDENTOPS_LLM_MODEL (and OPENAI_API_KEY) for a real run")
        model = build_provider(settings)
        ocr = OcrExtractor()
        model_name = settings.llm_model
    try:
        run = await run_benchmark(cases, ocr=ocr, model=model, config_model=model_name)
    finally:
        await model.aclose()
    report, data = write_report(run, args.out_dir, run_date=args.date, name=args.name)
    print(f"decision: {run.decision}")
    print(f"Wrote {report} and {data}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark multimodal extraction (AIENG-40 to 43)."
    )
    parser.add_argument("--cases", type=Path, default=Path("evals/multimodal/cases.jsonl"))
    parser.add_argument("--out-dir", type=Path, default=Path("evals/multimodal/runs"))
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--name", default="multimodal-benchmark")
    parser.add_argument("--fake", action="store_true", help="scripted model, no OCR (wiring check)")
    asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
