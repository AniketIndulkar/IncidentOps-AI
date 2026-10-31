---
id: rb-checkout-502
type: runbook
title: checkout-api returns 502 Bad Gateway
service: checkout-api
---

# checkout-api returns 502 Bad Gateway

Use this runbook when the edge load balancer reports 502 responses for `POST /checkout` and the
rate is above 2% of requests for five minutes. checkout-api is owned by the Commerce Platform team.

## Symptoms

- The load balancer dashboard shows `HTTPCode_ELB_502_Count` rising while checkout-api pods still
  report Ready.
- Customers see "Something went wrong" at the payment step. Retrying sometimes works.
- The checkout-api logs contain `upstream prematurely closed connection` or
  `connection reset by peer` from the nginx sidecar.

## First checks (five minutes)

1. Open the Deploys dashboard and look for a checkout-api release in the last 60 minutes. Most 502
   incidents we have seen follow a release.
2. Run `kubectl get pods -l app=checkout-api -n commerce` and look at restart counts. More than
   three restarts in ten minutes points to a crash loop, not a gateway problem.
3. Check the pod readiness probe. checkout-api is only Ready when `/healthz/ready` returns 200 and
   the payments-gateway circuit breaker is closed.

## Likely causes and fixes

### A bad release

If a release went out in the last hour and the 502 rate started within ten minutes of it, roll back
first and investigate afterwards:

```
kubectl rollout undo deployment/checkout-api -n commerce
kubectl rollout status deployment/checkout-api -n commerce --timeout=180s
```

A rollback takes about three minutes. Do not wait for a root cause before rolling back. Announce the
rollback in #inc-commerce so nobody redeploys the bad version.

### Keepalive mismatch between nginx and the app

The nginx sidecar keeps upstream connections open for 75 seconds, while the application server
closes idle connections after 60 seconds. When the app closes a connection at the same moment nginx
reuses it, nginx returns a 502. The setting that must hold is: the application keepalive timeout
must be longer than the sidecar's `keepalive_timeout`. Set `GUNICORN_KEEPALIVE=90` and restart one
pod at a time.

### Payments circuit breaker open

When payments-gateway is slow, the circuit breaker in checkout-api opens after 20 consecutive
failures and the readiness probe fails. Pods drop out of the load balancer pool and the remaining
pods overload. Follow the payments-gateway runbook, then reset the breaker with
`POST /admin/circuit-breaker/reset` on one pod.

## Escalation

Page the Commerce Platform on-call if the 502 rate stays above 5% fifteen minutes after a rollback.
If card payments are failing for every customer, declare SEV1 and follow the general triage runbook.
