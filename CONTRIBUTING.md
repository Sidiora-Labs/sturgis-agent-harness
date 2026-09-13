# Contributing to Sturgis

> **ACTIVE DEVELOPMENT — NOT PRODUCTION-READY.** Sturgis is experimental software. APIs, data formats, and operating requirements can change. A focused test or successful local run does not qualify a production deployment.

Sturgis is developed by **Sidiora Labs**. Contributions should preserve its durable agent core, independent model gateway, explicit execution authority, and separate user and developer documentation. Start with the [README](README.md), [architecture](documentation/developers/src/content/docs/guides/architecture/overview.md), and [current workflow](spec/workflow.kvx).

The project uses the [Apache License 2.0](LICENSE). Retain applicable copyright and license notices, and identify the source and license of any third-party material you introduce. See [third-party notices](THIRD_PARTY_NOTICES.md).

## Propose a bounded change

For a normal bug report, include the relevant commit, operating system, steps to reproduce, expected behavior, and a sanitized error. Distinguish a missing feature from a failing documented feature. Report bugs and propose improvements through [GitHub Issues](https://github.com/Sidiora-Labs/sturgis-agent-harness/issues).

For a vulnerability, follow [SECURITY.md](SECURITY.md) instead of opening a public issue with exploit details or private data.

Agree on the affected modules and public contracts before a broad change. The [specifications](spec/) describe ownership and acceptance criteria. When several contributors work in parallel, assign disjoint source and test paths, publish interface changes early, and leave shared manifests and spec state to the coordinating maintainer. Preserve unrelated work already present in the checkout.

## Set up a development environment

Use Linux with Python 3.12, Node.js 22.22.2, Make, a C++20 compiler, CMake 3.20 or newer, and SQLite development headers. The development pins are in [.python-version](.python-version) and [.nvmrc](.nvmrc). Native coverage uses GCC/gcov and gcovr. Isolated execution also needs bubblewrap, util-linux `prlimit`, and usable Linux namespaces; a container alone does not establish those capabilities.

The [development container](.devcontainer/README.md) supplies a prepared toolchain and installs dependencies and Chromium after creation; it does not start services or run the full test suites. For a manual setup on Ubuntu or Debian, install the system build and isolation dependencies:

```sh
sudo apt-get update
sudo apt-get install build-essential cmake make pkg-config libsqlite3-dev \
  bubblewrap util-linux git curl ca-certificates
```

Install the pinned Python and Node versions using your environment's toolchain manager, including virtual-environment support for Python. The system-package command above does not select Python or Node versions. If the distribution cannot supply the required toolchain, use the development container. From the repository root, install project and browser dependencies:

```sh
make bootstrap
npx --no-install playwright install-deps chromium
make browsers
make dev
```

`bootstrap` installs the editable Python package with test/development extras, documentation requirements, and both locked npm dependency sets. It does not run tests or start services. Playwright's `install-deps` installs browser operating-system libraries on supported distributions and may require administrator privileges. `browsers` then installs the locked Chromium build for your user.

`make dev` builds native memory and the web client, then serves at `http://127.0.0.1:8787`. It uses ignored `.data/sturgis/` for application data and `.data/sturgis/client/` for CLI configuration. Override `PORT`, `HOST`, or `STURGIS_ROOT` deliberately, for example `make dev PORT=8788`. `make setup` writes that local configuration without starting a service. See the [development setup guide](documentation/developers/src/content/docs/guides/getting-started/setup.md) for the runtime boundaries.

Keep application data, model credentials, and installation secrets out of version control. Use disposable test accounts and temporary workspaces for effectful checks.

## Common developer commands

Run `make help` for the current command list. The [Makefile](Makefile) is the executable reference.

| Command                    | Purpose                                                                                    |
| -------------------------- | ------------------------------------------------------------------------------------------ |
| `make lint`                | Python and JavaScript correctness rules, plus a C++ build with warnings treated as errors. |
| `make typecheck`           | Strict TypeScript checking for the authored production frontend.                           |
| `make format-check`        | Check new/changed files and verify the exact recorded legacy formatting debt.              |
| `make format-debt`         | List the remaining legacy deviations and responsible formatters.                           |
| `make test`                | Build native memory, run the entire pytest suite, and run frontend component tests.        |
| `make test-tooling`        | Exercise the formatting helper and require at least 90% executable-line coverage for it.   |
| `make qualify`             | Run the complete system and browser qualification with independent coverage floors.        |
| `make docs`                | Build both documentation sites.                                                            |
| `make docs-check`          | Run the full documentation qualification, including examples and browser journeys.         |
| `make build`               | Build the native memory worker and production web clients.                                 |
| `make dev-web`             | Start Vite with reload; run `make dev` separately for the API.                             |
| `make dev-docs-users`      | Preview the user manual on port 4321.                                                      |
| `make dev-docs-developers` | Preview the developer manual on port 4322.                                                 |

`make test` is not the coverage qualification gate, and `make build` does not create an installable wheel. Use [packaging/build-wheel.sh](packaging/build-wheel.sh) for the platform wheel and review the [packaging requirements](packaging/README.md).

### Formatting and commit hooks

Legacy formatting debt is recorded per file with its content digest. It does not grant an exemption to newly added or changed files. Use `make format-debt` to inspect the outstanding work and apply the indicated formatter only to the paths you intend to change. Do not expand a formatting baseline merely to hide a new failure or reformat unrelated modules as part of a small fix.

Optional local hooks run the configured lint, TypeScript, and formatting checks:

```sh
.venv/bin/pre-commit install
```

The [hook configuration](.pre-commit-config.yaml) does not replace the test, system qualification, or documentation gates.

The [CI configuration and operating notes](.github/ci/README.md) define parallel quality, product qualification, and documentation jobs. Those jobs check a source revision; they do not publish a package or deploy a service. Hosted-worker qualification still requires usable Linux namespaces on the runner.

## Implement and verify

Read the contract and nearby tests before editing. Add meaningful regression coverage for changed behavior, especially scope checks, cancellation, recovery, model transport, and uncertain effects. Keep provider fixtures separate from claims about actual vendor-account access.

For example, run the gateway's focused tests with:

```sh
.venv/bin/python -m pytest tests/gateway -q
```

Use the appropriate test directory or individual test for your change. Frontend type checking and the production build are available through:

```sh
npm run typecheck:web
npm run build:web
```

Run the complete system qualification once the integrated source and build inputs are stable:

```sh
.venv/bin/python scripts/qualify_gateway.py
```

The current gate includes the core, consumer, and independent gateway. Python, native C++, and authored client JavaScript each require at least **90% executable-line coverage**; gateway Python also has its own 90% floor. Branch results are recorded separately. Do not lower these floors, omit production files, or replace a real boundary test with a fabricated success record. [Qualification guidance](documentation/developers/src/content/docs/guides/getting-started/qualification.md) explains the complete denominators and evidence limits.

Historical core-only and consumer-only receipts do not qualify newer work. Verify a retained current-system receipt with:

```sh
.venv/bin/python scripts/qualify_gateway.py --check
```

That check reads existing evidence; it does not rerun qualification. Report any failed, interrupted, or unexecuted checks explicitly.

## Maintain both documentation sites

Every user-visible feature needs a usable guide and example in the [user site](documentation/users/src/content/docs/index.md). Every changed mechanism needs source-grounded contracts and important branch behavior in the [developer site](documentation/developers/src/content/docs/index.md). Generated file and symbol references complement those explanations.

Update the applicable documentation coverage mappings after reviewing the final source. Then run:

```sh
python3 scripts/docs_gate.py full
python3 scripts/docs_gate.py verify-receipt
```

This gate builds both sites and checks completeness, rendered links and source anchors, examples, and browser journeys. It is separate from runtime qualification. A source or authored-document change can invalidate a previously passing receipt. See [documentation development](documentation/README.md) for preview commands and the closure process.

When using Codify, follow the repository's [workflow](spec/workflow.kvx): inspect the task packet, work within its declared lane, retain exact verification evidence, and close only satisfied tasks. Do not mark work complete because files exist or a partial suite passed.

## Review and submit

Explain the concrete problem, resulting behavior, validation performed, and remaining limitations. Include a small reproduction when it helps reviewers. Add an entry under **Unreleased** in [CHANGELOG.md](CHANGELOG.md) for a meaningful feature or behavior change; do not invent a release date or published version.

Inspect the complete diff and stage only intended files. For a contribution-guide-only change, for example:

```sh
git diff -- CONTRIBUTING.md
git add -- CONTRIBUTING.md
git diff --cached --check
git diff --cached
```

Use your actual reviewed paths for other changes. Do not stage the whole checkout by default. Exclude local installation data, `.env` files, keys, tokens, database contents, private conversation transcripts, and screenshots or evidence containing real user data. An ignored path or a redacted-looking filename is not proof that its contents are safe to publish.

Submit your branch as a [pull request](https://github.com/Sidiora-Labs/sturgis-agent-harness/pulls). Keep the change focused, answer review comments with source or executed evidence, and preserve other contributors' attribution. [CONTRIBUTORS.md](CONTRIBUTORS.md) describes the attribution record.
