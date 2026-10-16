---
id: rb-general-triage
type: runbook
title: General incident triage and severity levels
service:
---

# General incident triage and severity levels

This runbook applies to every service. The first responder owns the incident until they hand it to
an incident commander.

## Severity levels

| Level | Definition | Response |
|-------|------------|----------|
| SEV1 | Customers cannot pay, log in, or use the core product, or data is lost or exposed | Page immediately, all hands, update every 15 minutes |
| SEV2 | A major feature is degraded or a large group of customers is affected | Page the on-call, update every 30 minutes |
| SEV3 | Minor degradation, workaround exists | Fix in working hours, update daily |
| SEV4 | Cosmetic issue or request with no customer impact | Ticket only |

When in doubt, pick the higher level. Lowering a severity later costs nothing. Raising it late costs
trust.

## First ten minutes

1. Acknowledge the page and say so in the incident channel. Silence is the most common failure.
2. Write down the time the problem started, not the time you noticed it. Use the earliest customer
   report or the first alert.
3. Check for recent changes: deploys, feature flag changes, configuration changes, certificate
   expiries and cloud provider status.
4. Decide whether to roll back. If a release shipped shortly before the problem began, rolling back is
   the default. A rollback you did not need costs minutes. A rollback you delayed can cost the day.
5. Assign roles: one person fixes, one person communicates. Never combine them in a SEV1.

## Communication

- Post a customer-facing status update within 20 minutes of declaring SEV1 or SEV2.
- State what customers see, not internal causes. Do not promise a time to fix.
- The incident commander approves anything that goes to the status page.

## Closing

Every SEV1 and SEV2 gets a blameless postmortem within five working days. The postmortem lists the
timeline, the root cause, what went well, what went badly and dated action items with owners.
