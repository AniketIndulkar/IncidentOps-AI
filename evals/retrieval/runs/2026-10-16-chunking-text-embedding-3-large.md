# Chunking experiment, 2026-10-16

- Embedding model: `text-embedding-3-large` (1536 dimensions)
- Corpus: 18 documents, 34686 characters
- Questions: 30 (42 evidence spans); a chunk is relevant when it holds at least half of a span
- Retrieval: dense, cosine, exact scan, top 10, no filters unless stated
- Reproduce: `uv run python -m incidentops_ai.retrieval.chunking_experiment`

## Primary configurations

| Config | Chunks | Mean chars | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Precision@5 | MRR | Chars read@5 |
|---|---|---|---|---|---|---|---|---|---|
| fixed-500-50 | 82 | 459 | 0.500 | 0.822 | 0.917 | 0.983 | 0.267 | 0.725 | 2346 |
| fixed-1000-100 | 44 | 845 | 0.544 | 0.917 | 0.983 | 1.000 | 0.280 | 0.797 | 4358 |
| fixed-2000-200 | 23 | 1551 | 0.800 | 0.950 | 0.983 | 1.000 | 0.260 | 0.958 | 7704 |
| paragraph-1000 | 48 | 721 | 0.550 | 0.917 | 0.983 | 0.983 | 0.247 | 0.794 | 3686 |
| section-1500 | 95 | 363 | 0.572 | 0.800 | 0.950 | 0.983 | 0.240 | 0.764 | 2018 |

Selected: **fixed-2000-200**, by the rule fixed in `docs/roadmap-w03.md` (highest MRR; configs within 0.02 of it are ordered by Recall@5, then by smaller chunks).

## MRR by question style

| Style | n | fixed-500-50 | fixed-1000-100 | fixed-2000-200 | paragraph-1000 | section-1500 |
|---|---|---|---|---|---|---|
| identifier | 5 | 0.740 | 0.717 | 1.000 | 0.800 | 0.850 |
| lookup | 8 | 0.792 | 0.812 | 0.844 | 0.875 | 0.938 |
| multi | 5 | 0.562 | 0.800 | 1.000 | 0.600 | 0.567 |
| paraphrase | 8 | 0.760 | 0.812 | 1.000 | 0.875 | 0.635 |
| procedure | 4 | 0.708 | 0.833 | 1.000 | 0.708 | 0.812 |

## Context header (exploratory)

The same chunks, but each is embedded together with `document title > section`.

| Config | Chunks | Mean chars | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Precision@5 | MRR | Chars read@5 |
|---|---|---|---|---|---|---|---|---|---|
| fixed-500-50 | 82 | 459 | 0.500 | 0.822 | 0.917 | 0.983 | 0.267 | 0.725 | 2346 |
| fixed-1000-100 | 44 | 845 | 0.544 | 0.917 | 0.983 | 1.000 | 0.280 | 0.797 | 4358 |
| fixed-2000-200 | 23 | 1551 | 0.800 | 0.950 | 0.983 | 1.000 | 0.260 | 0.958 | 7704 |
| paragraph-1000 | 48 | 721 | 0.550 | 0.917 | 0.983 | 0.983 | 0.247 | 0.794 | 3686 |
| section-1500 | 95 | 363 | 0.572 | 0.800 | 0.950 | 0.983 | 0.240 | 0.764 | 2018 |
| fixed-500-50+ctx | 82 | 459 | 0.511 | 0.922 | 0.950 | 0.983 | 0.287 | 0.800 | 2331 |
| fixed-1000-100+ctx | 44 | 845 | 0.683 | 0.950 | 1.000 | 1.000 | 0.287 | 0.878 | 4308 |
| fixed-2000-200+ctx | 23 | 1551 | 0.767 | 0.983 | 0.983 | 1.000 | 0.260 | 0.944 | 7698 |
| paragraph-1000+ctx | 48 | 721 | 0.617 | 0.933 | 0.983 | 0.983 | 0.247 | 0.850 | 3665 |
| section-1500+ctx | 95 | 363 | 0.694 | 0.900 | 0.950 | 0.983 | 0.240 | 0.903 | 2001 |

## Service filter (exploratory)

The selected config, with and without restricting the search to the service named in the question (what extraction would supply). Questions with no service are unrestricted in both rows.

| Config | Chunks | Mean chars | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Precision@5 | MRR | Chars read@5 |
|---|---|---|---|---|---|---|---|---|---|
| fixed-2000-200 (no filter) | 23 | 1551 | 0.800 | 0.950 | 0.983 | 1.000 | 0.260 | 0.958 | 7704 |
| fixed-2000-200 (service filter) | 23 | 1551 | 0.833 | 1.000 | 1.000 | 1.000 | 0.267 | 0.983 | 3767 |
