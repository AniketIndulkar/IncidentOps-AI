"""Text-to-SQL with the real model, against H38 in roadmap-w09.md (AIENG-57).

    INCIDENTOPS_DATABASE_URL=<scratch admin url> INCIDENTOPS_OPS_DATABASE_URL=<reader url> \
        uv run python -m incidentops_ai.ops.benchmark

Each of the 15 questions is answered through the full path (model, validator, reader role); the
result is compared with the reference SQL's result by the rule fixed in the roadmap. The 8
malicious questions go through the same path; for them, the outcome is recorded: declined (the
model said no), refused (the validator said no), or answered (it ran, which can only be a SELECT
over the allow-list).
"""

import asyncio
import json
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from incidentops_ai.config import Settings
from incidentops_ai.llm.factory import build_model_config, build_provider
from incidentops_ai.ops.database import OpsDatabase, OpsResult
from incidentops_ai.ops.sql_guard import validate
from incidentops_ai.ops.text_to_sql import answer_question
from incidentops_ai.workflow.benchmark import Counting

QUESTIONS = Path("evals/sql/questions.jsonl")
MALICIOUS = Path("evals/sql/malicious.jsonl")


def _norm(value):
    if isinstance(value, (Decimal, float, int)) and not isinstance(value, bool):
        return round(float(value), 6)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def same_answer(reference: OpsResult, generated: OpsResult, *, ordered: bool) -> bool:
    """Same row count, and every reference column has a generated column with the same values
    (in order when the question asks for one). Extra generated columns are allowed."""
    if len(reference.rows) != len(generated.rows):
        return False
    produced = [[_norm(r[i]) for r in generated.rows] for i in range(len(generated.columns))]
    for i in range(len(reference.columns)):
        wanted = [_norm(r[i]) for r in reference.rows]
        if ordered:
            matched = any(column == wanted for column in produced)
        else:
            matched = any(
                Counter(map(repr, column)) == Counter(map(repr, wanted)) for column in produced
            )
        if not matched:
            return False
    return True


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def run(settings: Settings) -> dict:
    provider = Counting(build_provider(settings))
    config = build_model_config(settings)
    engine = create_async_engine(settings.ops_database_url)
    database = OpsDatabase(engine)
    rows, attacks = [], []
    try:
        for q in _load(QUESTIONS):
            reference = await database.run(validate(q["reference_sql"]))
            answer = await answer_question(provider, config, database, q["question"])
            generated = OpsResult(
                columns=answer.columns, rows=answer.rows, truncated=answer.truncated
            )
            correct = answer.status == "answered" and same_answer(
                reference, generated, ordered=q["ordered"]
            )
            rows.append(
                {
                    "id": q["id"],
                    "status": answer.status,
                    "rule": answer.rule,
                    "sql": answer.sql,
                    "correct": correct,
                }
            )
            print(q["id"], answer.status, correct)
        for m in _load(MALICIOUS):
            answer = await answer_question(provider, config, database, m["question"])
            attacks.append(
                {
                    "id": m["id"],
                    "question": m["question"],
                    "status": answer.status,
                    "rule": answer.rule,
                    "sql": answer.sql,
                }
            )
            print(m["id"], answer.status, answer.rule)
    finally:
        await engine.dispose()
    return {
        "model": config.model,
        "questions": rows,
        "malicious": attacks,
        "calls": provider.calls,
        "input_tokens": provider.input_tokens,
        "output_tokens": provider.output_tokens,
    }


def render(report: dict, date_: str) -> str:
    q = report["questions"]
    lines = [
        f"# Text-to-SQL with the real model ({date_})",
        "",
        (
            f"Model `{report['model']}`; {report['calls']} model calls, "
            f"{report['input_tokens']:,} input and {report['output_tokens']:,} output tokens."
        ),
        "",
        f"**Correct: {sum(r['correct'] for r in q)} of {len(q)}.**",
        "",
        "| Question | Status | Correct | SQL that ran |",
        "|---|---|---|---|",
    ]
    for r in q:
        sql = (r["sql"] or "–").replace("|", "\\|")
        lines.append(f"| {r['id']} | {r['status']} | {'yes' if r['correct'] else 'no'} | `{sql}` |")
    lines += [
        "",
        "## Malicious questions",
        "",
        "| Id | Question | Outcome | Rule | SQL |",
        "|---|---|---|---|---|",
    ]
    for a in report["malicious"]:
        sql = (a["sql"] or "–").replace("|", "\\|")
        lines.append(
            f"| {a['id']} | {a['question']} | {a['status']} | {a['rule'] or '–'} | `{sql}` |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    today = datetime.now(UTC).date().isoformat()
    report = asyncio.run(run(Settings()))
    stem = Path(f"evals/sql/runs/{today}-text-to-sql")
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    stem.with_suffix(".md").write_text(render(report, today))
    print(render(report, today))


if __name__ == "__main__":
    main()
