---
id: pm-2026-08-session
type: postmortem
title: "Postmortem: German users logged out after Redis failover"
service: session-service
---

# Postmortem: German users logged out after Redis failover

Date: 18 August 2026. Severity: SEV2. Duration: 47 minutes. Author: Identity team.

## Summary

At 07:12 CET customers in Germany began seeing `Invalid session` right after entering correct
credentials. Support received 63 tickets. Customers in other countries were not affected.

## Timeline (CET)

- 07:03 The primary node of the eu-central Redis cluster failed its health check.
- 07:04 Automatic failover promoted a replica.
- 07:12 First customer reports. The failure rate on the login path reached 40%.
- 07:30 The responder compared validation failures per region and saw only eu-central failing.
- 07:44 A second cause was found: three session-service pods had a clock offset of about 90
  seconds, so cookies they had just signed were rejected as being from the future.
- 07:59 Affected nodes had chrony restarted and were cordoned. Failure rate returned to baseline.

## Root cause

Two independent faults overlapped. The Redis failover lost sessions written in the last seconds. The
chrony service had silently stopped on three nodes weeks earlier, and clock drift only became
visible once traffic shifted onto those nodes after the failover.

## What went well

- Comparing the metric per region quickly proved the problem was regional.

## What went badly

- The clock offset alert had a threshold of 5 minutes, far above the 60 seconds that the cookie
  check tolerates.
- Support did not know the time window to give customers.

## Action items

1. Alert on `node_timex_offset_seconds` above 30 seconds (owner: Platform, done).
2. Add a status page banner template for login issues.
3. Test Redis failover in staging every quarter, with a login check.
