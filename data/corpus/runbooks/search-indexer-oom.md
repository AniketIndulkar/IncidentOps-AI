---
id: rb-search-indexer-oom
type: runbook
title: search-indexer pods OOMKilled
service: search-indexer
---

# search-indexer pods OOMKilled

Use this runbook when search-indexer pods restart with `OOMKilled` and exit code 137. In production
this delays new products appearing in search. In staging it has no customer impact.

## What the indexer does

search-indexer reads product change events from the `catalog.changes` topic, builds documents in
memory in batches, and bulk writes them to the search cluster. Memory use grows linearly with the
batch size. The pod memory limit is 2Gi and each document costs roughly 350 KB while it is being
built, so a batch of 5000 documents needs far more than the limit.

## Diagnose

1. Confirm the kill reason: `kubectl describe pod <pod>` shows `Reason: OOMKilled` and
   `Exit Code: 137` under Last State.
2. Check the configured batch size: `kubectl get deploy search-indexer -o yaml | grep BATCH`. The
   variable is `INDEXER_BATCH_SIZE`. The safe default is 1000.
3. Check whether the restarts began after a config change. In the September incident the batch size
   had been raised to 5000 the day before.

## Fix

1. Set `INDEXER_BATCH_SIZE=1000` and roll the deployment. Restarts stop on the next rollout.
2. Do not simply raise the memory limit. The node pool has 4Gi nodes, and a 4Gi limit evicts
   neighbouring pods during the nightly full reindex.
3. After the pods are stable, the consumer will have lag. It catches up at about 2,000 documents per
   second, so a one hour outage clears in roughly ten minutes. Watch `consumer_lag` on the Kafka
   dashboard rather than restarting consumers.

## Prevention

Any change to `INDEXER_BATCH_SIZE` must be load tested in staging with the nightly reindex running.
The staging memory graph should stay below 70% of the limit for thirty minutes.
