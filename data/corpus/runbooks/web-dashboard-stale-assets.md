---
id: rb-dashboard-assets
type: runbook
title: web-dashboard shows stale or wrong content
service: web-dashboard
---

# web-dashboard shows stale or wrong content

Use this runbook when users of the customer dashboard see old content after a release, such as an
outdated footer, a missing feature or a layout that is half old and half new. These issues are
almost always SEV4 unless they hide a payment or login control.

## Why it happens

web-dashboard is a static single page app served from object storage behind a CDN. Asset file names
contain a content hash, but `index.html` does not. The CDN caches `index.html` for 10 minutes by
default. After a release, some users get the new `index.html` that points at new assets while others
still hold the old one, and an old `index.html` can request assets that were already deleted.

## Fix

1. Purge the HTML entry points only; purging the whole distribution makes every asset cold and
   raises origin load:
   `cdnctl purge --distribution web-dashboard --paths "/index.html" "/"`.
2. Wait two minutes and check the response header `x-cache: Miss` followed by `Hit` on a second
   request.
3. Ask the user to hard refresh. Browsers may keep `index.html` for up to the CDN max-age.

## Hardcoded content

If the wrong content is in the build itself, for example the copyright year in the footer being
2025 in a 2026 release, purging will not help. Fix the source and ship a new release. The footer
year is generated at build time from `new Date().getFullYear()`, so a stale year means the release
artifact was built before the new year and promoted unchanged.

## Severity

Cosmetic problems with a workaround are SEV4 under the general triage runbook. Do not page anyone for
them outside working hours.
