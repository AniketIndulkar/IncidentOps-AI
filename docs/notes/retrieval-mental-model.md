# Retrieval mental model (W03, AIENG-24)

What the W03 implementation assumes about retrieval, in one page. Written before any retrieval code.

## The pipeline

```
documents ─▶ chunks ─▶ embeddings ─▶ index
                                       │
question ─▶ embedding ─▶ nearest chunks ─▶ (later: rerank, cite, answer)
```

Every stage can lose the answer, and each loses it in a different way:

| Stage | How it loses the answer |
|---|---|
| Parsing | Text missing or garbled (tables, page headers repeated in every chunk) |
| Chunking | The answer is cut in half, or buried in a chunk about something else |
| Embedding | Words that matter (`PAY-1042`) carry little weight in the vector |
| Search | The right chunk is rank 12 and we only read the top 5 |
| Context | Right chunk found, but lacks the context to be understood ("it", "the service") |

Because failures are so different, measure retrieval separately from generation. A wrong answer
could be either; a missed chunk is always retrieval.

## Chunking

A chunk is the unit that gets one vector and one citation. Trade-off:

- **Small chunks** are precise (one idea per vector) but the answer may be split across two.
- **Large chunks** keep the answer whole but dilute the vector and cost more context later.
- **Overlap** repeats text at the seams so a sentence cut in half still appears whole in one chunk.
- **Structure-aware chunking** uses headings so a runbook step stays with its title. A heading
  prefix on the embedded text ("Orders pool > Fix") gives a chunk the context it would otherwise lack.

We will not guess a size. AIENG-26 compares five configurations on the same questions.

## Embeddings

An embedding maps text to a point so that texts with similar meaning are close. Consequences:

- Paraphrases work ("login loop" finds "Invalid session"), which keyword search cannot do.
- Rare exact tokens (error codes, flags, ids) work worse. Expect dense retrieval to lose there.
  Hybrid search (W04) exists for this reason.
- Vectors from different models cannot be compared. Changing the model means re-embedding everything,
  so the model name is stored with the vectors.
- Cosine similarity on normalised vectors is the standard distance. OpenAI vectors are normalised.

## Metrics we will use

For each question, the retriever returns a ranked list of chunks. A chunk is **relevant** if it
covers at least half of a gold evidence span.

- **Recall@K:** fraction of gold evidence spans found in the top K. "Did we find what we needed?"
- **Precision@K:** fraction of the top K that is relevant. "How much noise did we read?"
- **MRR:** mean of 1 / rank of the first relevant chunk. "How early did we find it?"
- **Hit@K:** at least one relevant chunk in the top K.

Recall matters most for diagnosis, because the model cannot cite what it was never shown. Precision
matters for cost and for distraction. MRR matters once a reranker or a small K is in play.

## What a "good" number means here

With 30 questions, one question is 3.3 points. Differences smaller than about 5 points are noise.
The aim is to see large effects and to know where the system fails, not to chase decimals.

## Why ground truth is a quote

A chunk id is only meaningful for one chunking. A gold passage is meaningful for all of them, so one
eval set can judge every chunking, every embedding model and later hybrid search.
