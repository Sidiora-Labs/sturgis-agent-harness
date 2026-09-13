# Repository foundations validation

This report covers repository documentation, licensing, contributor tooling,
CI definitions, development-container configuration and wheel completeness.
Sturgis remains under active development and unfit for production use.

## Executed checks

- `make bootstrap` completed against the actual checkout.
- `make lint typecheck format-check test-tooling` passed, including native
  compilation with warnings treated as errors. The four tooling tests cover
  86 of 93 executable statements (92.47%). Combined line-and-branch coverage
  is separately reported as 89.93%; it is not the executable-line metric.
- Formatting inspected 399 first-party files. There are 291 unchanged legacy
  deviations, bounded by exact content and configuration hashes. New or changed
  violations fail. The entire tree is not represented as fully formatted.
- Root documentation links, referenced Make targets, shell/configuration syntax,
  Apache license bytes and package metadata were checked.
- A real wheel was built and installed outside the source checkout. It contains
  the agent, consumer and independent gateway packages, native memory executable,
  client assets, license and notices. Both CLI entrypoints were inspected.
- The six existing application tests passed after capturing the serialized
  startup exception before returning its response generator.

Local reports are retained in ignored `.build/`: `repository-foundations-done.log`,
`repository-tooling-coverage.json`, `root-bootstrap.log`,
`repository-application-lint-fix.log` and `development-wheel/inspection.json`.
These are local development evidence, not a release certification.

## Formatter checker contract

[`scripts/check_format.py`](../../scripts/check_format.py) inventories authored
Python, web, documentation and native sources; it excludes generated output,
vendored code, dependencies and symlinks. Its six functions have distinct roles:

- `run` executes a formatter from the repository root, captures diagnostics and
  rejects an unexpected exit code.
- `inventory` returns sorted relative source paths using the explicit roots,
  suffixes and generated/vendor exclusions.
- `digest` hashes exact file bytes with SHA-256.
- `policy` binds the baseline to the formatter versions and relevant configuration
  hashes, including the parsed Ruff configuration.
- `violations` checks the installed formatter versions, reads Ruff JSON,
  Prettier path output and clang-format XML, and rejects invalid source instead
  of treating parser failures as ordinary formatting debt.
- `main` accepts `--show-debt` and `--record-debt`. Recording is allowed only when
  no baseline exists. Normal checks preserve source and baseline bytes. An exact
  formatter/content hash match permits unchanged historical debt; a new or
  modified violation exits with status 1. Policy changes require explicit review.
  The script entrypoint reports filesystem, validation and XML failures as
  nonzero exits. Argparse rejects unsupported arguments.

## Tooling regression contracts

[`tests/test_repository_tooling.py`](../../tests/test_repository_tooling.py) invokes
the actual installed formatters against temporary repositories:

- `repository` copies real configuration into a temporary root, links installed
  Node dependencies, and redirects the checker root and baseline for isolation.
- `invoke` supplies CLI arguments and calls `main`, preserving exceptions for
  assertions.
- `test_actual_formatters_enforce_modified_and_new_files_without_rewriting`
  exercises Python, JavaScript and C++ debt; checks the printed debt listing and
  unchanged bytes; rejects changed historical and new violations; and accepts
  a newly formatted file.
- `test_baseline_cannot_be_replaced_and_config_changes_require_explicit_review`
  rejects repeated baseline recording and changed formatter configuration while
  preserving baseline bytes.
- `test_invalid_python_cannot_be_recorded_as_formatting_debt` rejects malformed
  Python and verifies that no baseline is created.
- `test_inventory_includes_tests_manuals_research_and_hidden_tooling_but_not_generated_or_vendor`
  verifies authored source inclusion, explicit generated/vendor exclusions and
  exclusion of symlinked source.

The Playwright test change resolves the installed browser through the locked
package rather than a particular user's cache path. It does not skip browser
tests or relax the namespace isolation preflight.

## Scope and unexecuted qualification

The CI definitions were validated locally; no remote GitHub Actions run is
claimed. Development-container configuration and image references were checked;
a complete container build and launch were not performed. No deployment,
publication, production approval or full runtime qualification occurred here.

The user stopped the earlier runtime repair and full two-site documentation
qualification. That work remains incomplete in `sturgis-03`. The local Codify
snapshot preserves those existing changes alongside repository maintenance, so
its derived documentation packet also references their symbols. Existing source
reference mappings retain traceability; they do not qualify that stopped work.
In particular, the conversation replay status limitation remains documented in
the [event guide](../../documentation/developers/src/content/docs/guides/frontend/data-events.md).

The following packet entries belong to preserved conversation work, not new
repository behavior. Their documentation and runtime qualification remain owned
by `sturgis-03`; their names are recorded here as explicit scope exclusions:

- `Replay` in `frontend/src/hooks/useEvents.ts`.
- `app`, `maintenance`, `turn`, `emitted`, `decode`, `append`,
  `test_public_page_filters_backlog_before_limit_and_resolves_terminal_scope`,
  `test_exact_page_boundary_empty_scope_and_future_cursor_do_not_regress`,
  `test_concurrent_append_is_observed_on_following_snapshot_without_loss`,
  `test_imported_history_keeps_source_and_terminal_binding_after_reopen`,
  `test_saved_room_order_uses_creation_time_not_random_ids`, and
  `test_routed_page_rejects_unbounded_or_ambiguous_arguments` in
  `tests/consumer/test_conversation_history.py`.

The README research and its source links are in
[`.research/readme-writing.md`](../../.research/readme-writing.md). Contributor
setup, formatting policy and root commands are documented in
[CONTRIBUTING.md](../../CONTRIBUTING.md); changes and migration cautions are in
[CHANGELOG.md](../../CHANGELOG.md).
