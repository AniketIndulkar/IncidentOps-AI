---
id: rb-billing-duplicates
type: runbook
title: billing-service charges customers twice
service: billing-service
---

# billing-service charges customers twice

Use this runbook when finance or Support report that customers were charged more than once for the
same invoice, or when the duplicate-charge monitor fires. This is a money-movement incident:
involve the Finance Operations lead before you fix anything.

## Stop the bleeding first

1. Pause the renewal job so no new charges are created:
   `billingctl jobs pause annual-renewals`. Pausing is safe; the job picks up where it stopped when
   it is resumed.
2. Do not roll back the release until you know whether it changed how charges are retried.
   A rollback can resend the charges that were queued.
3. Open a SEV2 immediately. If more than 100 customers are affected, escalate to SEV1.

## Find the duplicates

Duplicates normally come from a charge being retried without the original idempotency key. Run the
query in the billing-ops repository, `sql/duplicate_charges.sql`, which lists invoices with more than
one successful charge within 24 hours. Export the invoice ids to a file; Finance needs the list.

## Fix the cause

- If the retry path builds a new idempotency key for each attempt, the payments gateway cannot tell
  the attempts apart. The key must be derived from the invoice id and the billing period, never from
  a timestamp or a random value.
- If a queue consumer processed the same message twice after a rebalance, check that the handler
  records the invoice id before it charges.

## Refunds

Only Finance Operations approves refunds. Send the invoice id list to the finance channel. Refunds
are issued in batches by `billingctl refunds create --file duplicates.csv` after a second person has
reviewed the file. Customers receive an email within one business day. Never refund by hand through
the payment provider dashboard, because it is not recorded in our ledger.
