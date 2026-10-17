"""Questions about operations data, answered by text-to-SQL over the read-only ops database (W09)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.deps import (
    LLM,
    CanQueryOps,
    LLMConfig,
    OpsDatabaseDep,
    SessionFactory,
    enforce_llm_rate_limit,
    permit,
)
from app.errors import ERROR_RESPONSES, PROBLEM_JSON, Problem
from incidentops_ai.ops.text_to_sql import OpsAnswer, answer_question

_PROBLEM = {"model": Problem, "content": {PROBLEM_JSON: {}}}
router = APIRouter(
    prefix="/ops",
    tags=["ops"],
    dependencies=[Depends(permit("ops:query")), Depends(enforce_llm_rate_limit)],
    responses={**ERROR_RESPONSES, 401: _PROBLEM, 403: _PROBLEM, 429: _PROBLEM, 503: _PROBLEM},
)


class OpsQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=500)


@router.post("/query", response_model=OpsAnswer)
async def query_ops(
    body: OpsQuestion,
    principal: CanQueryOps,
    provider: LLM,
    config: LLMConfig,
    database: OpsDatabaseDep,
    sessions: SessionFactory,
):
    """Answer a question from the ops tables. The model writes one SELECT; it runs only if the
    validator accepts it, as a read-only role, with a timeout and a row cap. `status` says what
    happened: answered, declined, refused (with the rule), failed or model_failed."""
    return await answer_question(provider, config, database, body.question, sessions=sessions)
