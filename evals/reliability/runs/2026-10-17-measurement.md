# How the first W12 bottleneck finding was wrong (17 Oct 2026)

The first load runs drove every responder from one Python client process. At 100 responders,
creating an incident took 400 ms at the median (9 ms at 1 responder), and I concluded the database
connection pools were the system's first bottleneck. These checks refuted that.

## 1. Pool sizes made no difference (same single-process client)

| Pools (database pool + overflow, checkpointer) | create P50 / P95 at 50 | at 100 |
|---|---|---|
| 5 + 10, 5 (defaults) | 229 / 479 ms | 124 / 600 ms |
| 10 + 20, 10 | 204 / 409 ms | 118 / 592 ms |

## 2. A burst that touches no database was slow too

`python -m incidentops_ai.reliability.burst` ([results](2026-10-17-burst.md)): 100 simultaneous
`GET /health` took 79 ms at the median and 140 ms at P95, with one server worker or two. So most
of the burst latency was not the database, and not the server's worker count.

## 3. The client process was the bottleneck

The same server, the same 100 concurrent `GET /health`, sent from a different number of client
processes (two repetitions):

| Client processes × requests each | P50 | P95 |
|---|---|---|
| 1 × 100 | 422 / 442 ms | 729 / 740 ms |
| 2 × 50 | 121 / 129 ms | 210 / 207 ms |
| 4 × 25 | 45 / 49 ms | 69 / 76 ms |

One client process cannot drive 100 concurrent requests without queueing them itself. The load
runner now splits responders across client processes of at most 25.

## 4. With the fixed runner, pools are a minor factor

100 responders, at most 25 per client process, two repetitions:

| Pools | Time outside the steps (mean per run) | create P95 | triage P95 |
|---|---|---|---|
| 5 + 10, 5 (defaults) | 391 / 423 ms | 269 / 251 ms | 9.2 / 8.9 s |
| 20 + 20, 20 | 362 / 357 ms | 150 / 172 ms | 9.7 / 9.0 s |

Larger pools take 10 to 15% off the time a run spends outside its steps and about a third off
create P95 at 100 concurrent runs; triage latency does not move, because the model is 95% of it.
The defaults stay (every connection costs the database); the sizes are settings now.

**What I take from it:** measure the measuring tool. A load generator on the same machine is part
of the system under test until shown otherwise.
