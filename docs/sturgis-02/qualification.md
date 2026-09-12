# Sturgis 02 qualification

The complete core and consumer implementation passed on **2026-09-12 at 16:05 UTC**,
using Python 3.12.3 on Linux. All **13 sturgis-02 implementation tasks** passed their
declared verification and source-trace checks. The final run contains **850 automated
tests plus the production-browser journey**; it exercises self-hosted and hosted
installation modes within the operational limits below.

## Executed gate

```sh
.venv/bin/python scripts/qualify_consumer.py
```

This command was executed through `cg spec done 4.1`. It built the client and native
worker, cleared native counters, ran the complete native and Python suites, then
ran the coverage collector tests, UI tests and real browser journey. Each language
passed its independent, unrounded **90% executable-line floor**.

| Measurement | Result |
| --- | --- |
| Python core and consumer tests | **674 passed**, 336.01 seconds |
| Native process tests | **72 passed**, 13.66 seconds |
| Frontend component and data tests | **98 passed**, 29.19 seconds |
| Browser coverage collector regressions | **6 passed** |
| Python executable lines | **9,277 / 9,479 — 97.8689735204%**, all 59 files |
| C++ executable lines | **494 / 505 — 97.8217821782%** |
| Authored TypeScript/JavaScript executable lines | **1,100 / 1,132 — 97.1731448763%** |
| Python branches | 3,227 / 3,452 |
| C++ branches | 1,510 / 2,977 |
| Frontend branches | 1,510 / 1,985 |

Branch coverage is reported separately and has no 90% floor. Python's terminal
coverage column combines lines and branches; it differs from the executable-line
percentage above. Python validation independently parses every source's executable
statements and checks their executed/missing partition, file summaries and totals.
Raw traced docstring lines cannot inflate that denominator. Its real-report
regressions cover omitted files and lines, tampered summaries, exclusions, empty
modules, duplicate data and below-floor results.

## Browser quality

The actual browser completed bootstrap, agent/conversation management, provider
readiness, credential rotation, skills and permissions, model streaming, steering
and cancellation, attachments, native memory edits, knowledge, rooted file effects,
connections, goals, diagnostics, keyboard navigation, logout/login and reload.
All **five accessibility scans reported zero violations**: bootstrap, empty
conversation, conversation with history, work and mobile conversation. The scans
used the WCAG 2 A/AA and 2.1 AA rule tags; they do not replace a complete human
accessibility audit.

The cold conversation loaded **216,357 bytes of compressed JavaScript**, below
the 250 KiB budget. The measured local input-frame p95 was **119.09 ms**, below
the 180 ms runner threshold. These are measurements from this Linux/Chromium
runner, not production field Web Vitals or a universal device benchmark. Fonts
and dependency notices ship with the client.

See the actual [empty conversation](../../evidence/sturgis-02/conversation-material3-empty.png),
[desktop conversation](../../evidence/sturgis-02/conversation-material3-desktop.png),
[mobile conversation](../../evidence/sturgis-02/conversation-material3-mobile.png),
and [work view](../../evidence/sturgis-02/work-material3-desktop.png).

## Evidence

- [Aggregate qualification record](../../evidence/sturgis-02/qualification.json)
- [Production source manifest](../../evidence/sturgis-02/source-manifest.json)
- [Python coverage](../../evidence/sturgis-02/python-coverage.json)
- [Native coverage](../../evidence/sturgis-02/native-coverage.json)
- [Frontend coverage](../../evidence/sturgis-02/web-coverage.json) and [source manifest](../../evidence/sturgis-02/web-source-manifest.json)
- [Browser quality measurements](../../evidence/sturgis-02/browser-quality.json)

The production source-manifest SHA-256 is
`29abc8017cc3b9824c5cf756afdca75a4eb645661ab847774cc63444902f590e`.
The aggregate record also hashes its coverage and browser reports. The gate
confirmed source stability during execution; no previous success record is reused.

## Verification boundaries

The consumer browser uses the production React bundle served by Python, real
accounts, private workers, filesystem effects and native memory. Its remote model
and HTTP connection endpoints are controlled wire servers. Component tests cover
additional keyboard/focus, error, scoped-query, unknown-acknowledgement and restore
recovery cases. Neither component fixtures nor a browser screenshot alone qualify
the complete system.

Python tests include the original core and consumer packages, actual encrypted
credentials and SQLite journals, strict HTTP authentication, IPC, schedules,
approvals, external ingress, tool effects, crash/restart and archive recovery.
Native tests build the C++ worker and collect gcov execution counts. Platform-wheel
tests build independent release binaries, install outside the checkout and exercise
real CLI/private-worker journeys in both deployment modes.

All three language denominators independently require 90% executable line coverage.
The web denominator includes every authored TypeScript/JavaScript file; source maps
bind production V8 execution to the same AST statement coverage used by component
tests. Generated runtime exceptions require exact build-module provenance. Source,
assets, maps, lockfile, tests and runtime inputs cannot change during the web gate.
Branches are measured and reported separately from the line threshold.

The Codify source graph indexes authored modules. Generated client assets are
excluded from its public-symbol ledger; the browser gate still checks and hashes
every built asset, map, font and license notice and measures mapped authored code.

## Operational limits

- The tested host is Linux. Namespace execution requires an operator environment
  permitting bubblewrap and the configured resource limits.
- Hosted workers isolate private filesystem and process state, with networking
  shared as documented. Supplied HTTP adapters enforce pinned destination policy;
  arbitrary installed plugin code is trusted code, not tenant-safe native code.
- The concrete browser works in the exercised self-host configuration with a
  sufficiently large virtual address-space limit. Nested browser networking in
  the tested hosted namespace returns `browser_unavailable` with
  `network_namespace_denied`. No containment boundary was weakened to hide this.
- Memory/address-space limits are not a hard aggregate RSS or cgroup quota.
- Model/search/MCP/media/device tests exercise the documented bounded protocols.
  They do not certify every vendor implementation, OS actuator or proprietary API.
- Backup restores data and historical evidence. Credentials, grants, active
  connections, execution authority and scheduled work require deliberate setup.
- No public deployment, paid cloud, payment settlement or live model-vendor
  certification is claimed.

See [Deployment](deployment.md) for exact protocols, installation steps and resource
bounds, [Using Sturgis](consumer.md) for user journeys and the [HTTP contract](../../spec/sturgis-02/http-contract.md)
for command parameters. The earlier [core qualification](../qualification.md) is
historical sturgis-01 evidence; the consumer gate remeasures the current sources.

## Full-system documentation verification

The documentation gate covers the core, native protocol, consumer interface,
installation, hosted operation, HTTP contracts and qualification limits. Its
867-row ledger retains all 525 current required claims and resolves all 374
original core claims, merging duplicate source identities. The original core
measurements above remain historical; current regression links do not rewrite
that evidence.

The recorded commands are `cg docs check -f sturgis-02`,
`cg docs check -f sturgis-01`, `cg spec render --check` and `cg check`.
A separate Markdown audit verifies local file links, definition-line bounds and
heading anchors throughout the 43-document system and research set. External URLs
are counted without fetching; absolute donor links are verified on this host and
are not portable checkout links. Codify's inline-anchor freshness warnings are
reported separately from broken links and documentation claim failures.

See the [documentation gate record](../../evidence/sturgis-02/documentation-gate.json),
[source-claim audit](../../evidence/sturgis-02/documentation-claims.json) and
[local-link audit](../../evidence/sturgis-02/documentation-links.json).

## Source navigation index

These links identify current definitions in the consumer implementation and its
verification sources. They supplement the [developer entry-point map](deployment.md#developer-entry-points)
and the [core guide](../core.md). Projection types, private helpers, nested fixture
functions, and collector internals are not independently versioned extension APIs.
Repeated helper names retain separate definition-line links; their qualified scopes
are preserved in the documentation claims audit. This index is navigation evidence:
the qualification results above, their reports, and their stated limits establish
what was exercised. A symbol link alone does not establish a behavioral guarantee.

### Consumer implementation details

<details>
<summary>consumer/application.py</summary>

[_ConsumerChannel](../../consumer/application.py#L47).

</details>

<details>
<summary>consumer/cli.py</summary>

[Parser](../../consumer/cli.py#L175).

</details>

<details>
<summary>consumer/content.py</summary>

[_VisibleHTML](../../consumer/content.py#L42).

</details>

<details>
<summary>consumer/providers.py</summary>

[_PolicyClient](../../consumer/providers.py#L20), [_Response](../../consumer/providers.py#L38).

</details>

<details>
<summary>consumer/vault.py</summary>

[_Connection](../../consumer/vault.py#L146).

</details>

<details>
<summary>consumer/worker.py</summary>

[main](../../consumer/worker.py#L214).

</details>

### Frontend projection types and internal helpers

<details>
<summary>frontend/src/App.tsx</summary>

[Installation](../../frontend/src/App.tsx#L36).

</details>

<details>
<summary>frontend/src/components/ui.tsx</summary>

[ButtonProps](../../frontend/src/components/ui.tsx#L26), [CardProps](../../frontend/src/components/ui.tsx#L89), [BadgeTone](../../frontend/src/components/ui.tsx#L122).

</details>

<details>
<summary>frontend/src/hooks/useAgent.tsx</summary>

[AgentContextValue](../../frontend/src/hooks/useAgent.tsx#L9), [Recovery](../../frontend/src/hooks/useAgent.tsx#L173).

</details>

<details>
<summary>frontend/src/lib/types.ts</summary>

[Json](../../frontend/src/lib/types.ts#L2), [Params](../../frontend/src/lib/types.ts#L4), [Account](../../frontend/src/lib/types.ts#L5), [Agent](../../frontend/src/lib/types.ts#L11), [RuntimeState](../../frontend/src/lib/types.ts#L17), [Health](../../frontend/src/lib/types.ts#L27), [Conversation](../../frontend/src/lib/types.ts#L32), [Page](../../frontend/src/lib/types.ts#L39),
[Source](../../frontend/src/lib/types.ts#L43), [Knowledge](../../frontend/src/lib/types.ts#L53), [FileEntry](../../frontend/src/lib/types.ts#L61), [FileBody](../../frontend/src/lib/types.ts#L67), [Provider](../../frontend/src/lib/types.ts#L73), [Credential](../../frontend/src/lib/types.ts#L86), [ToolPolicy](../../frontend/src/lib/types.ts#L92), [Approval](../../frontend/src/lib/types.ts#L99),
[Connection](../../frontend/src/lib/types.ts#L105), [Goal](../../frontend/src/lib/types.ts#L116), [Commitment](../../frontend/src/lib/types.ts#L122), [Schedule](../../frontend/src/lib/types.ts#L129), [Workflow](../../frontend/src/lib/types.ts#L136), [Work](../../frontend/src/lib/types.ts#L143), [Proposal](../../frontend/src/lib/types.ts#L149), [WorkData](../../frontend/src/lib/types.ts#L154),
[Skill](../../frontend/src/lib/types.ts#L163), [Plugin](../../frontend/src/lib/types.ts#L176), [MemoryRecord](../../frontend/src/lib/types.ts#L181), [MemoryResult](../../frontend/src/lib/types.ts#L189), [Usage](../../frontend/src/lib/types.ts#L194), [Overview](../../frontend/src/lib/types.ts#L201), [PublicEvent](../../frontend/src/lib/types.ts#L210), [EventPage](../../frontend/src/lib/types.ts#L227),
[MessageReceipt](../../frontend/src/lib/types.ts#L232).

</details>

<details>
<summary>frontend/src/pages/ConnectionsPage.tsx</summary>

[ConnectionRow](../../frontend/src/pages/ConnectionsPage.tsx#L40), [Pairing](../../frontend/src/pages/ConnectionsPage.tsx#L48).

</details>

<details>
<summary>frontend/src/pages/ConversationPage.tsx</summary>

[Draft](../../frontend/src/pages/ConversationPage.tsx#L52), [Message](../../frontend/src/pages/ConversationPage.tsx#L57).

</details>

<details>
<summary>frontend/src/pages/SettingsPage.tsx</summary>

[Backup](../../frontend/src/pages/SettingsPage.tsx#L46).

</details>

<details>
<summary>frontend/src/pages/WorkPage.tsx</summary>

[Row](../../frontend/src/pages/WorkPage.tsx#L36), [Goal](../../frontend/src/pages/WorkPage.tsx#L37), [Commitment](../../frontend/src/pages/WorkPage.tsx#L38), [Schedule](../../frontend/src/pages/WorkPage.tsx#L44), [Workflow](../../frontend/src/pages/WorkPage.tsx#L45), [Work](../../frontend/src/pages/WorkPage.tsx#L49), [Proposal](../../frontend/src/pages/WorkPage.tsx#L50), [WorkData](../../frontend/src/pages/WorkPage.tsx#L55),
[Editor](../../frontend/src/pages/WorkPage.tsx#L64).

</details>

<details>
<summary>frontend/src/pages/shared.tsx</summary>

[FormField](../../frontend/src/pages/shared.tsx#L69).

</details>

### Qualification drivers and collectors

<details>
<summary>scripts/qualify_consumer.py</summary>

[main](../../scripts/qualify_consumer.py#L114).

</details>

<details>
<summary>tests/consumer_web/coverage.mjs</summary>

[slash](../../tests/consumer_web/coverage.mjs#L10), [sha256](../../tests/consumer_web/coverage.mjs#L11), [walk](../../tests/consumer_web/coverage.mjs#L12), [snapshot](../../tests/consumer_web/coverage.mjs#L22), [assertSnapshot](../../tests/consumer_web/coverage.mjs#L84), [filePath](../../tests/consumer_web/coverage.mjs#L90), [onlyAuthored](../../tests/consumer_web/coverage.mjs#L93), [zero](../../tests/consumer_web/coverage.mjs#L105),
[baseline](../../tests/consumer_web/coverage.mjs#L116), [files](../../tests/consumer_web/coverage.mjs#L117) (line 117), [browserCoverage](../../tests/consumer_web/coverage.mjs#L150), [files](../../tests/consumer_web/coverage.mjs#L154) (line 154), [mergeCoverage](../../tests/consumer_web/coverage.mjs#L299).

</details>

<details>
<summary>tests/consumer_web/coverage.test.mjs</summary>

[fixture](../../tests/consumer_web/coverage.test.mjs#L20).

</details>

<details>
<summary>tests/consumer_web/journey.mjs</summary>

[checkpointCoverage](../../tests/consumer_web/journey.mjs#L41), [click](../../tests/consumer_web/journey.mjs#L47), [fill](../../tests/consumer_web/journey.mjs#L48), [card](../../tests/consumer_web/journey.mjs#L50), [dialog](../../tests/consumer_web/journey.mjs#L54), [nav](../../tests/consumer_web/journey.mjs#L55), [tab](../../tests/consumer_web/journey.mjs#L71), [notices](../../tests/consumer_web/journey.mjs#L77),
[noErrors](../../tests/consumer_web/journey.mjs#L78), [dismiss](../../tests/consumer_web/journey.mjs#L81), [axe](../../tests/consumer_web/journey.mjs#L91).

</details>

<details>
<summary>tests/consumer_web/server.py</summary>

[main](../../tests/consumer_web/server.py#L17), [model](../../tests/consumer_web/server.py#L19), [emit](../../tests/consumer_web/server.py#L30).

</details>

### Python fixtures and verification entry points

<details>
<summary>tests/consumer/test_api.py</summary>

[api](../../tests/consumer/test_api.py#L23), [command](../../tests/consumer/test_api.py#L33), [agent](../../tests/consumer/test_api.py#L39), [test_accounts_agents_worker_and_revisioned_lifecycle](../../tests/consumer/test_api.py#L45), [test_conversation_content_events_and_no_worker_scope_override](../../tests/consumer/test_api.py#L79), [test_signed_webhook_preserves_exact_wire_body_and_never_becomes_owner_input](../../tests/consumer/test_api.py#L109), [test_private_bootstrap_file_and_operator_origin_configuration](../../tests/consumer/test_api.py#L137).

</details>

<details>
<summary>tests/consumer/test_api_security.py</summary>

[installation](../../tests/consumer/test_api_security.py#L25), [data](../../tests/consumer/test_api_security.py#L36), [denied](../../tests/consumer/test_api_security.py#L44), [bootstrap](../../tests/consumer/test_api_security.py#L55), [bearer](../../tests/consumer/test_api_security.py#L59), [agent](../../tests/consumer/test_api_security.py#L63), [command](../../tests/consumer/test_api_security.py#L67), [test_host_origin_and_forwarding_headers_do_not_change_authority](../../tests/consumer/test_api_security.py#L83),
[test_all_routes_and_static_assets_share_security_headers](../../tests/consumer/test_api_security.py#L91), [test_cookie_mutation_requires_origin_and_matching_session_csrf](../../tests/consumer/test_api_security.py#L105), [test_bearer_has_explicit_precedence_and_query_token_is_not_authentication](../../tests/consumer/test_api_security.py#L121), [test_https_cookie_attributes_and_login_rotation](../../tests/consumer/test_api_security.py#L137), [test_ambiguous_authentication_headers_fail_closed](../../tests/consumer/test_api_security.py#L152), [test_malformed_json_is_typed_safe_and_cannot_create_accounts](../../tests/consumer/test_api_security.py#L178), [test_streamed_body_bound_and_content_type_before_domain_effects](../../tests/consumer/test_api_security.py#L187), [chunks](../../tests/consumer/test_api_security.py#L189),
[test_cross_owner_requests_and_reserved_commands_never_reach_worker](../../tests/consumer/test_api_security.py#L202), [test_private_command_scope_fields_and_duplicate_json_fail_closed](../../tests/consumer/test_api_security.py#L223), [test_stale_archive_revision_does_not_suspend_running_worker](../../tests/consumer/test_api_security.py#L242), [test_secret_inspection_and_attachment_output_are_safe](../../tests/consumer/test_api_security.py#L257), [test_external_device_http_routes_pair_and_complete_actual_authorized_work](../../tests/consumer/test_api_security.py#L299), [owner_command](../../tests/consumer/test_api_security.py#L307), [headers](../../tests/consumer/test_api_security.py#L321).

</details>

<details>
<summary>tests/consumer/test_application.py</summary>

[configuration](../../tests/consumer/test_application.py#L16), [app](../../tests/consumer/test_application.py#L26), [test_strict_commands_and_conversation_cas](../../tests/consumer/test_application.py#L33), [test_scoped_content_credentials_and_skill_procedure](../../tests/consumer/test_application.py#L65), [test_accepted_input_replay_cancel_and_safe_events_without_provider](../../tests/consumer/test_application.py#L92), [test_lifecycle_fence_prevents_model_and_timer_admission](../../tests/consumer/test_application.py#L120), [test_registered_content_tools_use_scheduler_and_exact_file_effects](../../tests/consumer/test_application.py#L133), [invoke](../../tests/consumer/test_application.py#L139),
[test_connection_configuration_is_versioned_through_application](../../tests/consumer/test_application.py#L163).

</details>

<details>
<summary>tests/consumer/test_application_domains.py</summary>

[app](../../tests/consumer/test_application_domains.py#L25), [admit](../../tests/consumer/test_application_domains.py#L34), [invoke](../../tests/consumer/test_application_domains.py#L40), [signed](../../tests/consumer/test_application_domains.py#L45), [test_domain_allowlists_and_artifact_ranges_are_scoped](../../tests/consumer/test_application_domains.py#L52), [test_goal_commitment_wait_and_schedule_commands_use_current_revisions](../../tests/consumer/test_application_domains.py#L87), [test_permission_commands_project_rejection_and_consume_exact_future_grant](../../tests/consumer/test_application_domains.py#L117), [test_workflow_commands_execute_dependencies_and_preserve_evidence](../../tests/consumer/test_application_domains.py#L143),
[test_attention_inbox_and_workflow_revision_commands](../../tests/consumer/test_application_domains.py#L167), [test_child_commands_reject_unsupported_outcomes_and_cancel_active_child](../../tests/consumer/test_application_domains.py#L187), [test_device_commands_pair_sign_execute_and_fence_revocation](../../tests/consumer/test_application_domains.py#L208), [test_plugin_commands_run_confined_versions_health_rollback_and_uninstall](../../tests/consumer/test_application_domains.py#L248), [test_plugin_concurrent_same_revision_mutations_have_one_winner](../../tests/consumer/test_application_domains.py#L280), [test_plugin_revision_types_cannot_alias_integer_cas](../../tests/consumer/test_application_domains.py#L291).

</details>

<details>
<summary>tests/consumer/test_cli.py</summary>

[cli](../../tests/consumer/test_cli.py#L29), [records](../../tests/consumer/test_cli.py#L41), [test_installed_cli_http_login_agent_native_worker_model_chat_and_cancel](../../tests/consumer/test_cli.py#L45), [test_installed_setup_guidance_and_actual_serve](../../tests/consumer/test_cli.py#L103), [test_private_store_rotation_rejects_links_permissions_and_oversize](../../tests/consumer/test_cli.py#L135), [test_private_output_redaction_and_safe_argument_errors](../../tests/consumer/test_cli.py#L160), [test_params_prompts_and_local_failures](../../tests/consumer/test_cli.py#L173), [interrupt](../../tests/consumer/test_cli.py#L186),
[test_serve_settings_preserve_operator_choices](../../tests/consumer/test_cli.py#L194), [test_client_normalizes_wire_errors_and_does_not_follow_redirects](../../tests/consumer/test_cli.py#L219), [handler](../../tests/consumer/test_cli.py#L220) (line 220), [test_client_session_origin_binding_timeout_byte_limits_and_request_identity](../../tests/consumer/test_cli.py#L231), [handler](../../tests/consumer/test_cli.py#L232) (line 232), [test_events_follow_advances_cursor_and_secret_output_file](../../tests/consumer/test_cli.py#L263), [handler](../../tests/consumer/test_cli.py#L265) (line 265), [test_backup_restore_exact_http_transport_integrity_and_private_archive](../../tests/consumer/test_cli.py#L295),
[handler](../../tests/consumer/test_cli.py#L301) (line 301).

</details>

<details>
<summary>tests/consumer/test_connections.py</summary>

[wire](../../tests/consumer/test_connections.py#L27), [serve](../../tests/consumer/test_connections.py#L29), [services](../../tests/consumer/test_connections.py#L62), [admit_work](../../tests/consumer/test_connections.py#L72), [ready](../../tests/consumer/test_connections.py#L78), [test_urls_fail_closed](../../tests/consumer/test_connections.py#L85), [test_origin_and_strict_json](../../tests/consumer/test_connections.py#L89), [test_operator_limits](../../tests/consumer/test_connections.py#L99),
[test_request_shape_fails_before_network](../../tests/consumer/test_connections.py#L105), [test_actual_private_origin_and_dns_pin](../../tests/consumer/test_connections.py#L110), [audited](../../tests/consumer/test_connections.py#L120), [test_dns_errors_and_mixed_addresses](../../tests/consumer/test_connections.py#L132), [no_dns](../../tests/consumer/test_connections.py#L134), [empty](../../tests/consumer/test_connections.py#L138), [mixed](../../tests/consumer/test_connections.py#L142), [test_actual_tls_keeps_hostname_verification_and_sni](../../tests/consumer/test_connections.py#L149),
[test_redirects_revalidate_destinations_and_do_not_forward_secrets](../../tests/consumer/test_connections.py#L173), [redirect](../../tests/consumer/test_connections.py#L175), [test_wire_response_bounds](../../tests/consumer/test_connections.py#L199), [reply](../../tests/consumer/test_connections.py#L200), [test_stream_deltas_and_deadline_include_queued_wait](../../tests/consumer/test_connections.py#L207), [streaming](../../tests/consumer/test_connections.py#L209), [test_connection_lifecycle_exact_grants_scheduler_and_rotation](../../tests/consumer/test_connections.py#L232), [test_delegated_workspace_cannot_use_unconstrained_network](../../tests/consumer/test_connections.py#L267),
[test_connection_configuration_validation](../../tests/consumer/test_connections.py#L282), [test_unavailable_and_failed_probes](../../tests/consumer/test_connections.py#L288), [test_probe_race_cannot_admit_old_config_or_credential](../../tests/consumer/test_connections.py#L304), [delayed](../../tests/consumer/test_connections.py#L306), [signature](../../tests/consumer/test_connections.py#L321), [test_webhook_durable_dedupe_identity_and_no_owner_instruction](../../tests/consumer/test_connections.py#L325), [test_webhook_rejections_have_no_admission](../../tests/consumer/test_connections.py#L359).

</details>

<details>
<summary>tests/consumer/test_connectors.py</summary>

[reply](../../tests/consumer/test_connectors.py#L25), [connect](../../tests/consumer/test_connectors.py#L33), [invoke](../../tests/consumer/test_connectors.py#L39), [test_real_search_schema_sources_and_registration](../../tests/consumer/test_connectors.py#L46), [handler](../../tests/consumer/test_connectors.py#L47) (line 47), [test_bad_search_evidence_cannot_be_ready](../../tests/consumer/test_connectors.py#L65), [handler](../../tests/consumer/test_connectors.py#L66) (line 66), [test_adapter_option_bounds](../../tests/consumer/test_connectors.py#L77),
[mcp_wire](../../tests/consumer/test_connectors.py#L84), [handler](../../tests/consumer/test_connectors.py#L87) (line 87), [test_mcp_real_initialize_catalog_schema_and_call](../../tests/consumer/test_connectors.py#L117), [test_mcp_invalid_negotiation_and_response_rejected](../../tests/consumer/test_connectors.py#L138), [test_mcp_grants_and_actual_remote_target_errors](../../tests/consumer/test_connectors.py#L145), [test_remote_mcp_validator_programs_are_bounded](../../tests/consumer/test_connectors.py#L162), [test_mcp_schema_keyword_named_parameters_are_plain_properties](../../tests/consumer/test_connectors.py#L172), [wav_bytes](../../tests/consumer/test_connectors.py#L179),
[test_media_wire_transcription_audio_image_and_artifact_scope](../../tests/consumer/test_connectors.py#L186), [handler](../../tests/consumer/test_connectors.py#L188) (line 188), [test_media_invalid_wire_output_never_becomes_artifact](../../tests/consumer/test_connectors.py#L222), [handler](../../tests/consumer/test_connectors.py#L223) (line 223), [test_media_probe_does_not_invent_available_models](../../tests/consumer/test_connectors.py#L235), [handler](../../tests/consumer/test_connectors.py#L236) (line 236), [signed](../../tests/consumer/test_connectors.py#L242), [paired](../../tests/consumer/test_connectors.py#L248),
[test_device_pairing_wire_poll_actual_action_and_signed_result](../../tests/consumer/test_connectors.py#L259), [endpoint](../../tests/consumer/test_connectors.py#L270), [test_device_cancellation_late_evidence_and_schema_denial](../../tests/consumer/test_connectors.py#L311), [test_device_forgery_cannot_poll](../../tests/consumer/test_connectors.py#L337), [test_device_pairing_revision_identity_and_response_bounds](../../tests/consumer/test_connectors.py#L344), [test_unavailable_browser_is_an_explicit_readiness_failure](../../tests/consumer/test_connectors.py#L360), [test_real_chromium_executes_javascript_and_blocks_ungranted_subresources](../../tests/consumer/test_connectors.py#L370), [handler](../../tests/consumer/test_connectors.py#L372) (line 372),
[test_real_browser_raw_network_has_no_host_access](../../tests/consumer/test_connectors.py#L396), [test_browser_prelaunch_origin_denial_and_actual_resident_limit](../../tests/consumer/test_connectors.py#L409), [test_browser_diagnostics_only_expose_safe_operator_reason](../../tests/consumer/test_connectors.py#L422).

</details>

<details>
<summary>tests/consumer/test_consumer_gate.py</summary>

[test_language_floors_use_unrounded_executable_lines](../../tests/consumer/test_consumer_gate.py#L17), [test_complete_denominators_and_totals](../../tests/consumer/test_consumer_gate.py#L24), [actual_python_report](../../tests/consumer/test_consumer_gate.py#L57), [test_real_coverage_docstrings_empty_modules_and_missing_branch](../../tests/consumer/test_consumer_gate.py#L95), [test_real_python_report_tampering_is_rejected](../../tests/consumer/test_consumer_gate.py#L114), [test_real_missing_execution_cannot_hide_below_the_floor](../../tests/consumer/test_consumer_gate.py#L131).

</details>

<details>
<summary>tests/consumer/test_content.py</summary>

[content](../../tests/consumer/test_content.py#L16), [uploaded](../../tests/consumer/test_content.py#L25), [memory_ids](../../tests/consumer/test_content.py#L29), [test_upload_is_durable_typed_idempotent_and_integrity_checked](../../tests/consumer/test_content.py#L33), [test_unsupported_and_oversized_extraction_retains_original](../../tests/consumer/test_content.py#L65), [test_html_excludes_active_content_and_attachment_budget_is_utf8_safe](../../tests/consumer/test_content.py#L77), [test_fixed_scope_denies_foreign_content_even_with_shared_stores](../../tests/consumer/test_content.py#L92), [test_workspace_conditional_effects_protected_state_and_symlinks](../../tests/consumer/test_content.py#L109),
[test_reserved_or_escaping_paths_are_denied_for_reads_and_writes](../../tests/consumer/test_content.py#L136), [test_knowledge_chunks_exact_evidence_search_revisions_and_retraction](../../tests/consumer/test_content.py#L143), [test_user_memory_correction_preserves_history_and_actual_retraction](../../tests/consumer/test_content.py#L182), [test_unavailable_indexing_is_truthful_and_resumes_from_durable_sources](../../tests/consumer/test_content.py#L210), [test_upload_validation](../../tests/consumer/test_content.py#L234), [test_range_revision_and_context_validation](../../tests/consumer/test_content.py#L242), [test_independent_content_calls_remain_responsive_while_native_is_stopped](../../tests/consumer/test_content.py#L258).

</details>

<details>
<summary>tests/consumer/test_contracts.py</summary>

[test_identifier_rejects_untrusted_paths](../../tests/consumer/test_contracts.py#L12), [test_contracts_reject_unknown_missing_oversized_and_non_json](../../tests/consumer/test_contracts.py#L17), [test_envelopes_protect_metadata_and_unexpected_errors](../../tests/consumer/test_contracts.py#L29), [test_operator_settings_are_canonical_immutable_and_secret_repr](../../tests/consumer/test_contracts.py#L40), [test_settings_fail_closed](../../tests/consumer/test_contracts.py#L58), [test_environment_loading](../../tests/consumer/test_contracts.py#L63).

</details>

<details>
<summary>tests/consumer/test_identity.py</summary>

[journal](../../tests/consumer/test_identity.py#L29), [accounts](../../tests/consumer/test_identity.py#L36), [owner](../../tests/consumer/test_identity.py#L40), [error](../../tests/consumer/test_identity.py#L45), [_Error](../../tests/consumer/test_identity.py#L49), [__init__](../../tests/consumer/test_identity.py#L50), [__enter__](../../tests/consumer/test_identity.py#L53), [__exit__](../../tests/consumer/test_identity.py#L58),
[test_bootstrap_login_persistence_and_secret_projection](../../tests/consumer/test_identity.py#L64), [test_owner_isolation_and_administrator_cannot_read_private_agents](../../tests/consumer/test_identity.py#L98), [test_directory_revisions_archive_and_invalid_inputs](../../tests/consumer/test_identity.py#L126), [test_account_configuration_rejected](../../tests/consumer/test_identity.py#L149), [test_account_input_validation_throttling_and_expiry](../../tests/consumer/test_identity.py#L154), [test_vault_rotation_persistence_scope_and_revocation](../../tests/consumer/test_identity.py#L185), [test_vault_ciphertext_integrity_and_compare_swap](../../tests/consumer/test_identity.py#L223), [rotate](../../tests/consumer/test_identity.py#L226),
[test_vault_private_paths_and_key_validation](../../tests/consumer/test_identity.py#L242), [provider_server](../../tests/consumer/test_identity.py#L264), [Handler](../../tests/consumer/test_identity.py#L266), [log_message](../../tests/consumer/test_identity.py#L267), [do_POST](../../tests/consumer/test_identity.py#L270), [core](../../tests/consumer/test_identity.py#L302), [settings](../../tests/consumer/test_identity.py#L309), [configure](../../tests/consumer/test_identity.py#L316),
[test_provider_real_probe_activation_runtime_stream_and_usage](../../tests/consumer/test_identity.py#L320), [test_provider_configuration_revisions_expiry_and_tools_probe](../../tests/consumer/test_identity.py#L352), [test_provider_failures_unknown_usage_and_budget_overrun](../../tests/consumer/test_identity.py#L383), [test_provider_probe_rotation_and_config_races_preserve_usage](../../tests/consumer/test_identity.py#L402), [test_provider_probe_cancellation_and_busy_admission](../../tests/consumer/test_identity.py#L425), [test_provider_refresh_does_not_override_human_selection](../../tests/consumer/test_identity.py#L446), [test_provider_network_denial_and_configuration_validation](../../tests/consumer/test_identity.py#L466), [test_usage_deduplicates_children_and_tool_receipts](../../tests/consumer/test_identity.py#L493).

</details>

<details>
<summary>tests/consumer/test_portability.py</summary>

[installation](../../tests/consumer/test_portability.py#L29), [backup_when_idle](../../tests/consumer/test_portability.py#L45), [attempt](../../tests/consumer/test_portability.py#L46), [repack](../../tests/consumer/test_portability.py#L54), [test_actual_private_worker_backup_restores_content_memory_history_without_authority](../../tests/consumer/test_portability.py#L70), [command](../../tests/consumer/test_portability.py#L74), [empty_backup](../../tests/consumer/test_portability.py#L151), [test_manifest_schema_hash_and_restore_failures_leave_no_visible_agent](../../tests/consumer/test_portability.py#L165),
[test_archive_paths_fail_before_any_content_is_written](../../tests/consumer/test_portability.py#L174), [test_archive_special_files_and_duplicate_names_are_rejected](../../tests/consumer/test_portability.py#L182), [extra](../../tests/consumer/test_portability.py#L184), [test_transport_and_corrupt_zip_are_rejected](../../tests/consumer/test_portability.py#L194), [test_snapshot_lock_cancellation_and_filesystem_bounds](../../tests/consumer/test_portability.py#L199), [slow](../../tests/consumer/test_portability.py#L206) (line 206), [copying](../../tests/consumer/test_portability.py#L211), [test_logical_memory_validation_and_missing_worker_cleanup](../../tests/consumer/test_portability.py#L230),
[malformed](../../tests/consumer/test_portability.py#L232), [test_independent_archive_inflation_file_count_and_transport_bounds](../../tests/consumer/test_portability.py#L246), [test_current_derived_projection_vectors_revision_retraction_and_source_history_survive](../../tests/consumer/test_portability.py#L254), [test_restore_reservation_fences_commands_and_cancelled_import_is_removed](../../tests/consumer/test_portability.py#L289), [slow](../../tests/consumer/test_portability.py#L293) (line 293), [importing](../../tests/consumer/test_portability.py#L299), [test_cancelled_restore_settles_reservation_directory_and_publication_writes](../../tests/consumer/test_portability.py#L313), [delayed](../../tests/consumer/test_portability.py#L320) (line 320),
[delayed](../../tests/consumer/test_portability.py#L327) (line 327), [delayed](../../tests/consumer/test_portability.py#L334) (line 334), [at_boundary](../../tests/consumer/test_portability.py#L342), [test_owner_crash_before_restore_publication_is_discarded_on_startup](../../tests/consumer/test_portability.py#L363).

</details>

<details>
<summary>tests/consumer/test_work.py</summary>

[register_files](../../tests/consumer/test_work.py#L23), [conversation](../../tests/consumer/test_work.py#L31), [core](../../tests/consumer/test_work.py#L39), [admitted](../../tests/consumer/test_work.py#L49), [write_call](../../tests/consumer/test_work.py#L55), [invoke](../../tests/consumer/test_work.py#L59), [test_exact_approval_rollback_no_automatic_effect_and_replay](../../tests/consumer/test_work.py#L64), [test_approval_is_one_use_under_concurrency_and_exact_arguments](../../tests/consumer/test_work.py#L96),
[test_child_approval_cannot_expand_into_parent_workspace](../../tests/consumer/test_work.py#L112), [test_policy_revocation_fences_work_children_and_schedules](../../tests/consumer/test_work.py#L135), [test_decision_expiry_denial_source_checks_and_scope](../../tests/consumer/test_work.py#L169), [test_large_approval_bound_and_changed_tool_descriptor](../../tests/consumer/test_work.py#L207), [test_connection_revision_invalidates_permission_and_approval](../../tests/consumer/test_work.py#L226), [test_goal_commitment_and_schedule_owner_projections](../../tests/consumer/test_work.py#L255), [test_schedule_and_work_cancellation_validation](../../tests/consumer/test_work.py#L291), [workflow_harness](../../tests/consumer/test_work.py#L313),
[test_workflow_executes_real_dependencies_and_scoped_tool_checks](../../tests/consumer/test_work.py#L332), [test_workflow_narrative_does_not_satisfy_outcome_checks_and_restart_dedup](../../tests/consumer/test_work.py#L354), [test_later_budget_failure_preserves_prior_evidence_and_can_be_revised](../../tests/consumer/test_work.py#L373), [test_consumer_child_delegation_has_real_isolated_effect_and_result](../../tests/consumer/test_work.py#L390), [test_pause_fences_timer_work_before_assignment_projection](../../tests/consumer/test_work.py#L412), [test_suspend_waits_for_actual_inflight_journal_projection](../../tests/consumer/test_work.py#L425), [block_journal](../../tests/consumer/test_work.py#L428), [advance_owns_lock](../../tests/consumer/test_work.py#L435),
[test_workflow_budgets_revisions_manual_steps_and_monitor_pause](../../tests/consumer/test_work.py#L457), [test_initiative_manual_and_bounded_execution_attention_limits](../../tests/consumer/test_work.py#L501), [test_child_scope_views_delegation_and_cancel](../../tests/consumer/test_work.py#L536).

</details>

<details>
<summary>tests/consumer/test_workers.py</summary>

[private](../../tests/consumer/test_workers.py#L20), [frame](../../tests/consumer/test_workers.py#L31), [wire](../../tests/consumer/test_workers.py#L38), [test_config_private_files_ownership_and_operator_paths](../../tests/consumer/test_workers.py#L49), [test_authenticated_requests_responses_dedup_and_read_only_receipt_lookup](../../tests/consumer/test_workers.py#L77), [test_authenticated_malformed_fields_are_rejected](../../tests/consumer/test_workers.py#L111), [test_tampering_wrong_keys_unknown_methods_and_fixed_scope](../../tests/consumer/test_workers.py#L117), [test_secret_parameters_and_transient_responses_never_enter_receipts](../../tests/consumer/test_workers.py#L131),
[test_quiesce_resume_deadlines_and_unfinished_receipt_recovery](../../tests/consumer/test_workers.py#L159), [delayed](../../tests/consumer/test_workers.py#L166), [test_client_validation_and_no_dispatch_unavailable](../../tests/consumer/test_workers.py#L189), [test_configured_limits_optional_mounts_and_fail_closed_launcher](../../tests/consumer/test_workers.py#L201).

</details>

<details>
<summary>tests/consumer_integration/test_application.py</summary>

[application](../../tests/consumer_integration/test_application.py#L15), [test_attachment_sources_skill_labels_and_persisted_output](../../tests/consumer_integration/test_application.py#L36), [test_slow_wire_stream_allows_stop_and_next_input](../../tests/consumer_integration/test_application.py#L59), [test_restart_closes_receipt_admission_window_without_duplicate_model_work](../../tests/consumer_integration/test_application.py#L75), [test_unroutable_recovery_receipt_does_not_block_private_startup](../../tests/consumer_integration/test_application.py#L100).

</details>

<details>
<summary>tests/consumer_integration/test_http.py</summary>

[http_installation](../../tests/consumer_integration/test_http.py#L16), [started](../../tests/consumer_integration/test_http.py#L23), [test_real_http_worker_model_stream_and_responsive_steering](../../tests/consumer_integration/test_http.py#L37), [output](../../tests/consumer_integration/test_http.py#L54), [finished](../../tests/consumer_integration/test_http.py#L63).

</details>

<details>
<summary>tests/consumer_integration/test_lifecycle.py</summary>

[test_browser_resource_readiness_and_real_worker_model_tool_render](../../tests/consumer_integration/test_lifecycle.py#L23), [page](../../tests/consumer_integration/test_lifecycle.py#L33), [finished](../../tests/consumer_integration/test_lifecycle.py#L71), [eventually](../../tests/consumer_integration/test_lifecycle.py#L99), [quiet](../../tests/consumer_integration/test_lifecycle.py#L108), [check](../../tests/consumer_integration/test_lifecycle.py#L109), [suspend_when_quiet](../../tests/consumer_integration/test_lifecycle.py#L115), [attempt](../../tests/consumer_integration/test_lifecycle.py#L116) (line 116),
[descendants](../../tests/consumer_integration/test_lifecycle.py#L124), [test_actual_worker_artifact_memory_restart_and_cross_runtime_boundaries](../../tests/consumer_integration/test_lifecycle.py#L136), [test_message_payload_precedes_wake_secret_params_are_transient_and_retries_read_receipts](../../tests/consumer_integration/test_lifecycle.py#L183), [admitted](../../tests/consumer_integration/test_lifecycle.py#L206), [test_ambiguous_dispatch_is_reconciled_by_receipt_without_effect_reexecution](../../tests/consumer_integration/test_lifecycle.py#L227), [test_suspend_retains_due_schedule_and_real_watcher_wakes_worker](../../tests/consumer_integration/test_lifecycle.py#L266), [fired](../../tests/consumer_integration/test_lifecycle.py#L286), [test_archived_suspended_runtime_never_wakes_for_due_schedule_or_commands](../../tests/consumer_integration/test_lifecycle.py#L301),
[test_archive_busy_rejection_and_directory_cas_are_atomic](../../tests/consumer_integration/test_lifecycle.py#L355), [aborted_finalizer](../../tests/consumer_integration/test_lifecycle.py#L392), [attempt](../../tests/consumer_integration/test_lifecycle.py#L400) (line 400), [test_cancelled_archive_commit_remains_fenced_and_retry_finishes_stop](../../tests/consumer_integration/test_lifecycle.py#L417), [finalizer](../../tests/consumer_integration/test_lifecycle.py#L427), [attempt](../../tests/consumer_integration/test_lifecycle.py#L431) (line 431), [committing](../../tests/consumer_integration/test_lifecycle.py#L438), [test_busy_suspend_cancellation_failure_receipts_and_lifecycle_controls](../../tests/consumer_integration/test_lifecycle.py#L457),
[dispatched](../../tests/consumer_integration/test_lifecycle.py#L495), [test_nonresponsive_real_process_is_forcibly_reaped](../../tests/consumer_integration/test_lifecycle.py#L521), [test_parent_death_terminates_real_worker_and_new_owner_recovers](../../tests/consumer_integration/test_lifecycle.py#L532), [ready](../../tests/consumer_integration/test_lifecycle.py#L552), [gone](../../tests/consumer_integration/test_lifecycle.py#L559).

</details>

<details>
<summary>tests/consumer_integration/test_packaging.py</summary>

[installed_release](../../tests/consumer_integration/test_packaging.py#L23), [test_installed_cli_web_native_and_private_worker_start_without_vendor_accounts](../../tests/consumer_integration/test_packaging.py#L66), [cli](../../tests/consumer_integration/test_packaging.py#L117).

</details>

<details>
<summary>tests/consumer_integration/test_portability_http.py</summary>

[portable_http](../../tests/consumer_integration/test_portability_http.py#L17), [started](../../tests/consumer_integration/test_portability_http.py#L24), [backup_when_idle](../../tests/consumer_integration/test_portability_http.py#L42), [test_real_http_backup_restores_fresh_agent_content_memory_and_labeled_history](../../tests/consumer_integration/test_portability_http.py#L52), [test_real_http_backup_ownership_csrf_and_restore_hash_boundaries](../../tests/consumer_integration/test_portability_http.py#L95).

</details>

<details>
<summary>tests/consumer_integration/test_restore_idempotency.py</summary>

[test_real_lost_http_ack_replays_same_agent_and_scopes_identity_to_account](../../tests/consumer_integration/test_restore_idempotency.py#L24), [test_actual_owner_crash_restore_receipt_survives_atomic_publication](../../tests/consumer_integration/test_restore_idempotency.py#L73), [test_cli_repeated_restore_reuses_identity_and_explicit_new_request_creates_copy](../../tests/consumer_integration/test_restore_idempotency.py#L144), [test_durable_in_progress_receipt_fences_another_service_instance](../../tests/consumer_integration/test_restore_idempotency.py#L167), [pause](../../tests/consumer_integration/test_restore_idempotency.py#L172).

</details>

<details>
<summary>tests/consumer_integration/test_text_only_provider.py</summary>

[text_application](../../tests/consumer_integration/test_text_only_provider.py#L13), [test_default_text_provider_streams_before_completion_without_advertised_tools](../../tests/consumer_integration/test_text_only_provider.py#L40), [test_text_provider_unsolicited_tool_is_rejected_before_any_file_effect](../../tests/consumer_integration/test_text_only_provider.py#L68), [test_programmatic_core_default_still_requires_registered_tool_support](../../tests/consumer_integration/test_text_only_provider.py#L88).

</details>

<details>
<summary>tests/integration/test_continuity.py</summary>

[test_harness_assertion_failure_closes_blocked_provider_before_waiting_for_servers](../../tests/integration/test_continuity.py#L305), [fail_after_partial_output](../../tests/integration/test_continuity.py#L310).

</details>

### Frontend fixtures and component checks

<details>
<summary>frontend/tests/action-recovery.test.tsx</summary>

[reply](../../frontend/tests/action-recovery.test.tsx#L12), [mount](../../frontend/tests/action-recovery.test.tsx#L16), [wrapper](../../frontend/tests/action-recovery.test.tsx#L22), [execute](../../frontend/tests/action-recovery.test.tsx#L31).

</details>

<details>
<summary>frontend/tests/api.test.ts</summary>

[response](../../frontend/tests/api.test.ts#L12).

</details>

<details>
<summary>frontend/tests/app.test.tsx</summary>

[answer](../../frontend/tests/app.test.tsx#L6), [start](../../frontend/tests/app.test.tsx#L22).

</details>

<details>
<summary>frontend/tests/backup_fixture.py</summary>

[main](../../frontend/tests/backup_fixture.py#L16), [Transport](../../frontend/tests/backup_fixture.py#L34), [request](../../frontend/tests/backup_fixture.py#L35).

</details>

<details>
<summary>frontend/tests/components.test.tsx</summary>

[Example](../../frontend/tests/components.test.tsx#L79) (line 79), [Example](../../frontend/tests/components.test.tsx#L115) (line 115), [shell](../../frontend/tests/components.test.tsx#L167).

</details>

<details>
<summary>frontend/tests/connections.test.tsx</summary>

[connection](../../frontend/tests/connections.test.tsx#L6), [choose](../../frontend/tests/connections.test.tsx#L18).

</details>

<details>
<summary>frontend/tests/conversation.test.tsx</summary>

[envelope](../../frontend/tests/conversation.test.tsx#L14), [mount](../../frontend/tests/conversation.test.tsx#L19), [Page](../../frontend/tests/conversation.test.tsx#L129), [menu](../../frontend/tests/conversation.test.tsx#L348), [file](../../frontend/tests/conversation.test.tsx#L436) (line 436), [file](../../frontend/tests/conversation.test.tsx#L567) (line 567).

</details>

<details>
<summary>frontend/tests/files.test.tsx</summary>

[digest](../../frontend/tests/files.test.tsx#L9).

</details>

<details>
<summary>frontend/tests/harness.tsx</summary>

[Call](../../frontend/tests/harness.tsx#L6), [mountPage](../../frontend/tests/harness.tsx#L16).

</details>

<details>
<summary>frontend/tests/hooks.test.tsx</summary>

[reply](../../frontend/tests/hooks.test.tsx#L12), [flush](../../frontend/tests/hooks.test.tsx#L14), [wrapper](../../frontend/tests/hooks.test.tsx#L108), [Wrapper](../../frontend/tests/hooks.test.tsx#L112).

</details>

<details>
<summary>frontend/tests/runtime.test.tsx</summary>

[reply](../../frontend/tests/runtime.test.tsx#L20), [rig](../../frontend/tests/runtime.test.tsx#L42), [wrapper](../../frontend/tests/runtime.test.tsx#L84), [mount](../../frontend/tests/runtime.test.tsx#L93), [control](../../frontend/tests/runtime.test.tsx#L121), [cycle](../../frontend/tests/runtime.test.tsx#L154), [focusAndInvalidate](../../frontend/tests/runtime.test.tsx#L158).

</details>

<details>
<summary>frontend/tests/settings.test.tsx</summary>

[settings](../../frontend/tests/settings.test.tsx#L15), [tab](../../frontend/tests/settings.test.tsx#L71), [fixture](../../frontend/tests/settings.test.tsx#L481), [archiveFile](../../frontend/tests/settings.test.tsx#L489), [openRestore](../../frontend/tests/settings.test.tsx#L659), [response](../../frontend/tests/settings.test.tsx#L676).

</details>

<details>
<summary>frontend/tests/setup.ts</summary>

[addListener](../../frontend/tests/setup.ts#L23), [removeListener](../../frontend/tests/setup.ts#L24), [addEventListener](../../frontend/tests/setup.ts#L25), [removeEventListener](../../frontend/tests/setup.ts#L26), [dispatchEvent](../../frontend/tests/setup.ts#L27), [ResizeObserverStub](../../frontend/tests/setup.ts#L32), [observe](../../frontend/tests/setup.ts#L33), [unobserve](../../frontend/tests/setup.ts#L34),
[disconnect](../../frontend/tests/setup.ts#L35).

</details>

<details>
<summary>frontend/tests/work.test.tsx</summary>

[empty](../../frontend/tests/work.test.tsx#L6), [selectTab](../../frontend/tests/work.test.tsx#L15).

</details>
