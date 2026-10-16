---
id: pm-2026-07-search
type: postmortem
title: "Postmortem: search results stale after repeated indexer restarts"
service: search-indexer
---

# Postmortem: search results stale after repeated indexer restarts

Date: 9 July 2026. Severity: SEV3. Duration: 5 hours. Author: Discovery team.

## Summary

New and changed products took up to five hours to appear in search because the search-indexer pods
restarted every few minutes. No orders were lost, but merchandisers launching a summer campaign
could not find their products in search.

## Timeline (UTC)

- 06:10 Memory limit alert fired for search-indexer.
- 06:30 First `OOMKilled` restart recorded. The alert was acknowledged as noise.
- 09:00 A merchandiser reported missing products.
- 09:20 The cause was traced to the memory limit and a recent change to the indexing library.
- 11:05 A fix was deployed and the consumer caught up by 11:15.

## Root cause

A library upgrade changed how product images metadata was held in memory. Each document grew from
about 120 KB to about 350 KB while a batch was being built. With a batch size of 1000 the heap
peaked just under the 2Gi limit, and any large product made it go over.

## What we decided not to do

We considered raising the memory limit to 4Gi. We rejected it because the nightly full reindex
already runs on the nodes with the least free memory, and a 4Gi limit would evict other pods.

## Action items

1. Stream image metadata instead of loading it, which brought a document back to about 130 KB.
2. Lower `INDEXER_BATCH_SIZE` to 500 until the streaming change shipped.
3. Treat three OOMKilled restarts in an hour as a ticket, not noise (owner: Discovery, done).
