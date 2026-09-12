# Test coverage and qualification contract

The user requires 90% test coverage. This spec defines an enforceable floor of **90.00% executable line coverage for `agent/` and independently 90.00% for production sources in `native/memory/`**. One language cannot conceal a deficit in the other. Python branch coverage is enabled and reported separately; C++ branch coverage is reported where the compiler supports it. Neither branch reporting nor passing coverage replaces behavioral acceptance.

## Measurement

- Python: pytest + coverage.py, `source = ["agent"]`, branch measurement enabled. Python worker entry contracts are exercised directly and through isolated workers; coverage counts instrumented executions only. Generate JSON/XML and readable reports. Collect unimported modules as uncovered. The gate checks actual covered/total executable lines, not rounded display values.
- C++: GCC/gcov instrumentation with an unoptimized coverage build, pytest-driven real process protocol tests, and gcovr JSON/XML reports. Include only first-party production `.cpp`/`.hpp` files under `native/memory`; external SQLite/JSON headers, generated compiler code and test harness sources are outside that denominator. Missing instrumentation/report files fail the gate.
- Dependency versions are isolated in `.venv` and declared in `pyproject.toml`. Generated coverage/build artifacts live under ignored `.build/` and `evidence/sturgis-01/` as appropriate. Source and run metadata are recorded with final evidence.
- There are no blanket pragma exclusions, untested-package omit lists, or code deletion to inflate percentages. Any intrinsically unreachable defensive branch must remain visible in source/review; no such exemption is preauthorized for line coverage.

## Required test families

| Family | Real observations required |
| --- | --- |
| Contracts | Malformed/duplicate/unknown fields, incompatible versions, invalid enums, complete versus partial framing, oversized/truncated input. |
| Persistence | Revision races, transactional rollback, deduplication content conflict, scope isolation, cursors, reopen/restart. |
| Tools/plugins | Rooted file operations and subprocesses, preimage checks, environment/output/time limits, cancellation, plugin lifecycle/state migration and failure quarantine. |
| Memory | Actual compiled worker, persisted temporal revisions, lexical/vector scores, embedding-space mismatch, retractions, generation conflicts, leases and restart. |
| Scheduling | Shared budgets, stale authority, ancestor revocation, duplicate wakes, source-trigger waits, calendar/interval scheduling and lease recovery. |
| Runtime | User input while tool/memory is stalled, persisted partial/final delivery, fixed context snapshots, steering/cancellation, restart and indeterminate effect recovery. |
| Evolution | Fixed evaluator, bounded candidate roots, exact hashes, isolated execution, canary/promotion, rejected incompatibility and rollback preserving history. |

`scripts/qualify.py` is the final gate. It runs scoped-source checks, native build/verification with coverage, then the Python suite with coverage, and reports the two independent percentages. A below-floor result, missing test collection, missing expected module, omitted production source, or failed behavior test is a nonzero exit. `docs/qualification.md` records actual commands, results, measured percentages and remaining unsupported backends; it must never claim live provider certification from a loopback test server.
