# Chunking experiment, 2026-10-16

- Embedding model: `text-embedding-3-small` (1536 dimensions)
- Corpus: 18 documents, 34686 characters
- Questions: 30 (42 evidence spans); a chunk is relevant when it holds at least half of a span
- Retrieval: dense, cosine, exact scan, top 10, no filters unless stated
- Reproduce: `uv run python -m incidentops_ai.retrieval.chunking_experiment`

## Primary configurations

| Config | Chunks | Mean chars | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Precision@5 | MRR | Chars read@5 |
|---|---|---|---|---|---|---|---|---|---|
| fixed-500-50 | 82 | 459 | 0.461 | 0.744 | 0.917 | 0.950 | 0.260 | 0.690 | 2332 |
| fixed-1000-100 | 44 | 845 | 0.378 | 0.900 | 0.967 | 0.983 | 0.273 | 0.673 | 4198 |
| fixed-2000-200 | 23 | 1551 | 0.800 | 0.983 | 0.983 | 1.000 | 0.260 | 0.961 | 7433 |
| paragraph-1000 | 48 | 721 | 0.483 | 0.883 | 0.933 | 0.983 | 0.227 | 0.725 | 3659 |
| section-1500 | 95 | 363 | 0.622 | 0.833 | 0.933 | 0.967 | 0.233 | 0.801 | 1961 |

Selected: **fixed-2000-200**, by the rule fixed in `docs/roadmap-w03.md` (highest MRR; configs within 0.02 of it are ordered by Recall@5, then by smaller chunks).

## MRR by question style

| Style | n | fixed-500-50 | fixed-1000-100 | fixed-2000-200 | paragraph-1000 | section-1500 |
|---|---|---|---|---|---|---|
| identifier | 5 | 0.690 | 0.633 | 1.000 | 0.700 | 0.850 |
| lookup | 8 | 0.708 | 0.812 | 0.854 | 0.833 | 0.854 |
| multi | 5 | 0.567 | 0.540 | 1.000 | 0.600 | 0.617 |
| paraphrase | 8 | 0.698 | 0.625 | 1.000 | 0.812 | 0.833 |
| procedure | 4 | 0.792 | 0.708 | 1.000 | 0.521 | 0.800 |

## Context header (exploratory)

The same chunks, but each is embedded together with `document title > section`.

| Config | Chunks | Mean chars | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Precision@5 | MRR | Chars read@5 |
|---|---|---|---|---|---|---|---|---|---|
| fixed-500-50 | 82 | 459 | 0.461 | 0.744 | 0.917 | 0.950 | 0.260 | 0.690 | 2332 |
| fixed-1000-100 | 44 | 845 | 0.378 | 0.900 | 0.967 | 0.983 | 0.273 | 0.673 | 4198 |
| fixed-2000-200 | 23 | 1551 | 0.800 | 0.983 | 0.983 | 1.000 | 0.260 | 0.961 | 7433 |
| paragraph-1000 | 48 | 721 | 0.483 | 0.883 | 0.933 | 0.983 | 0.227 | 0.725 | 3659 |
| section-1500 | 95 | 363 | 0.622 | 0.833 | 0.933 | 0.967 | 0.233 | 0.801 | 1961 |
| fixed-500-50+ctx | 82 | 459 | 0.528 | 0.811 | 0.933 | 0.967 | 0.280 | 0.774 | 2334 |
| fixed-1000-100+ctx | 44 | 845 | 0.478 | 0.900 | 0.967 | 1.000 | 0.273 | 0.764 | 4123 |
| fixed-2000-200+ctx | 23 | 1551 | 0.767 | 0.983 | 0.983 | 1.000 | 0.260 | 0.950 | 7436 |
| paragraph-1000+ctx | 48 | 721 | 0.450 | 0.917 | 0.983 | 0.983 | 0.247 | 0.729 | 3599 |
| section-1500+ctx | 95 | 363 | 0.722 | 0.850 | 0.950 | 0.983 | 0.240 | 0.889 | 2016 |

## Service filter (exploratory)

The selected config, with and without restricting the search to the service named in the question (what extraction would supply). Questions with no service are unrestricted in both rows.

| Config | Chunks | Mean chars | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Precision@5 | MRR | Chars read@5 |
|---|---|---|---|---|---|---|---|---|---|
| fixed-2000-200 (no filter) | 23 | 1551 | 0.800 | 0.983 | 0.983 | 1.000 | 0.260 | 0.961 | 7433 |
| fixed-2000-200 (service filter) | 23 | 1551 | 0.833 | 1.000 | 1.000 | 1.000 | 0.267 | 0.983 | 3664 |
