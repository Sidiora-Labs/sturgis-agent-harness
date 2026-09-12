---
title: "Qualify the current complete system"
description: "Understand the three independent coverage floors and the evidence a passing gate records."
---

The current whole-system entry point is:

```sh
.venv/bin/python scripts/qualify_consumer.py
```

It qualifies the existing agent core together with the consumer layer. Do not substitute the historical `scripts/qualify.py` command: that gate enforces the original core-only tree, rejects the frontend, and removes its previous Sturgis-01 success record before starting. Historical results remain historical evidence.

## What the numbers mean

Python, C++ and authored TypeScript/JavaScript each require at least 90% executable-line coverage. The gate does not blend languages or round a value up to the floor. Python source includes both `agent/` and `consumer/`, including files that a test never imported. C++ excludes vendored and compiler sources by identity. The web denominator includes all authored production client source, not only files loaded during the browser journey.

Branch coverage is recorded separately. Passing a line floor does not establish that every branch, platform or vendor protocol was exercised.

For the browser, unit execution and built-browser V8 execution are matched to the same source through source maps and source hashes. The collector checks the compiled assets and implementation inputs. A stale build cannot silently qualify newer source. Coverage collectors are themselves tested against missing files, mismatched inputs and invalid reports.

## A practical qualification workflow

Run focused tests while changing one mechanism. Once those pass, run the aggregate gate against the final source. Keep the resulting report, language coverage artifacts and source manifest together. If a code or build input changes during qualification, treat the result as invalid and rerun after inputs stabilize.

Read the actual behavioral evidence as well as the percentage: real Linux processes, native memory, authenticated HTTP, consumer workers, installed wheels and browser interactions test different boundaries. A fixture can verify exact request serialization and cancellation against a local endpoint without certifying a remote commercial service.

For failures, start with [debugging](../operations/debugging.md). Do not repair a failing gate by excluding a production file, dropping difficult paths from the denominator, or replacing an actual effect test with a fabricated receipt.

## Implementation sources

- [scripts/qualify_consumer.py](/reference/files/scripts/qualify_consumer.py/)
- [scripts/verify_native.py](/reference/files/scripts/verify_native.py/)
- [scripts/verify_web.mjs](/reference/files/scripts/verify_web.mjs/)
- [tests/consumer_web/coverage.mjs](/reference/files/tests/consumer_web/coverage.mjs/)
- [spec/sturgis-02/coverage.md](/reference/files/spec/sturgis-02/coverage.md/)
- [scripts/qualify.py](/reference/files/scripts/qualify.py/)

[Developer manual](../index.md)

