---
id: pm-2026-06-payments
type: postmortem
title: "Postmortem: card charges timing out in eu-west-1"
service: payments-gateway
---

# Postmortem: card charges timing out in eu-west-1

Date: 4 June 2026. Severity: SEV1. Duration: 26 minutes. Author: Payments team.

## Summary

The primary card acquirer for eu-west-1 stopped answering requests. For 26 minutes, charges made
through the eu-west-1 gateway waited 8 seconds and then failed with internal code PAY-1042. About
11% of European card payments failed. Other regions were not affected.

## Timeline (UTC)

- 16:44 p99 latency of `/v1/charges` in eu-west-1 rose from 400 ms to 9 seconds.
- 16:49 Page fired. The first responder suspected a gateway deploy and found none.
- 16:58 The responder noticed the failures carried the `region=eu-west-1` label only.
- 17:06 Region failed over to the secondary acquirer with `payctl region failover`.
- 17:10 Charge success rate back above 99.5%.

## Root cause

The acquirer reported a database outage on their side. Our gateway had no circuit breaker around the
acquirer call, so every charge kept waiting the full 8 seconds timeout, which also held worker
threads and made the gateway slower for healthy requests.

## What went well

- Failover was safe because every attempt carries an idempotency key. The reconciliation job found
  and settled 214 charges that were in flight, with no double charges.

## What went badly

- The first 14 minutes were spent looking for a code change. The region label was on the dashboard
  but not on the alert.
- Finance was not told about split settlement files until the next morning.

## Action items

1. Add a per-acquirer circuit breaker that opens after 10 timeouts in 30 seconds.
2. Add `region` and `acquirer` to the alert text.
3. Failover checklist now includes notifying the Finance Operations channel.
