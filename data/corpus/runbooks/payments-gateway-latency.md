---
id: rb-payments-latency
type: runbook
title: payments-gateway latency and charge failures
service: payments-gateway
---

# payments-gateway latency and charge failures

payments-gateway talks to two card acquirers and to Stripe. Use this runbook when `/v1/charges`
latency or error rate alerts fire.

## Alert thresholds

- Page: p99 latency of `/v1/charges` above 3 seconds for five minutes.
- Page: more than 1% of charges returning HTTP 500 or 504 for five minutes.
- Ticket only: p99 between 1.5 and 3 seconds.

## Internal error codes

The gateway logs an internal code with every failed charge:

| Code | Meaning | Action |
|------|---------|--------|
| PAY-1001 | Card declined by issuer | None, normal |
| PAY-1017 | Idempotency key reused with different body | Client bug, ticket only |
| PAY-1042 | Acquirer timeout after 8 seconds | Fail over the region, see below |
| PAY-1050 | Stripe API unavailable | Check status.stripe.com, enable the fallback acquirer |

## PAY-1042 acquirer timeouts

A spike of PAY-1042 together with `StripeConnectionError` and a p99 near 9 seconds means the primary
acquirer in one region has stopped answering. The gateway waits 8 seconds before giving up, which is
why p99 jumps from around 400 ms to about 9 s.

1. Check whether only one region is affected using the `region` label on the charge latency panel.
2. Fail the affected region over to the secondary acquirer:
   `payctl region failover --region eu-west-1 --to secondary`.
3. Failover is safe for new charges. In-flight charges are resolved by the reconciliation job, which
   runs every 5 minutes and never double charges because every attempt carries an idempotency key.
4. Tell the Finance Operations channel, because settlement files will arrive from two acquirers for
   the day.

## Stripe unavailable (PAY-1050)

If status.stripe.com shows an incident, run `payctl fallback enable stripe`. The fallback routes
card-not-present payments to the secondary acquirer. It must be disabled again by hand when Stripe
recovers, or fees stay higher than normal.

## Escalation

If more than 5% of charges fail for ten minutes, declare SEV1. Customers cannot pay, which is the
definition of SEV1 for commerce services in the general triage runbook.
