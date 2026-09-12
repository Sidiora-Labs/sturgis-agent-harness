# Sturgis

Sturgis is a persistent Python agent with durable operational state, an isolated C++ memory process, and a consumer interface for self-hosted and hosted installations. Its Gemini-inspired Material 3 web client has Sturgis branding, accessible controls, a continuous conversation canvas and private spaces for work, knowledge, files and connections.

The current consumer specification is [sturgis-02](spec/sturgis-02/spec.kvx). Start with the [consumer guide](docs/sturgis-02/consumer.md), [installation and deployment guide](docs/sturgis-02/deployment.md), [HTTP contract](spec/sturgis-02/http-contract.md) and [consumer qualification report](docs/sturgis-02/qualification.md). Python serves the built React client; Node is needed only during the build.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]' setuptools wheel
npm ci
bash packaging/build-wheel.sh .build/release
.venv/bin/python -m pip install --force-reinstall --no-deps .build/release/sturgis_core-*.whl
.venv/bin/sturgis setup
.venv/bin/sturgis serve
```

Open `http://127.0.0.1:8787`, use the private installation token for first-owner setup, create an agent, then configure and probe your model. The build also produces a platform wheel containing the native executable and web assets for installation outside the checkout. Hosted operation uses the same account and command boundaries with private Linux namespace workers; see the deployment guide for resource, network and browser limits.

Run `.venv/bin/python scripts/qualify_consumer.py` for the complete native, Python and production-browser gate. Each language must independently meet 90% executable line coverage against a complete source denominator. Component tests complement real worker/database/memory/file/browser journeys. Qualification records source hashes and distinguishes locally exercised adapters from vendor or public deployment certification.

## Documentation sites

Sturgis has **two complete documentation sites**. The [site build and reading instructions](documentation/README.md) explain how to open them.

- **User site:** `documentation/users/` → `documentation/dist/users/`, served at `http://127.0.0.1:4321`. Learning paths, feature manuals, worked examples, CLI reference and troubleshooting.
- **Developer site:** `documentation/developers/` → `documentation/dist/developers/`, served at `http://127.0.0.1:4322`. Architecture, integration and operation guides plus every first-party file, qualified function and numbered source line.

Run `python3 scripts/docs_gate.py full` to generate, build and qualify both sites. Documentation is a required part of the [spec workflow](spec/workflow.kvx); the older guides below are retained as historical/supporting material.

## Core architecture

The executable plan is [sturgis-01](spec/sturgis-01/spec.kvx). Read its [design](spec/sturgis-01/design.body.md), [parallel execution plan](spec/sturgis-01/execution.md), [shared contracts](spec/sturgis-01/contracts.md) and [coverage policy](spec/sturgis-01/coverage.md).

Use the [core developer guide](docs/core.md) for Python composition, ACP/channel connections, capabilities, scoped delegation, schedules and native memory. The [sturgis-01 qualification record](docs/qualification.md) preserves the historical core-only gate and backend limits. Use the current `scripts/qualify_consumer.py` command above for this checkout; it requalifies the core together with the consumer layer and preserves the historical record.

Linux namespace support, bubblewrap, `prlimit`, CMake, GCC/gcov and SQLite development headers are required for the concrete execution backend. The current gate checks complete source denominators and records hashes of the qualified source.

The design record includes the [architecture schematics](.research/agent-architecture-v0.2.md), [module map](.research/agent-module-map-v0.2.md), [interface contracts](.research/agent-interface-contracts-v0.2.md), [visual module map](.research/diagrams/v0.2/module-map.svg) and [agent-memory research](.research/agent-memory-systems-2026-09-12.md).
