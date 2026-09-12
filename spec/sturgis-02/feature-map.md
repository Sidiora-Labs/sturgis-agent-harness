# Consumer feature disposition

All audited families are accounted for. These are the sturgis-02 implementation boundaries, informed by the [three-project audit](../../.research/sturgis-02-reference-synthesis.md).

| Family | This spec | Explicit extension boundary |
| --- | --- | --- |
| Setup/providers | Owner bootstrap, accounts/sessions, agents, BYOK endpoint/model setup and real readiness | Branded vendor OAuth flows and managed paid subscriptions |
| Conversations | Persistent conversation metadata, history/search, input queue, streaming replay, stop/steer, attachments | Native desktop/mobile app packages |
| Work/responsibility | Existing goal/commitment/planner/child evidence exposed as consumer commands/views | A separate workforce product or another scheduler |
| Inbox/attention | Proposals, reviewed decisions, actionable work states and configured in-app attention | External push/email delivery and every donor inbox provider |
| Automation | Durable schedules and bounded dependency workflows using core owners; wake/recovery | Importing all donor workflow definitions |
| Knowledge/memory | Text/Markdown/JSON/CSV/HTML extraction, source-backed knowledge/search/revisions, temporal memory correction/retraction | OCR and office/PDF decoding without a real installed decoder |
| Files/artifacts | Scoped uploads, rooted file operations, previews/downloads and artifact provenance | Unrestricted server filesystem browsing |
| Connections/channels | Versioned connection setup/grants/probe/revoke, signed webhook and HTTP MCP adapters | All 52 Gideon bundles or all messaging vendors |
| Tools/plugins/skills | Consumer lifecycle/configuration over existing versioned core services, real registered tools | A public marketplace or unreviewed remote code installation |
| Browser/research/devices | Actual CDP browser, bounded search/fetch with source artifacts, paired remote-device protocol | Native Windows/macOS/Linux desktop actuators or autonomous publishing without grants |
| Speech/media | Configurable real HTTP transcription/synthesis/image providers and artifact delivery | Bundled model weights or universal vendor compatibility |
| Permissions | Revisioned policy, exact bounded approvals, revocation/fencing and audit | Client-supplied claims of owner authority |
| Usage/ownership | Factual usage/overages, unresolved reservations, resource allocations/limits, backup/export/restore | Monetary estimates, financial settlement, crypto/wallet features and payment processor deployment |
| Operations | Self-host CLI/service/container packaging, hosted namespace workers, durable lifecycle/health/diagnostics | Paid cloud provisioning, public release or production certification |

The two modes expose the same implemented feature catalog. Differences in available providers, devices or operator policy are presented as concrete readiness/capability states. Local installation does not require hosted account infrastructure.
