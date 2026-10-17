# W13 roadmap: the v1.0 release (28 Dec 2026–1 Jan 2027)

Source: Jira epic **AIENG-14** and tasks **AIENG-74 to AIENG-78**, read 17 Oct 2026. Overall plan:
[ROADMAP.md](ROADMAP.md). Built early, stacked on W12 (branch `feat/w13-v1-release`).

> **Status, 17 Oct 2026: built; v1.0 ready to tag.** Design written before any W13 work; decisions
> are mine (W05 to W13 built back to back at the user's request).
>
> **Result:** H54 to H56 hold. The guide was followed from an empty volume to a real triage and an
> approved ticket; it found five gaps, all fixed or documented, the largest being a compose file
> that passed only some settings to the API. Writing the cost model found a second must-fix (reads
> and decisions spent the model budget). Findings:
> [w13-findings](../evals/release/2026-10-17-w13-findings.md); [release notes](release/v1.0-release-notes.md).
>
> **Not done without the user:** tagging `v1.0` (and `v0.1`, `v0.2`), recording the walkthrough,
> any AWS deployment, merging the stacked pull requests, Jira updates.

## Goal of the week

Stop adding features and turn the quarter's work into something another engineer could run,
operate and judge from the repository alone.

**Exit criterion (from the epic):** v1.0; architecture, threat, evaluation and benchmark documents;
deployment guide and runbook; cost analysis; known limitations; a recorded walkthrough.

## AIENG-74: scope freeze and the release checklist

**Scope is frozen from this commit**: no new features. Every open item from the ROADMAP's risk list,
the weekly findings and the retrospective is classified in `docs/release/v1.0-checklist.md`:

- **must-fix**: a defect or gap that would mislead or endanger a user of v1.0. Fixed this week,
  each with a test.
- **defer**: worth doing, not needed for v1.0, with the reason.
- **known limitation**: true of v1.0 and documented where a user will see it.

The one must-fix already chosen by the retrospective: **stale-run recovery runs periodically**, not
only at startup.

## AIENG-75: architecture view and ADR index

`docs/architecture.md`: one diagram and a page per boundary (ingress and auth, retrieval, graph,
multimodal, orchestration, tools, observability, deployment), each naming its code, its ADR and its
failure behaviour. `docs/adr/README.md` indexes the nine ADRs.

## AIENG-76: reproducible reports

`docs/reports/`: evaluation (retrieval, generation, workflow), benchmarks (performance, cost), and
security (the threat model's tests and the attack results). Each claim names its dataset, the
command that reproduces it, the committed result, failure examples and limits. Nothing is re-run
with a real model this week unless a must-fix changes a prompt (none planned).

## AIENG-77: README, a deployment verified from clean, cost model, limitations

- The README is rewritten for a newcomer: what it is, quick start, configuration, operations.
- `docker-compose.yml` and the deployment guide are brought up to v1.0 and **verified from clean**:
  an empty Docker volume, the documented steps only, until `GET /status` answers. Every step that
  turned out to be missing is added to the guide (or fixed), and listed in the findings.
- `docs/cost-model.md`: per-triage and monthly estimates from the recorded token counts, labelled as
  estimates at the assumed price.
- `docs/known-limitations.md`, from the checklist.

## AIENG-78: walkthrough and tag

`docs/release/walkthrough-script.md`: a 10 to 15 minute outline (the problem, architecture,
retrieval, evals, graph and multimodal choices, agent boundaries, security, failure handling,
cost, what changes at 10× scale) for the user to record. The release notes for v1.0 are written;
**the tag and the recording are the user's.**

## Hypotheses

- **H54.** Following only the deployment guide, a clean local deployment reaches `GET /status`
  with the database `ok`; every gap found on the way ends up documented or fixed.
- **H55.** Every number in the README and the reports links to a committed result and a command.
- **H56.** With the periodic sweep, a run left `running` by a dead process becomes resumable within
  one sweep interval, without a restart.

## Risks

1. Documentation written by the builder reads clearer to the builder than to anyone else.
2. A local Docker deployment is not the AWS one; the Lightsail guide is updated but not re-run.
