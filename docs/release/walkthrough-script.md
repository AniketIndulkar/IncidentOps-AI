# v1.0 walkthrough script (AIENG-78)

A 12 to 15 minute recording for an engineer who has not seen the project. Each section says what
to show and the point to make; the wording is yours. Spend: one real triage, about $0.02.

**Before recording:** the stack running from [deploy.md](../deploy.md) with the corpus ingested
and indexed, the bundled ticket server on, `GET /status` answering `FULL`; a terminal with
`TOKEN` set; [architecture.md](../architecture.md) open in a browser. Have one incident's JSON ready
to paste.

## 1. The problem (1 min)

**Show:** the README's first paragraph.

**Say:** an on-call engineer at 3 a.m. needs the right runbook, the likely cause and who owns the
service, fast. A chatbot that answers confidently and wrongly is worse than nothing. So the
question for every part of this system was "how does it fail?", not "how clever is it?".

## 2. One triage, end to end (3 min)

**Show:** create an incident, `POST …/triage`, then the response: `steps` (the step log with
timings), `diagnosis.causes[].citations` (a quote and the document it came from), `route`,
`reasons`, `proposed_action`. Approve it; show `status: "actioned"` and the ticket. Then
`GET …/events`.

**Say:** about six seconds and two cents. Two steps called a model; everything else was code.
The run waited for a person before writing anything. The events table is append-only: the
database refuses to change it.

## 3. The architecture (2 min)

**Show:** the two diagrams in [architecture.md](../architecture.md), then scroll one failure table.

**Say:** one API process, PostgreSQL for everything that matters (incidents, embeddings,
checkpoints, the audit trail), optional Neo4j, an optional ticket system. Every boundary on this
page has a table of how it fails, and every row was checked against the code.

## 4. Retrieval and citations (1.5 min)

**Show:** the retrieval table in [the evaluation report](../reports/evaluation.md#retrieval-w03-w04).

**Say:** hybrid search, dense plus BM25 fused by rank, won a comparison whose rule was written
before the run. The reranker lost: slower, 1.3 GB heavier, no better. A citation is checked
against the retrieved text, which proves the words and not the reasoning: that is a documented
limitation, not a solved problem.

## 5. Choices that said no (1.5 min)

**Show:** the ADR index's one-liners for 002 and 003.

**Say:** the knowledge graph answers who owns a service and what depends on it, but steering
retrieval with it did not help, so it does not. Images are read by a vision model, but only its
*transcript* is cited, because a direct answer has nothing to quote. And the attachment router
picked the best extractor for only 9 of 14 assets (H23): it is reported as refuted, not hidden.

## 6. Where the agent stops (1.5 min)

**Show:** the triage graph diagram; [ADR-004](../adr/004-agentic-vs-deterministic-steps.md),
decision 1 to 4.

**Say:** a fixed graph. No model decides the next step, chooses a tool that writes, or decides the
route. The recommendation is assembled by code from facts already gathered. The one write needs
a person's approval, signed over its exact arguments.

## 7. Security (1.5 min)

**Show:** the summary table in [the security report](../reports/security.md);
`uv run pytest -q -m redteam` finishing.

**Say:** identity from the token, permissions per route, the tenant enforced where data is read,
down to row-level security for generated SQL. Attacks are tests: 170 of them run in CI. Against a
model scripted to obey every injection, the controls held 8 of 8.

## 8. Failure handling (2 min)

**Show:** stop the database (`docker compose stop db`), `GET /status` → `MANUAL`; start it again →
`FULL`. Then the runbook's table of contents.

**Say:** every dependency is bounded and ends in a named state. The load test found that my own
load generator was the bottleneck, not the pools I blamed, and that finding is kept, marked
superseded. Forcing failures found three real faults, including duplicate tickets under
concurrency, now prevented by a lock in the database.

## 9. Cost (1 min)

**Show:** [cost-model.md](../cost-model.md), the per-month table.

**Say:** about two cents a triage at the assumed price; about $119 a month at 200 incidents a day.
The rate limit is the spend ceiling, and every model call is recorded with its tokens.

## 10. At 10× (1 min)

**Show:** the last section of [architecture.md](../architecture.md).

**Say:** the model is 95% of a run, so 10× is mostly a provider account question: its rate
limits are unmeasured. Then an approximate vector index, more API processes, and retention for
checkpoints and traces. And the evaluation sets need a second author: they were written by the
same assistant that built the system.

## Close (15 s)

**Show:** [known-limitations.md](../known-limitations.md).

**Say:** what it can't do is written down, next to what it can.
