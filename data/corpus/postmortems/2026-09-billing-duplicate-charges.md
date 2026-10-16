---
id: pm-2026-09-billing
type: postmortem
title: "Postmortem: annual subscribers charged twice"
service: billing-service
---

# Postmortem: annual subscribers charged twice

Date: 11 to 12 September 2026. Severity: SEV1. Duration: 6 hours 20 minutes. Author: Billing team.

## Summary

After the release of billing-service 5.8.0 on 11 September at 22:00 UTC, 312 annual subscribers were
charged twice for their renewal. The total over-charged amount was 38,400 GBP. All customers were
refunded within two business days.

## Timeline (UTC)

- 22:00 Release 5.8.0 deployed, which moved the renewal retry logic into a new helper.
- 22:40 The renewal job started its nightly run.
- 02:15 The duplicate-charge monitor fired (the monitor runs only every 4 hours).
- 02:40 On-call paused the renewal job.
- 04:20 The root cause was found in the new retry helper.
- 04:30 A hot fix was deployed. Finance began refunds in the morning.

## Root cause

The new retry helper generated a fresh idempotency key for every attempt, built from the current
timestamp. When the first attempt timed out after the charge had succeeded, the retry created a
second charge, because the payments gateway saw a different key. The old code derived the key from
the invoice id and billing period.

## What went well

- Pausing the job stopped further damage within 25 minutes of detection.
- Finance and Support agreed a single customer message quickly.

## What went badly

- Detection took more than four hours. A monitor every 4 hours is too slow for charges.
- There was no test that the same invoice yields the same key on retry.

## Action items

1. Derive the key from invoice id and period, and add a test that retries reuse it.
2. Run the duplicate-charge monitor every 5 minutes.
3. Require Finance sign-off for any change to retry logic in billing code.
