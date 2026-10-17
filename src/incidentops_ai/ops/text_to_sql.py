"""Questions in plain language, answered from the ops database (AIENG-57).

The model writes SQL; the validator decides whether it may run; the reader role and the read-only
transaction decide what it can touch. Every outcome is a status, and every model call is recorded.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from incidentops_ai.llm.prompts import load_prompt
from incidentops_ai.llm.provider import LLMProvider, LLMStatus, ModelConfig
from incidentops_ai.llm.runs import LLMRunRepository
from incidentops_ai.ops.catalogue import schema_text
from incidentops_ai.ops.database import OpsDatabase, OpsQueryError
from incidentops_ai.ops.sql_guard import SqlRefused, validate
from incidentops_ai.security.pii import redact

PROMPT, VERSION = "text_to_sql", "v1"


class SqlAnswer(BaseModel):
    """What the model returns."""

    model_config = ConfigDict(extra="forbid")

    answerable: bool
    sql: str | None
    explanation: str


class OpsAnswer(BaseModel):
    status: Literal["answered", "declined", "refused", "failed", "model_failed"]
    question: str
    sql: str | None = None  # what ran (validated), or what the model wrote when it was refused
    columns: list[str] = []
    rows: list[list] = []
    truncated: bool = False
    rule: str | None = None  # the validator rule, or the database failure kind
    explanation: str | None = None


def _input(question: str) -> str:
    return f"Schema:\n\n{schema_text()}\n\nQuestion:\n<<<\n{question}\n>>>"


async def answer_question(
    provider: LLMProvider,
    config: ModelConfig,
    database: OpsDatabase,
    question: str,
    *,
    sessions: async_sessionmaker[AsyncSession] | None = None,
) -> OpsAnswer:
    prompt = load_prompt(PROMPT, VERSION, input_text=_input(redact(question).text))
    reply = await provider.generate_structured(prompt=prompt, schema=SqlAnswer, config=config)
    answer = await _answer(reply, database, question)
    if sessions is not None:
        async with sessions() as session:
            await LLMRunRepository(session).record(
                operation="ops_query",
                incident_id=None,
                status=reply.status,
                metadata=reply.metadata,
                error_detail=reply.error_detail,
                output={"status": answer.status, "sql": answer.sql, "rule": answer.rule},
            )
    return answer


async def _answer(reply, database: OpsDatabase, question: str) -> OpsAnswer:
    if reply.status is not LLMStatus.SUCCEEDED or reply.value is None:
        return OpsAnswer(status="model_failed", question=question, rule=reply.status.value)
    generated: SqlAnswer = reply.value
    if not generated.answerable or not generated.sql:
        return OpsAnswer(status="declined", question=question, explanation=generated.explanation)
    try:
        safe = validate(generated.sql)
    except SqlRefused as refused:
        return OpsAnswer(
            status="refused",
            question=question,
            sql=generated.sql,
            rule=refused.rule,
            explanation=generated.explanation,
        )
    try:
        result = await database.run(safe)
    except OpsQueryError as failed:
        return OpsAnswer(
            status="failed",
            question=question,
            sql=safe,
            rule=failed.kind,
            explanation=generated.explanation,
        )
    return OpsAnswer(
        status="answered",
        question=question,
        sql=safe,
        columns=result.columns,
        rows=result.rows,
        truncated=result.truncated,
        explanation=generated.explanation,
    )
