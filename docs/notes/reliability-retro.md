# Reliability retrospective (AIENG-73, W12)

Written after the week's load, failure and recovery work ([findings](../../evals/reliability/runs/2026-10-17-w12-findings.md)).

## What the week showed

- Under load the system waits on the model; its own overhead is small but grows (42 → 402 ms per
  run from 1 to 100 concurrent responders).
- Three real failure modes were found only by forcing them: an unbounded retrieval, a run left
  `running` by a dead process, and duplicate tickets from concurrent writes. All three are fixed
  and pinned by tests.
- One conclusion was wrong and had to be retracted: the first bottleneck was the load generator.

## The top five reliability risks

1. **Real provider limits.** The simulated model cannot show a provider's rate limits, quota or
   slow tails; at 9 triages a second, a real account's tokens-per-minute limit would be the first
   wall. Mitigation in place: the gateway's fallback and breaker. Missing: a measured limit.
2. **Stale runs are recovered only at startup.** After a database blip the API keeps running, so
   runs interrupted by it stay `running` until someone restarts the process.
3. **Ticket deduplication depends on the ticket system's search.** The advisory lock serialises
   our writers, but a Jira-style search index that lags could still miss a just-created issue.
4. **One process.** More API processes were not measured; stale-run recovery with several
   replicas relies on its age threshold being longer than any live run.
5. **Checkpoints grow without retention** (W07 risk), and so do traces and `llm_runs`.

## The one change before v1.0

**Run stale-run recovery periodically, not only at startup** (risk 2): a small background sweep
in the API process, at the same age threshold, so a database interruption does not need a restart
to become resumable. It goes on the v1.0 must-fix list.
