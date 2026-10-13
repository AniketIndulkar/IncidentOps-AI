# W02 roadmap: LLM application engineering (12–16 Oct 2026)

Source: Jira epic **AIENG-3** and tasks **AIENG-19 to AIENG-23**, read 10 Oct 2026. All six are To Do.
Overall plan: [ROADMAP.md](ROADMAP.md).

> **Status, 13 Oct 2026: complete.** All five tickets done on Tuesday, three days early.
> What was built and learned: [ROADMAP.md, "Current state"](ROADMAP.md). Decisions:
> [ADR-001](adr/001-llm-failure-handling.md). Two changes from this plan: prompts live inside
> the package (`src/incidentops_ai/prompts/`), and a second endpoint
> (`POST /incidents/{id}/related-incidents`) was added so the tool path is reachable.

## Goal of the week

Turn free-text incident reports into **typed, validated data** using an LLM, and make every way
the LLM can fail **explicit and tested**.

**Exit criterion (from the epic):** the incident extraction pipeline produces typed outputs and
fails safely under malformed responses and timeouts.

```
incident text ──▶ provider layer ──▶ LLM ──▶ typed IncidentExtraction ✅
                       │
                       └─ timeout / 429 / bad output / refusal ──▶ explicit failure state (never a guess)
```

## What already exists (from W01)

- `POST /incidents`, `GET /incidents/{id}`, idempotency keys, RFC 9457 errors
- PostgreSQL + Alembic, 26 tests, CI (lint → test → build), Docker image
- Postman collection (8 requests) in `postman/`

## Before Monday: one thing only you can do

| # | Task | Why |
|---|---|---|
| 1 | **Create an OpenAI API key and put it in `.env`** (git-ignored) as `OPENAI_API_KEY`, never in code, chat or CI | Needed from Tuesday (AIENG-20). The repo is public. |

**Provider: OpenAI** (decided 10 Oct; existing account credit covers the PREP "LLM API credit" item).
It supports what this week needs: structured outputs from a Pydantic model, strict function-tool
schemas, and parallel tool calls. The provider layer we build is provider-neutral, so this choice is
not permanent (W11 swaps in a gateway).

**Recommended account safety steps** (OpenAI dashboard, same idea as the AWS budget):

- Create a **project-scoped key** for IncidentOps, not a key with access to the whole account.
- Set a **monthly spend limit** and an email alert on that project.
- Turn **auto-recharge off**, so spend is capped by the prepaid balance.

**Model choice: decide on Monday** as part of AIENG-19, from OpenAI's official pricing page.
Model names and prices change often and third-party price lists disagree, so none are fixed here.
Guidance:

- Extraction is a small, well-specified task: a **mid or small tier** model is the sensible start.
- One extraction call is roughly 1.5k input and 0.7k output tokens; a week of development is a few
  hundred calls, so the cost is small on any tier.
- The model name lives in config (`INCIDENTOPS_LLM_MODEL`), so switching is a one-line change.
- Tests and CI never call a real model, so they cost nothing.

## Day by day

Each ticket is timeboxed to **2 hours**; unfinished work rolls forward.

### Mon 12 Oct · AIENG-19 · Read and design the provider layer

No code that calls an LLM today.

- **Read:** *AI Engineering* (Chip Huyen), the sections on model interfaces and structured outputs;
  then OpenAI's docs on structured outputs, function calling, errors and rate limits, and the
  pricing page (pick the model).
- **Design:** a small interface the rest of the app depends on, instead of the vendor SDK:

  ```
  LLMProvider
    generate_structured(prompt, schema, config) -> StructuredResult[schema]
    tool_call(prompt, tools, config)            -> ToolCallResult
  ModelConfig: model, max_tokens, timeout_s, max_retries, effort
  ```

- **Decide and write down:** where prompts live, what metadata every call returns (model, prompt
  version, tokens, latency, attempts, stop reason), and the list of failure states.
- **Deliverable:** `docs/notes/llm-provider-design.md`.
- **Done when:** the interfaces for `generate_structured` / `tool_call` and the model config are documented.

### Tue 13 Oct · AIENG-20 · Structured incident extraction

- **Schemas** (nested Pydantic, `src/incidentops_ai/extraction/models.py`): title, service, symptoms,
  error codes, environment, severity candidate, entities, **missing information**.
- **Provider:** first real implementation (`OpenAIProvider`, async client from the official `openai`
  SDK) behind the Monday interface, using strict structured output parsed into the Pydantic model.
- **Prompt v1** stored as a file with a version id.
- **Dataset:** 10 sample incident reports in `evals/extraction/samples.jsonl` (varied: clear, vague,
  missing service, multiple error codes, noisy logs).
- **Script:** `uv run python -m incidentops_ai.extraction.run_samples` prints a pass/fail table.
- **Done when:** all 10 samples parse into valid typed results.
- **Needs:** the API key.

### Wed 14 Oct · AIENG-21 · Validation, retries and safe failure

- **Fault injection** with a fake provider and a mocked HTTP layer: malformed JSON, schema mismatch,
  truncated output (`max_tokens`), timeout, 429, 5xx, refusal.
- **Bounded retry in our layer** (the SDK's own retries switched off, so behavior is visible and
  counted): exponential backoff with jitter, a hard attempt cap, `Retry-After` respected.
- **Explicit failure states, never coercion:** a failed extraction returns a status such as
  `failed_validation`, `timed_out`, `rate_limited`, `provider_error`, `refused`; it never returns
  half-filled or guessed data.

  | Failure | Retry? |
  |---|---|
  | Timeout, 429, 5xx, connection error | Yes, bounded |
  | Invalid output / schema mismatch | Once at most, then fail |
  | 400 bad request, auth error, refusal | No |

- **Done when:** automated tests cover retryable versus non-retryable paths. These tests run in CI with no API key.

### Thu 15 Oct · AIENG-22 · Tool calling, parallel tools, prompt versioning

- **One safe read-only tool:** `get_recent_incidents(service, limit)`, which reads our own
  `incidents` table. No writes, no external calls.
- **Validate tool arguments** with Pydantic before running anything; invalid arguments return an
  error result to the model and are never executed. Tool schema declared `strict`.
- **Parallel tool calls:** run them concurrently (`asyncio.gather`) and return all results in one message.
- **Run metadata persisted:** new table `llm_runs` (Alembic migration): prompt version, model,
  tokens in/out, latency, attempts, outcome, linked incident.
- **API:** `POST /incidents/{id}/extraction` to run and store an extraction; add it to the Postman collection.
- **Done when:** invalid tool arguments are rejected and execution metadata is persisted.
- **Risk:** this is the heaviest day. If time runs out, the API endpoint moves to Friday.

### Fri 16 Oct · AIENG-23 · Failure matrix, tests and ADR-001

- **Failure matrix** (`docs/notes/llm-failure-matrix.md`): schema errors, rate limits, timeouts,
  provider outage, low confidence → what the system does, what the user sees, what is logged.
- **Tests** for the important rows not yet covered.
- **ADR-001** (`docs/adr/001-llm-failure-handling.md`): when to **retry**, **fall back**,
  send to **human review**, or **reject**.
- **Done when:** the behavior is explicit and testable.
- **Also:** update `ROADMAP.md` current state; short Week 2 note.

## What the repo should look like on Friday

```
src/incidentops_ai/
  llm/            provider interface, OpenAIProvider, FakeProvider, retry, errors, config
  extraction/     schemas, extraction service, prompt loader
  tools/          read-only tool + argument validation
prompts/extract_incident/v1.md
evals/extraction/samples.jsonl
migrations/versions/…_add_llm_runs.py
docs/adr/001-llm-failure-handling.md
docs/notes/llm-provider-design.md, llm-failure-matrix.md
tests/            new: provider, retry, extraction, tools (all offline)
postman/          + extraction request
```

## Decisions to make early

| Decision | Recommendation | Why |
|---|---|---|
| Provider | OpenAI via the official `openai` SDK (decided) | Existing credit; hidden behind our interface |
| Model | Pick Monday from the official pricing page; start mid/small tier | Extraction is a simple task; name is in config |
| Structured output | Native structured outputs with the Pydantic model, not "please return JSON" prompts | Schema-valid by construction; fewer failure paths |
| Retries | Our own bounded retry; SDK retries off | Explicit, countable, testable (the point of AIENG-21) |
| Tests | Fake provider + mocked HTTP; no real calls in CI | Free, fast, deterministic; no key in a public repo |
| Real-model check | The 10-sample script, run by hand | Keeps spend small and deliberate |
| Prompts | Versioned files, version recorded with every run | Needed for W11 evals and regressions |
| Low confidence | Model reports `missing_information`; routing to human review is defined in ADR-001, built in W08 | Keeps W02 in scope |

## Risks

1. **No API key in `.env` by Tuesday** blocks AIENG-20. Mitigation: build the schemas, fake provider and tests first; real calls when the key is there.
2. **Thursday is overloaded** (tool, parallel calls, new table, endpoint). Mitigation: endpoint is the first thing to slip to Friday.
3. **Key leak in a public repo.** Mitigation: `.env` only, GitHub push protection on, CI has no secrets.
4. **Structured outputs don't remove all failures:** truncation, refusals, timeouts and semantically wrong values still happen. AIENG-21 covers these.
5. **Scope creep into agents.** W02 is single calls and one tool; orchestration is W07.
6. **Cost drift.** Mitigation: project spend limit, auto-recharge off, output-token cap, attempt cap, token counts logged per run from Thursday.
7. **SDK details differ from this plan.** The exact structured-output call and SDK retry/timeout defaults are confirmed in Monday's reading before any code is written.

## Carried into later weeks

- W03: extraction output (service, error codes) becomes retrieval metadata.
- W08: human-review routing from ADR-001.
- W11: provider layer swapped for a gateway; `llm_runs` feeds tracing and cost dashboards.
