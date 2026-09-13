---
title: "Qualify the current complete system"
description: "Understand the independent language and gateway coverage floors and the evidence a passing gate records."
---

The current whole-system entry point is:

```sh
.venv/bin/python scripts/qualify_gateway.py
```

It qualifies the existing agent core, consumer layer, and independent gateway. Results go to `evidence/sturgis-03/`; the compatible `scripts/qualify_consumer.py` entry retains its default Sturgis-02 destination. The gateway wrapper adds the real standalone and embedded gateway browser journeys. Do not substitute the historical `scripts/qualify.py` command: that gate enforces the original core-only tree, rejects the frontend, and removes its previous Sturgis-01 success record before starting. Historical results remain historical evidence.

## What the numbers mean

Python, C++ and authored TypeScript/JavaScript each require at least 90% executable-line coverage. The gateway Python package also has its own 90% floor so existing modules cannot conceal missing gateway tests. The gate does not blend languages or round a value up to the floor. Python source includes `agent/`, `consumer/`, and `model_gateway/`, including files that a test never imported. C++ excludes vendored and compiler sources by identity. The web denominator includes all authored production client source, including both entrypoints and files never loaded during a browser journey.

Branch coverage is recorded separately. Passing a line floor does not establish that every branch, platform or vendor protocol was exercised.

For the browser, unit execution and built-browser V8 execution are matched to the same source through source maps and source hashes. The collector checks the compiled assets and implementation inputs. A stale build cannot silently qualify newer source. Coverage collectors are themselves tested against missing files, mismatched inputs and invalid reports.

## A practical qualification workflow

Run focused tests while changing one mechanism. Once those pass, run the aggregate gate against the final source. Keep the resulting report, language coverage artifacts and source manifest together. If a code or build input changes during qualification, treat the result as invalid and rerun after inputs stabilize.

Read the actual behavioral evidence as well as the percentage: real Linux processes, native memory, authenticated HTTP, consumer workers, installed wheels and browser interactions test different boundaries. A fixture can verify exact request serialization and cancellation against a local endpoint without certifying a remote commercial service.

For failures, start with [debugging](../operations/debugging.md). Do not repair a failing gate by excluding a production file, dropping difficult paths from the denominator, or replacing an actual effect test with a fabricated receipt.

## Qualification implementation

`qualify_gateway.main` selects Sturgis-03 and enables both browser journeys. `qualify_consumer.arguments` preserves the prior entry point's default output directory while accepting a separate `--output`. `python_command` includes all core, integration, consumer, consumer-integration, and gateway suites. `validate_python` independently parses executable source, checks the complete file denominator, and rejects excluded lines or inconsistent report totals. `gateway_report` repeats that validation for every gateway module before applying its separate floor. `validate_native` and `validate_web` retain the independent native and authored-client denominators.

The source snapshot includes gateway code and the test inputs. `scripts/verify_web.mjs` runs the existing consumer journey and `tests/gateway/browser_journey.mjs`, then merges their raw V8 records with component-test coverage. Gateway screenshots and `gateway-browser-quality.json` accompany the ordinary consumer browser evidence. Every successful aggregate report hashes these results and its source manifest; a changed input prevents a success record. Selecting another output directory changes where evidence is written, not which production files or coverage floors apply.

`frontend/vitest.config.ts` runs component tests in jsdom with V8 coverage. Its worker count is the smaller of two and Node's available parallelism, leaving CPU capacity for DOM execution and coverage collection on a shared host. Each test retains the 10-second deadline, and the configuration preserves test isolation, the full frontend test pattern, and an empty coverage exclusion list. The aggregate web collector still independently validates every authored production file; limiting worker concurrency does not change that denominator.

## Verify an existing qualification receipt

After the complete gate has passed, verify its retained evidence against the current checkout:

```sh
.venv/bin/python scripts/qualify_gateway.py --check
```

`verify_receipt` requires the Sturgis-03 report, its exact artifact hashes, and matching current source and built-web input manifests. It independently recalculates all four coverage floors and their executable-line denominators, compares the recorded line and branch counts, checks the raw browser/component coverage hashes, and requires a passed gateway browser result. It also checks the recorded Python version and operating system. A missing report, edited evidence, changed source, or stale web input rejects the receipt.

This mode reads the existing evidence without rerunning the test suites or browser journeys and does not create a new qualification result. Use it when closing the spec after qualification; if it fails, inspect the cause and run the complete gate again when the source is stable. For evidence written elsewhere, supply the same `--output` directory to the complete run and the receipt check. Neither mode certifies access to a remote provider account or a public deployment.

## Implementation sources

- [scripts/qualify_gateway.py](/reference/files/scripts/qualify_gateway.py/)
- [scripts/qualify_consumer.py](/reference/files/scripts/qualify_consumer.py/)
- [scripts/verify_native.py](/reference/files/scripts/verify_native.py/)
- [scripts/verify_web.mjs](/reference/files/scripts/verify_web.mjs/)
- [frontend/vitest.config.ts](/reference/files/frontend/vitest.config.ts/)
- [tests/consumer_web/coverage.mjs](/reference/files/tests/consumer_web/coverage.mjs/)
- [tests/gateway/browser_journey.mjs](/reference/files/tests/gateway/browser_journey.mjs/)
- [spec/sturgis-02/coverage.md](/reference/files/spec/sturgis-02/coverage.md/)
- [scripts/qualify.py](/reference/files/scripts/qualify.py/)

[Developer manual](../index.md)
