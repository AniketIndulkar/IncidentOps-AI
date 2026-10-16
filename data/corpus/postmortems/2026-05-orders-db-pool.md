---
id: pm-2026-05-orders-pool
type: postmortem
title: "Postmortem: orders-api writes failing, connection pool exhausted"
service: orders-api
---

# Postmortem: orders-api writes failing, connection pool exhausted

Date: 21 May 2026. Severity: SEV1. Duration: 1 hour 47 minutes. Author: Commerce Platform.

## Summary

For 107 minutes almost every write to orders-api failed with a connection pool timeout. Customers
could browse and view past orders but could not place new orders. An estimated 6,800 orders were
delayed and 1,900 were never placed.

## Timeline (UTC)

- 09:38 A new release of orders-api started rolling out.
- 09:41 Order creation latency rose; write errors began.
- 09:52 Page fired on the order create success rate.
- 10:05 First responder raised `pool_size` from 5 to 20 on all pods.
- 10:12 The primary database hit its limit of 200 connections and reads started to fail as well.
- 10:30 Pool size reverted. Responders began terminating idle sessions.
- 11:25 Service recovered after the release was rolled back.

## Root cause

The release added an audit write inside a database transaction but returned early on a validation
error without closing the transaction. Each failed request left a connection `idle in transaction`
until the pool was empty. Reads were unaffected at first because they use the replica pool.

## What went badly

- Raising the pool size made things worse. 12 pods times 30 connections is 360, above the primary's
  limit of 200. The runbook said not to do this, but the on-call engineer had not read it.
- It took 70 minutes to find that connections were idle in transaction because nobody looked at
  `pg_stat_activity` before changing configuration.

## Action items

1. Set `idle_in_transaction_session_timeout` to 120 seconds on the orders database (done).
2. Add a lint rule rejecting early returns inside open transactions.
3. Deploy PgBouncer in transaction mode in front of the primary (owner: Database Reliability, Q3).
4. Link the runbook from the alert, so responders see "do not raise pool_size" in the page itself.
