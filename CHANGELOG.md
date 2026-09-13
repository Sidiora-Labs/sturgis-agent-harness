# Changelog

> **ACTIVE DEVELOPMENT — NOT PRODUCTION-READY.** These notes describe development work, not a stable release or deployment certification.

## Unreleased

No published release history is asserted here. Package version fields are build metadata and do not establish that a corresponding release was published.

### Added

- A persistent Python agent core with a durable journal, labeled context packets, scoped work admission, budgets, goals, commitments, plans, delegation, recorded tool execution, and supervised input/output paths.
- An isolated C++ memory process with temporal records, concepts, retrieval, and resumable consolidation coordinated by the Python runtime.
- Consumer accounts and private agent spaces, conversations, knowledge and workspace content, connections, procedures, schedules, workflow views, lifecycle operations, and bounded backup/restore.
- A Sturgis-branded Material 3 web client and separate authored user and developer documentation sites, including generated source references and independent documentation qualification.
- An independent Model Gateway with standalone and embedded operation, provider presets, model discovery and import, credential storage, supported native account flows, client keys, routing, protocol translation, settings, and usage records.
- Apache-2.0 project licensing and third-party notices; contribution, security, and attribution guidance; pinned developer tooling, Make commands, local hooks, explicit formatting-debt checks, GitHub workflow configuration, and a development container.

### Changed

- Provider setup uses a preset drawer and model import, followed by **Use model** in agent settings. Imported catalog entries are distinguished from verified inference readiness.
- Agent settings distinguish **Chat and tools** from **Chat only**, expose a tool-readiness retry, and retain categorized recovery messages for failed model checks.
- Runtime requests include the canonical Sturgis identity and Sidiora Labs attribution separately from underlying model provenance; changing providers does not change the product identity.

### Fixed

- OpenRouter-compatible streaming accepts a narrowly validated, empty terminal usage envelope without losing final accounting or accepting new output after completion.
- Request validation and imported model output allowances use consistent limits through activation and ordinary conversation requests.
- Tool readiness uses the canonical invocation schema and a bounded tool-response allowance. Slow checks renew their original scheduler lease, join cancellation cleanup, and retain factual usage before settlement.
- Rechecking an acknowledged model binding can start a fresh capability check while uncertain requests retain their existing identity for reconciliation.
- Completed daily memory consolidation no longer creates a redundant scheduler job on every idle poll. Active-work queries avoid decoding completed historical work as candidates.
- Conversation replay filters scoped public events and the selected binding before pagination, preserves snapshot cursors and terminal-event routing, and shows loading or reconnection state while retaining loaded messages.

### Development status and known limits

- Full qualification of the current combined checkout remains incomplete. Earlier receipts and focused or live checks apply only to the exact source and behavior they recorded; subsequent fixes and repository changes require renewed qualification.
- Hosted operation depends on Linux namespace and resource controls. Managed browser availability in hosted workers remains constrained by the host's nested-isolation support; the documented qualification host does not support that path.
- Provider access, model availability, account entitlements, and protocol compatibility remain provider-dependent. Local fixtures and a successful request through one account do not certify every vendor or model.
- Content decoding and context inclusion are bounded. Bundled source extraction does not provide general PDF, office-document, or OCR support.
- APIs, persistence formats, and migration requirements may change during development. See the [current limits](documentation/users/src/content/docs/reference/limits.md), [qualification guidance](documentation/developers/src/content/docs/guides/getting-started/qualification.md), and [security policy](SECURITY.md).
