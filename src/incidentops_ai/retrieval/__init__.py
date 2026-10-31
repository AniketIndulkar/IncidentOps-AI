"""Retrieval: corpus documents, chunking, embeddings, retrievers and their evaluation (W03).

Pipeline, and the module for each step:

    documents.py   parse corpus files into `Document`s (cleaned text, pages, headings)
    chunking.py    cut documents into `Chunk`s, each an exact slice of the document text
    ingest.py      store documents and chunks in PostgreSQL (orm.py holds the tables)
    embeddings.py  turn text into vectors (OpenAI, an offline fake, a disk cache)
    index.py       embed the stored chunks (store.index_chunks does the work)
    retriever.py   the `Retriever` interface and `RetrievalFilter`
    store.py       `PgVectorRetriever`: filtered cosine search in SQL
    memory.py      `InMemoryDenseRetriever`: the same search in numpy, used by experiments
    evalset.py     eval questions whose ground truth is an exact quote
    metrics.py     Recall, Precision, Hit and MRR against those quotes
    evaluate.py    the scoring loop shared by experiments and benchmarks
    experiment.py, chunking_experiment.py   compare chunkers and pick one by a fixed rule
    benchmark.py   score a retriever and write a report

Conventions used throughout:

- Offsets: every position is a character offset into `Document.text`, and ranges are
  half-open `[start, end)` like Python slices. Chunks, evidence spans and citations all share
  this coordinate system, which is what lets one eval set judge any chunker.
- Keyword-only parameters: in `def f(a, *, b)`, the bare `*` means everything after it must be
  passed by name (`f(1, b=2)`). It is used for options so calls stay readable and arguments
  cannot be swapped by position.
- `@dataclass(frozen=True, slots=True)`: immutable value objects (assigning a field raises) with
  no per-instance `__dict__`. Data passed between steps cannot be changed along the way.
- `typing.Protocol`: interfaces checked by shape, not inheritance. `Retriever`, `Embedder` and
  `Chunker` are protocols; implementations do not subclass them.
- Transactions: functions that take a session flush but never commit; the caller decides, so a
  failure part way leaves the database as it was.
"""
