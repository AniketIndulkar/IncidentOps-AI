# Known limitations (v1.0)

What v1.0 does not do, has not shown, or does imperfectly. Each one is true of the release as
shipped; none hides a wrong answer behind a confident one (the failure modes in
[architecture.md](architecture.md) all end in a named state or in review). Numbers in brackets are
risks in [ROADMAP.md](ROADMAP.md); the release decisions are in [the v1.0 checklist](release/v1.0-checklist.md).

## What the evidence can and cannot say

- **The evaluation sets are small and single-author** (3 to 30 cases per layer; one author, the
  assistant that built the system, wrote the cases, the labels and the code) [7, 14, 18, 26, 30]. They show the design behaving as
  intended on cases written to probe it, not an accuracy rate on real incidents.
- **The faithfulness judge is reported, never gated.** It agrees with the labels 77% of the time
  (kappa 0.45), below the 80% / 0.6 bar [45]. Its 13 labels were written by the assistant; have a
  person re-label them before letting it gate anything.
- **Load was measured with a simulated model** and on one machine [49]. A real provider's rate
  limits and slow tails, and more than one API process, are unmeasured. Memory under load is
  unmeasured.
- **The adapters are tested against fakes** [39]: Jira, the CMDB and Langfuse have not met the
  real systems. No real identity provider has been connected [44, 46].

## What the model-facing parts cannot guarantee

- **A citation proves the words, not the claim** [22]. The check confirms that the quoted text is
  in the cited evidence; a cause can still draw the wrong conclusion from a true quote.
- **Routes inherit noise from what the model chooses to list** [31]. The review rule for open
  questions fires at four; the model listed four in one run and three in the next for the same
  incident.
- **Vision transcripts can misread faithfully, and OCR can drop lines** [28, 29]: a citation then
  quotes the misreading. The attachment router picks the best extractor for 9 of 14 assets.
- **A recommendation can rest on one reviewer's note** [35]: notes are recalled as evidence and
  cited like documents.
- **An off-corpus incident is declined by the model, not by a threshold** [23]: 3 of 3 in W11.
  There is no relevance gate before diagnosis.
- **Prompt changes cost a real-model run** [47]: the CI gate fails until the generation baseline is
  refreshed (about $0.20).

## Security

- **PII detection is pattern-based** [42]: names, addresses and anything without a shape are not
  redacted.
- **The injection detector is a phrase list** [43]: it missed one phrasing in the real-model run.
  It routes to review; it never blocks.
- **The static API token is an admin credential with no expiry** [41]. Use JWTs for people; keep
  the token for automation and rotate it by redeploying.
- **The approval secret can mint approvals, and the JWT key can mint identities** [36]. Store both
  as secrets.
- **Catalog reads depend on the SQL validator alone** [38]: the reader login can read
  `pg_catalog`, as every PostgreSQL role can. Moving the ops data to its own database closes it.
- **Ticket deduplication leans on the ticket system's search** [50] (plus an advisory lock per run
  across processes).

## Operations

- **`GET /status` does not check that the corpus is ingested** (found in W13) [54]. An empty corpus
  sends every run to review (`no_answer`) rather than to a wrong answer; step 4 of
  [deploy.md](deploy.md) covers it.
- **The Langfuse send runs on the event loop** (found in W13) [53]. It is bounded at 5 s, but a slow
  Langfuse can stall other requests for that long at the end of each run. Prefer the JSONL trace
  file until this is moved to a background thread.
- **Nothing expires**: idempotency keys [10], checkpoints, traces and `llm_runs` [33], and runs
  waiting at the approval gate [37] are kept until someone deletes them.
- **Neo4j is a second datastore to run** [27]; the graph is optional, and everything works without
  it.
- **The reranker does not fit a 1 GB server** [25] (+1.3 GB); it is off by default.
- **Exact vector search** is right below about 10,000 chunks [20]; beyond that, add an approximate
  index.
- **There is no endpoint to update an incident** [15], so no lost-update protection is needed yet.
- **The AWS Lightsail guide was written for W01** and has not been re-run for v1.0.
