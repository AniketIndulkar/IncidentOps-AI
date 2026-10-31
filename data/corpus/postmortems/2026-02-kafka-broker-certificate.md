---
id: pm-2026-02-kafka
type: postmortem
title: "Postmortem: notifications stopped when the Kafka broker certificate expired"
service: notification-worker
---

# Postmortem: notifications stopped when the Kafka broker certificate expired

Date: 3 February 2026. Severity: SEV2. Duration: 2 hours 10 minutes. Author: Messaging team.

## Summary

No email, SMS or push notifications were sent for 130 minutes, including password reset emails.
Customers who were locked out contacted Support, which handled 210 contacts.

## Timeline (UTC)

- 03:20 The TLS certificate of the Kafka brokers expired.
- 03:21 notification-worker began logging `NoBrokersAvailable`.
- 05:05 An engineer started the day, saw the alert, and checked broker health. Brokers looked fine.
- 05:12 DNS and network policy were checked, and ruled out.
- 05:30 `openssl s_client` showed the expired certificate. The certificate was renewed and brokers
  rolled one at a time.

## Root cause

The broker certificate was issued manually two years earlier and was not managed by cert-manager, so
it did not renew itself. The alert for certificate expiry only covered certificates in Kubernetes,
not those on virtual machines.

## Why it took so long

The client error `NoBrokersAvailable` does not mention TLS. Responders spent almost two hours
looking at broker health because the error suggests the cluster is down.

## What we learned

- A client that cannot connect can hide the actual reason. Check the certificate end date early.
- Messages older than four hours were dropped to the dead letter topic. Support replayed 1,640 of
  them with a 24 hour replay window.

## Action items

1. Move the broker certificate to cert-manager and alert 21 days before any expiry.
2. Add the certificate end date check to the notification-worker runbook (done).
3. Page after 10 minutes of NoBrokersAvailable instead of 60.
