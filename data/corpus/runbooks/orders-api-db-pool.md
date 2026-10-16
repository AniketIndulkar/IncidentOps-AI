---
id: rb-orders-db-pool
type: runbook
title: orders-api database connection pool exhausted
service: orders-api
---

# orders-api database connection pool exhausted

Use this runbook when orders-api writes time out and the logs show
`sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached`. Reads usually keep
working because they are served from the replica pool, which is sized separately.

## How the pool is configured

orders-api opens at most 15 connections to `orders-db-primary` per pod: `pool_size=5` plus
`max_overflow=10`. A request waits up to 30 seconds for a free connection (`pool_timeout=30`) before
raising the error above. With 12 pods the database can see up to 180 application connections, and
the primary allows 200 in total, so there is little headroom for other clients.

## Diagnose

1. Count connections by state on the primary:

   ```
   SELECT state, count(*) FROM pg_stat_activity
   WHERE datname = 'orders' GROUP BY state;
   ```

2. A large number of `idle in transaction` sessions means the application opened a transaction and
   did not finish it. This is the most common cause. Find the oldest ones:

   ```
   SELECT pid, now() - xact_start AS age, left(query, 80)
   FROM pg_stat_activity
   WHERE state = 'idle in transaction'
   ORDER BY xact_start LIMIT 10;
   ```

3. Check for a long-running query that holds locks. A single slow `UPDATE orders` can make every
   other writer queue behind it and hold its pool connection while it waits.

## Fix

- Terminate sessions that have been idle in transaction for more than 5 minutes with
  `SELECT pg_terminate_backend(pid)`. This frees the pool immediately and is safe: the application
  retries the transaction.
- Do not raise `pool_size` during an incident. More connections per pod hit the 200 connection limit
  on the primary and turn a write outage into a full outage. The permanent fix is to put PgBouncer
  in transaction mode in front of the primary, which the team is tracking.
- If a deploy introduced the leak, roll back with `kubectl rollout undo deployment/orders-api`.

## After the incident

Record the longest `idle in transaction` age you saw in the incident ticket. The Commerce Platform
team uses it to set the `idle_in_transaction_session_timeout` alert, currently 120 seconds.
