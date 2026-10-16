---
id: rb-session-invalid
type: runbook
title: Users get "Invalid session" after logging in
service: session-service
---

# Users get "Invalid session" after logging in

session-service issues and validates login sessions for the web and mobile apps. Use this runbook
when customers enter correct credentials and are then sent back to the login page with
`Invalid session`.

## How sessions work

A successful login creates a session record in the regional Redis cluster and returns a signed
cookie. Every later request validates the cookie signature and then looks the session up in Redis.
Sessions live for 12 hours and are not replicated across regions, so a customer is always served by
the Redis cluster nearest to them.

## Narrowing down the cause

1. **Is it one region?** Compare the `session_validation_failures` metric per region. If only one
   region fails, the problem is that region's Redis cluster or its network path.
2. **Did a Redis failover happen?** Check the Redis dashboard for a primary change. A failover can
   lose the most recent writes, so sessions created in the last few seconds are missing.
3. **Is it clock skew?** The signed cookie carries a timestamp. If an app node's clock drifts by more
   than 60 seconds the signature check rejects fresh cookies. Compare `node_timex_offset_seconds`
   across session-service pods.

## Fixes

- For a Redis failover: sessions created before the failover are gone and users must log in once
  more. No data repair is possible. Post a banner in the status page so Support can answer quickly.
- For clock skew: restart the chrony daemon on the affected nodes with
  `systemctl restart chronyd`, then cordon nodes whose offset stays above 60 seconds.
- Do not extend the session lifetime or loosen signature checks to make the errors go away. That
  hides the symptom and weakens authentication.

## Customer impact notes

Failures concentrated in one country are a strong sign of a regional Redis or routing problem rather
than a code bug. Support tickets from Germany or France with the exact time of the first failure help
identify the affected cluster.
