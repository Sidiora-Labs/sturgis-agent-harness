---
title: "Make a change without splitting authority"
description: "Use ownership boundaries, narrow changes and evidence-oriented tests."
---

Begin by identifying the record owner and the public boundary affected by your change. A new schedule option belongs to scheduler validation and occurrence calculation. A new screen belongs to the frontend projection and existing consumer command boundary. A new human approval rule belongs to the permissions wrapper, while scheduler grants remain responsible for execution allocation.

For an additional command, follow this sequence:

1. Implement the operation on its existing domain service.
2. Register an explicit method name and allowed/required parameters in `AgentApplication`.
3. Add it to the public HTTP command allowlist only if it is intended for authenticated owners.
4. Have the client send a stable identity for a mutation and the current revision for a compare-and-swap update.
5. Exercise success, stale authority and an ambiguous acknowledgment where effects are possible.

Do not expose generic attribute lookup, arbitrary journal writes, or a client-selected filesystem root. Those shortcuts move authority into request data and bypass the reasons for the original boundary.

## Transaction discipline

A domain update and its causal event should commit together. Use the shared `Journal.transaction()`; methods may nest through savepoints. Never await while holding its synchronous transaction. For asynchronous services, move a short complete synchronous boundary to `asyncio.to_thread`, then await the actual external operation after the transaction has closed.

An irreversible or uncertain effect requires an intent record and current execution authorization before it begins. A failed schema check is different from a command that reached a remote service and lost its response. Preserve that distinction in both code and tests.

## Review evidence

Prefer assertions about observable invariants: another owner cannot resolve the agent; a stale revision changes nothing; a cancelled process has actually stopped; a lost acknowledgment does not repeat an effect. A test that only reconstructs the implementation's dictionary is weak evidence.

Keep implementation, qualification and deployment claims separate. The current executable plan and task records live in the specification. The [qualification guide](qualification.md) explains the independent language floors; [release practice](../operations/release-migration.md) explains why source changes after a gate require fresh qualification.

## Developer checks and formatting policy

The root `Makefile` exposes dependency setup, lint, strict frontend type checking, focused development entry points, both documentation builds, and the existing complete qualification gates. `make test` runs pytest and frontend component tests; `make qualify` retains the separate 90% language and gateway coverage floors. Building native memory and web assets with `make build` does not create a platform wheel or qualify the application.

`package.json` provides the matching npm build, development, lint, TypeScript, and component-test entry points. `pyproject.toml` pins the Python development/build tools and records the project's Alpha status, Sidiora Labs attribution, and Apache-2.0 metadata. These fields describe the package; they do not announce a published release. `packaging/build-wheel.sh` stages the core, consumer, and independent gateway packages together with the README and root license/notice files before building the platform wheel. Third-party notices retain their separate purpose.

`scripts/check_format.py` checks authored Python with pinned Ruff, supported web/configuration/documentation files with pinned Prettier, and native source with pinned clang-format. `inventory` discovers inputs independently, excluding declared dependencies, generated outputs, vendored code, and runtime data. It does not treat a previously generated file list as the scope authority. `violations` validates formatter versions and reads their diagnostics without rewriting production files. Formatter execution or parse errors fail the check.

`policy` binds the formatter versions and configuration digests, including the Ruff configuration inside `pyproject.toml`. `digest` binds a file's exact bytes. `.format-debt.json` records only the initially accepted legacy formatting deviations, together with their formatter and digest. `main` accepts a current deviation only if that complete record still matches; a newly added or modified unformatted file fails. Formatting a legacy file removes it from the current debt report. Baseline presence is not a claim that the legacy tree is fully formatted.

For example, inspect outstanding debt before changing a file:

```sh
make format-debt
make format-check
```

Use the reported formatter on the intended changed paths, then rerun the check. `--record-debt` initializes the baseline only when none exists and refuses to replace an existing baseline. A formatter-version or configuration change also requires explicit policy review; it cannot silently bless changed source. The script's `run` helper executes argument arrays and accepts only the formatter's declared exit codes.

These development checks complement behavioral tests and the documentation gate. Their success does not establish runtime qualification or production readiness. The root contribution guide, development-container guide, and CI notes describe installation and runner prerequisites; missing Linux isolation remains an environmental failure rather than an exemption from execution tests.

`make test-tooling` runs the formatting helper's regression suite with an independent 90% executable-line coverage floor. Its isolated report is `.build/repository-tooling-coverage.json`; branch coverage is recorded alongside the enforced line result. This helper check remains separate from the product and documentation qualification gates.

## Implementation sources

- `spec/sturgis-02/spec.kvx` (live task state in the checkout; see the [published workflow](/reference/files/spec/workflow.kvx/))
- [spec/sturgis-02/execution.md](/reference/files/spec/sturgis-02/execution.md/)
- [agent/state/**init**.py](/reference/files/agent/state/__init__.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)
- [Makefile](/reference/files/Makefile/)
- [scripts/check_format.py](/reference/files/scripts/check_format.py/)
- [pyproject.toml](/reference/files/pyproject.toml/)
- [package.json](/reference/files/package.json/)
- [packaging/build-wheel.sh](/reference/files/packaging/build-wheel.sh/)
- [.format-debt.json](/reference/files/.format-debt.json/)

[Developer manual](../index.md)
