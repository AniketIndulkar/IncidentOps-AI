# Architecture decision records

Each record states the problem, the decision, the options rejected and the evidence (a findings
file with the measurements). All are accepted; none has been superseded. Where they sit in the
system: [architecture.md](../architecture.md).

| ADR | Week | Decision in one line | Evidence |
|---|---|---|---|
| [001](001-llm-failure-handling.md) LLM failure handling | W02 | Choose the response by who can fix the failure: retry what time fixes, retry bad output once, reject what only an operator fixes, send thin results to a person; never repair a reply | [LLM failure matrix](../notes/llm-failure-matrix.md) |
| [002](002-knowledge-graph.md) The knowledge graph | W05 | The graph answers structured questions (ownership, on-call, dependencies) shown as context, never as citations; it does not steer retrieval by default and is optional at runtime | [graph findings](../../evals/graph/runs/2026-10-17-graph-findings.md) |
| [003](003-multimodal-routing.md) Attachments | W06 | Route by what the file is (text layer, OCR, vision transcript); only text is cited, with its provenance; attachments stay with their incident | [multimodal findings](../../evals/multimodal/runs/2026-10-17-multimodal-findings.md) |
| [004](004-agentic-vs-deterministic-steps.md) Agentic vs deterministic steps | W07 | A fixed LangGraph graph with two checked model steps; the route and the recommendation are code; checkpoints in PostgreSQL | [workflow findings](../../evals/workflow/runs/2026-10-17-workflow-findings.md) |
| [005](005-approval-gate-and-tool-trust-boundary.md) Approval gate and tool trust | W08 | Every run stops at one gate; audit and memory are append-only; tools over MCP, writes carry a signed approval; every loop has a budget | [W08 findings](../../evals/workflow/runs/2026-10-17-w08-findings.md) |
| [006](006-text-to-sql-and-integration-failures.md) Text-to-SQL and integrations | W09 | Four independent layers around generated SQL; every outside call has a contract; a downstream failure is a named state | [W09 findings](../../evals/sql/runs/2026-10-17-w09-findings.md) |
| [007](007-identity-tenancy-and-model-controls.md) Identity, tenancy, model controls | W10 | Identity from the token, permissions per route, the tenant enforced where data is read (row-level security for SQL), controls around the model in code, attacks as tests | [W10 findings](../../evals/security/runs/2026-10-17-w10-findings.md) |
| [008](008-evaluation-gateway-and-tracing.md) Evaluation, gateway, tracing | W11 | Four evaluation layers; a judge reported, not gated, until it agrees with labels; one gateway for every model call; one trace per run; a CI gate on prompt changes | [W11 findings](../../evals/generation/runs/2026-10-17-w11-findings.md) |
| [009](009-reliability-and-degradation.md) Reliability and degradation | W12 | Every dependency bounded and ending in a named state; runs left by a dead process recovered; irreversible writes serialised per run; degradation visible in `GET /status` | [W12 findings](../../evals/reliability/runs/2026-10-17-w12-findings.md) |

The retrieval stack (W03 and W04: chunking, dense retrieval, hybrid with BM25, the reranker left
off) was decided by pre-registered comparisons recorded as findings rather than ADRs:
[dense baseline](../../evals/retrieval/runs/2026-10-16-dense-baseline-findings.md),
[hybrid](../../evals/retrieval/runs/2026-10-16-hybrid-findings.md).

## Writing a new one

Copy the shape of 009: status and date, context tickets, evidence links, then Context, Decision,
Why not the alternatives, Consequences, Revisit when. Write the hypotheses before the measurement,
and link the findings file the decision rests on. A decision that replaces an earlier one says so
in both.
