# Sturgis 01 qualification

The complete core passed its qualification gate on **2026-09-12 at 11:32 UTC**, using Python 3.12.3 on Linux. All **11 implementation tasks** were verified through their declared commands and Codify graph checks. The scope contains the 28 requested Python module directories and the C++ memory engine; it contains no application or UI.

## Executed gate

```sh
.venv/bin/python scripts/qualify.py
```

This command was executed by `cg spec done 5.1`. It built the native worker incrementally, cleared native counters, ran the native protocol suite and gcovr, then ran the complete Python core/integration suite with branch measurement. Both independent line floors passed without rounding or blending languages.

| Measurement | Result |
| --- | --- |
| Python core/integration tests | **328 passed**, 71.23 seconds |
| Native process tests | **72 passed**, 13.73 seconds |
| Total tests in the final gate | **400 passed** |
| Python executable lines | **4,595 / 4,714 — 97.4756045821%** |
| Native executable lines | **494 / 505 — 97.8217821782%** |
| Required line floor | **90% independently for each language** |
| Python branches | 1,811 / 1,948 — 92.97% |
| Native branches | 1,510 / 2,977 — 50.72% |

Branch coverage is reported separately and has no 90% floor in this spec. The Python terminal display combines lines and branches; its combined percentage is not the executable-line percentage above. All first-party Python source is included, including modules without imported execution. Native coverage includes production source and excludes vendored/compiler/library code.

## Evidence

- [Qualification record](../evidence/sturgis-01/qualification.json)
- [Qualified source hashes](../evidence/sturgis-01/source-manifest.json)
- [Python coverage JSON](../evidence/sturgis-01/python-coverage.json) and [XML](../evidence/sturgis-01/python-coverage.xml)
- [Native coverage JSON](../evidence/sturgis-01/native-coverage.json), [XML](../evidence/sturgis-01/native-coverage.xml) and [text](../evidence/sturgis-01/native-coverage.txt)

The source-manifest SHA-256 is `d9aa43856c612cd41930452624afc76a6ebe594485a3e41d32e1fad5787e5f16`. The gate checked that production/build source did not change during its run. A later run invalidates the previous success record before starting. The manifest also identifies vendored native headers for source reproducibility; those external lines remain outside the coverage denominator.

## Behavioral evidence

| Area | Concrete exercised behavior |
| --- | --- |
| Contracts and state | Strict parsing, bounded frames, revision conflicts, transactions, deduplication, scope boundaries, artifact integrity and durable cursors. |
| Cognition | Profile roots, watcher projections, cumulative goal budgets, commitments, proactive/context attention separation, dependency plans and evidence-checked transitions. |
| Capabilities | Real rooted file changes, namespace-confined subprocesses, process-tree cancellation, isolated Python plugin lifecycle/migration/rollback, versioned skills and normalized action receipts. |
| Memory | Actual C++ worker, SQLite persistence, temporal records, lexical/concept/vector retrieval, incompatible embeddings, generations, leases, retractions, pruning and daily consolidation recovery. |
| Scheduler and children | Shared allocations, goal pause enforcement, late factual usage, ancestor fences, durable waits, process-exit receipts, DST-aware schedules, real native scouts and scoped model children. |
| Runtime | Real HTTP streaming and framed channel sockets; output before slow work finishes; steering during a real process; immutable context and explicit unread ranges; prior conversation continuity; scheduled work through the common loop. |
| Failure recovery | Native `SIGSTOP` during retrieval/publication, single-slot sleep preemption/resume, owner `SIGKILL`, atomic-admission rollback, legacy admission-gap recovery, durable outbox replay, and real effects with lost acknowledgments remaining indeterminate without replay. |
| Outcome admission | Truncated/refused provider responses preserve partial text and dispatch no tools; unknown token usage remains reserved; reconciliation requires exact trusted causal observations rather than generated narratives. |
| Evolution | Exact source staging, protected build/shadow/canary checks, real isolated candidate execution, completion-receipt admission, activation, observation, reversal and cancellation/recovery. |
| Gate correctness | Twenty-six isolated gate tests reject below-floor results, omitted sources, out-of-scope modules, third-party coverage inflation, report/subprocess failures and stale success records. |

Remote-provider fixtures exercise the actual HTTP adapter against a controlled local server. Coverage-gate unit tests use explicitly isolated report-producing subprocess doubles. Neither substitutes for the local integrated runtime, native memory or real effect tests listed above.

## Supported boundaries

This qualification covers the concrete Linux/Python/SQLite/C++ backend. Process and plugin execution require bubblewrap, `prlimit` and permitted Linux namespaces. Other operating-system isolation backends are not implemented.

The provider adapter implements configurable chat/SSE HTTP, and ACP implements its version 1 text-session facade. No live paid provider, external production client, hosted service or deployment was exercised. External delivery remains unconfirmed until the transport supplies a negotiated receipt.

Native retrieval uses bounded exact scanning. The default sleep consolidator removes exact repetition; model-based consolidation can be injected under the same evidence and publication contract. Large-scale retrieval latency and learning-quality improvements are not benchmark claims from this test run.

Behavior activation selects immutable source for fresh execution; it does not hotpatch the current interpreter. Persistent behavior-state migrations are rejected by this backend. Custom/plugin executors without delegated workspace enforcement reject child dispatch. Missing provider usage retains its token reservation until an authoritative receipt is supplied.

The core exposes programmatic mechanisms and explicit policy/grant configuration. Application shells, UI and next-spec product features remain outside sturgis-01.

## Verification entry points and boundary fixtures

The `main` entry point in [qualify.py](../scripts/qualify.py) orchestrates the aggregate gate; `main` in [verify_native.py](../scripts/verify_native.py) builds and measures the native process. These are local developer commands, not application services.

The following source map explains the boundary suite in [test_boundaries.py](../tests/core/test_boundaries.py). Fixture functions are test internals and carry no public API compatibility promise. Other behavioral suites are described in the evidence table above and included by the aggregate gate.

| Tests and helpers | Exercised evidence |
| --- | --- |
| `setup_channels`; `test_input_admission_and_scoping` | Real SQLite journal and channel binding; deduplication, conflicting reuse, scope/participant isolation, validation, cancellation admission and incorporation requiring a context ID. |
| `test_stream_channel_and_durable_delivery`; nested `receiver` | Real loopback TCP with framed JSON, ordered delivery, outbox deduplication, inbound frames, EOF and adapter registration. Sends remain unconfirmed until explicitly acknowledged. |
| `test_delivery_claim_recovery_retry_and_replay` | Explicit timestamps exercise lease expiry, duplicate-possible recovery, stale claims, retry delay, exhausted attempts, ordering and capability-gated replay/acknowledgment. This particular test simulates lease recovery without restarting an OS process. |
| `test_missing_adapter_records_retry` | An unavailable adapter produces a persisted retry state. |
| `wire_provider`; nested `serve`; `descriptor`; `collect_provider` | Loopback HTTP/SSE fixtures capture requests, emit responses or stall; helpers construct descriptors and collect normalized events. No live remote provider is contacted. |
| `test_real_http_stream_and_tool_serialization` | Real adapter text streaming, fragmented tool calls, usage/terminal events, bearer serialization and system-role preservation. |
| `test_provider_errors` | HTTP failure, incomplete streams, invalid events, upstream errors, malformed JSON, bounded input and unsupported tool-function names. |
| `test_provider_cancel_and_validation`; nested `run` and `collect` | Bounded cancellation of a stalled stream, existing cancellation, invalid roles, unsupported tools, missing credentials, connection failure and timeout validation. |
| `test_catalog_freshness_and_versions` | Durable provider registration and observations, capability/context bounds, expiry and increasing observation revisions. |

## Test source index

This navigation index maps the qualified test suite and its fixtures to their definitions. Test/helper names identify source locations; the executed results and behavioral claims are stated above. Nested helpers are not core extension APIs.

<details>

<summary>tests/core/test_boundaries.py</summary>


- [setup_channels](../tests/core/test_boundaries.py#L18)
- [test_input_admission_and_scoping](../tests/core/test_boundaries.py#L24)
- [test_stream_channel_and_durable_delivery](../tests/core/test_boundaries.py#L43)
- [receiver](../tests/core/test_boundaries.py#L45)
- [test_delivery_claim_recovery_retry_and_replay](../tests/core/test_boundaries.py#L75)
- [test_missing_adapter_records_retry](../tests/core/test_boundaries.py#L106)
- [wire_provider](../tests/core/test_boundaries.py#L114)
- [serve](../tests/core/test_boundaries.py#L116)
- [descriptor](../tests/core/test_boundaries.py#L137)
- [collect_provider](../tests/core/test_boundaries.py#L141)
- [test_real_http_stream_and_tool_serialization](../tests/core/test_boundaries.py#L145)
- [test_provider_errors](../tests/core/test_boundaries.py#L166)
- [test_provider_cancel_and_validation](../tests/core/test_boundaries.py#L172)
- [run](../tests/core/test_boundaries.py#L175)
- [collect](../tests/core/test_boundaries.py#L178)
- [test_catalog_freshness_and_versions](../tests/core/test_boundaries.py#L187)
- [test_acp_facade_correlation_delivery_and_cancel](../tests/core/test_boundaries.py#L204)
- [emit](../tests/core/test_boundaries.py#L206)
- [submit](../tests/core/test_boundaries.py#L207)
- [cancel](../tests/core/test_boundaries.py#L212)
- [req](../tests/core/test_boundaries.py#L215)

</details>

<details>

<summary>tests/core/test_capabilities.py</summary>


- [journal](../tests/core/test_capabilities.py#L28)
- [authority](../tests/core/test_capabilities.py#L34)
- [authorize](../tests/core/test_capabilities.py#L35)
- [dispatcher](../tests/core/test_capabilities.py#L46)
- [context](../tests/core/test_capabilities.py#L51)
- [descriptor](../tests/core/test_capabilities.py#L55)
- [manifest](../tests/core/test_capabilities.py#L59)
- [package](../tests/core/test_capabilities.py#L64)
- [test_capability_contract_rejects_invalid_descriptors](../tests/core/test_capabilities.py#L102)
- [test_plugin_manifest_validation](../tests/core/test_capabilities.py#L111)
- [test_sdk_worker_protocol_and_schemas](../tests/core/test_capabilities.py#L116)
- [test_rooted_file_operations_preimages_and_symlinks](../tests/core/test_capabilities.py#L146)
- [test_file_limits_and_mutation_failures](../tests/core/test_capabilities.py#L177)
- [test_real_process_limits_environment_and_namespace](../tests/core/test_capabilities.py#L209)
- [test_process_tree_cancellation_stops_descendant_effect](../tests/core/test_capabilities.py#L236)
- [test_dispatch_transaction_schema_dedupe_and_real_file_effect](../tests/core/test_capabilities.py#L249)
- [test_dispatch_process_target_error_and_file_operations](../tests/core/test_capabilities.py#L275)
- [test_dispatch_cancellation_failures_recovery_and_reconciliation](../tests/core/test_capabilities.py#L294)
- [slow](../tests/core/test_capabilities.py#L297)
- [bad](../tests/core/test_capabilities.py#L300)
- [large](../tests/core/test_capabilities.py#L302)
- [error](../tests/core/test_capabilities.py#L304)
- [test_plugin_worker_migration_commands_rollback_restart](../tests/core/test_capabilities.py#L344)
- [test_plugin_filesystem_and_network_grants_are_real](../tests/core/test_capabilities.py#L372)
- [test_plugin_failure_quarantine_and_version_integrity](../tests/core/test_capabilities.py#L396)
- [test_plugin_dispatch_draining_cancellation_and_malformed_response](../tests/core/test_capabilities.py#L435)
- [skill](../tests/core/test_capabilities.py#L464)
- [test_skills_scoped_discovery_selection_revisions_and_rollback](../tests/core/test_capabilities.py#L469)
- [test_skill_synthesis_uses_actual_scoped_tool_outcomes](../tests/core/test_capabilities.py#L503)
- [test_invalid_skill_packages](../tests/core/test_capabilities.py#L540)

</details>

<details>

<summary>tests/core/test_cognition.py</summary>


- [journal](../tests/core/test_cognition.py#L23)
- [observation](../tests/core/test_cognition.py#L29)
- [active_goal](../tests/core/test_cognition.py#L39)
- [candidate](../tests/core/test_cognition.py#L45)
- [step](../tests/core/test_cognition.py#L50)
- [test_profiles_roots_revisions_and_restart](../tests/core/test_cognition.py#L54)
- [test_profile_invalid_boundaries](../tests/core/test_cognition.py#L77)
- [test_awareness_lifecycle_deduplication_and_stale_observations](../tests/core/test_cognition.py#L116)
- [test_scoped_outcome_checks_and_paged_evidence](../tests/core/test_cognition.py#L149)
- [test_goal_states_evidence_and_atomic_revision](../tests/core/test_cognition.py#L170)
- [test_goal_reservations_cumulative_usage_and_overage](../tests/core/test_cognition.py#L199)
- [test_goal_competing_reservations_and_reopen](../tests/core/test_cognition.py#L241)
- [reserve](../tests/core/test_cognition.py#L243)
- [test_commitments_evidence_progress_and_overdue](../tests/core/test_cognition.py#L257)
- [test_commitment_registrar_contract_is_atomic_before_release](../tests/core/test_cognition.py#L283)
- [register](../tests/core/test_cognition.py#L285)
- [cancel](../tests/core/test_cognition.py#L287)
- [test_all_trigger_shapes](../tests/core/test_cognition.py#L319)
- [test_initiative_evidence_scores_expiry_and_deduplication](../tests/core/test_cognition.py#L327)
- [test_attention_proactive_policies_and_notification_accounting](../tests/core/test_cognition.py#L343)
- [test_attention_quiet_hours_and_validation](../tests/core/test_cognition.py#L365)
- [test_context_attention_preserves_required_inputs_and_explicit_deferrals](../tests/core/test_cognition.py#L378)
- [test_planner_routes_dependencies_assignments_and_history](../tests/core/test_cognition.py#L394)
- [test_planner_invalid_graphs_and_revision_changes](../tests/core/test_cognition.py#L433)

</details>

<details>

<summary>tests/core/test_context.py</summary>


- [test_packet_labels_roles_ranges_and_immutability](../tests/core/test_context.py#L7)
- [test_unicode_chunk_cursor_and_optional_deferral](../tests/core/test_context.py#L18)
- [test_causal_tool_pair_and_budget_reservation](../tests/core/test_context.py#L35)
- [test_invalid_blocks](../tests/core/test_context.py#L50)
- [test_invalid_packets_and_instruction_overflow](../tests/core/test_context.py#L54)

</details>

<details>

<summary>tests/core/test_evolution.py</summary>


- [journal](../tests/core/test_evolution.py#L21)
- [make_dispatcher](../tests/core/test_evolution.py#L27)
- [authorize](../tests/core/test_evolution.py#L28)
- [checks](../tests/core/test_evolution.py#L37)
- [setup](../tests/core/test_evolution.py#L45)
- [stage](../tests/core/test_evolution.py#L60)
- [qualify](../tests/core/test_evolution.py#L66)
- [execute_selected](../tests/core/test_evolution.py#L72)
- [test_real_build_shadow_canary_activation_and_rollback](../tests/core/test_evolution.py#L79)
- [test_candidate_cannot_replace_governing_checks_or_claim_success](../tests/core/test_evolution.py#L108)
- [test_default_authority_and_incompatible_migration_fail_explicitly](../tests/core/test_evolution.py#L130)
- [test_competing_candidate_cas_and_baseline_restoration](../tests/core/test_evolution.py#L154)
- [test_watchdog_requires_exact_runtime_evidence_and_restores_selected_code](../tests/core/test_evolution.py#L173)
- [test_diagnosis_uses_actual_trace_and_never_becomes_verified_truth](../tests/core/test_evolution.py#L189)
- [test_real_timeout_cancellation_and_reopen_recovery](../tests/core/test_evolution.py#L204)
- [test_source_tamper_policy_changes_and_missing_evaluation](../tests/core/test_evolution.py#L230)
- [test_passing_verdict_without_completion_receipt_cannot_activate](../tests/core/test_evolution.py#L259)
- [test_edit_validation_scopes_evidence_preimages_and_deletion](../tests/core/test_evolution.py#L277)
- [test_snapshot_bounds_and_copy_integrity](../tests/core/test_evolution.py#L307)
- [test_invalid_immutable_checks](../tests/core/test_evolution.py#L327)
- [test_harness_requires_real_checks_and_allowed_programs](../tests/core/test_evolution.py#L332)

</details>

<details>

<summary>tests/core/test_foundation.py</summary>


- [test_strict_protocol](../tests/core/test_foundation.py#L14)
- [test_tool_contracts](../tests/core/test_foundation.py#L28)
- [test_journal_atomicity_restart_scope_and_dedupe](../tests/core/test_foundation.py#L39)
- [test_competing_revisions](../tests/core/test_foundation.py#L85)
- [update](../tests/core/test_foundation.py#L88)
- [test_artifact_integrity_and_scope](../tests/core/test_foundation.py#L98)
- [test_incremental_frames](../tests/core/test_foundation.py#L117)
- [test_async_frames_real_socket](../tests/core/test_foundation.py#L131)
- [echo](../tests/core/test_foundation.py#L132)

</details>

<details>

<summary>tests/core/test_memory.py</summary>


- [record](../tests/core/test_memory.py#L24)
- [client](../tests/core/test_memory.py#L34)
- [journal](../tests/core/test_memory.py#L42)
- [seed](../tests/core/test_memory.py#L48)
- [result_ids](../tests/core/test_memory.py#L54)
- [test_client_real_multiplexing_retrieval_and_scoped_context](../tests/core/test_memory.py#L58)
- [test_deadline_cancel_and_late_frame_drain_preserve_responsiveness](../tests/core/test_memory.py#L83)
- [foreground](../tests/core/test_memory.py#L88)
- [test_capacity_and_writer_wait_deadlines](../tests/core/test_memory.py#L108)
- [test_worker_death_restart_and_unknown_dispatched_mutation](../tests/core/test_memory.py#L127)
- [test_snapshot_freshness_partial_vector_status_and_release](../tests/core/test_memory.py#L139)
- [test_client_configuration](../tests/core/test_memory.py#L158)
- [test_invalid_requests_and_startup_failure](../tests/core/test_memory.py#L163)
- [test_bad_peer_frames_fail_closed_without_deadlock](../tests/core/test_memory.py#L181)
- [test_startup_deadline_and_closing_stalled_worker](../tests/core/test_memory.py#L196)
- [test_event_replay_exact_sources_rejections_and_no_recursive_ingestion](../tests/core/test_memory.py#L209)
- [test_ingestion_receipt_replay_after_cursor_loss](../tests/core/test_memory.py#L234)
- [test_native_event_rejection_is_durable_and_acknowledgment_idempotent](../tests/core/test_memory.py#L245)
- [test_unavailable_ingestion_retains_cursor](../tests/core/test_memory.py#L257)
- [test_ingestor_configuration_and_invalid_source](../tests/core/test_memory.py#L271)
- [test_sleep_default_consolidation_pruning_and_same_day_idempotency](../tests/core/test_memory.py#L282)
- [test_sleep_admission_and_ingestion_are_bounded](../tests/core/test_memory.py#L305)
- [admission](../tests/core/test_memory.py#L310)
- [test_sleep_candidate_evidence_rejection_and_partial_truth](../tests/core/test_memory.py#L327)
- [consolidate](../tests/core/test_memory.py#L329)
- [test_sleep_conflict_rebases_next_attempt_and_prunes_abandoned](../tests/core/test_memory.py#L339)
- [consolidate](../tests/core/test_memory.py#L342)
- [test_sleep_preserves_prune_work_added_during_another_cycle](../tests/core/test_memory.py#L358)
- [consolidate](../tests/core/test_memory.py#L367)
- [test_sleep_lost_cycle_ownership_cannot_publish](../tests/core/test_memory.py#L378)
- [takeover](../tests/core/test_memory.py#L380)
- [test_sleep_cancel_then_resume_and_parallel_foreground](../tests/core/test_memory.py#L392)
- [consolidate](../tests/core/test_memory.py#L395)
- [test_sleep_publication_receipt_recovers_lost_ack](../tests/core/test_memory.py#L416)
- [request](../tests/core/test_memory.py#L421)
- [test_cycle_timeout_and_callback_failures_are_resumable](../tests/core/test_memory.py#L439)
- [slow](../tests/core/test_memory.py#L441)
- [broken](../tests/core/test_memory.py#L448)
- [test_iana_day_boundary_and_invalid_sleep_configuration](../tests/core/test_memory.py#L456)
- [test_candidate_validation_boundaries](../tests/core/test_memory.py#L481)
- [test_default_consolidation_respects_evidence_and_content_bounds](../tests/core/test_memory.py#L488)

</details>

<details>

<summary>tests/core/test_qualification_gate.py</summary>


- [ReportBoundary](../tests/core/test_qualification_gate.py#L11)
- [__init__](../tests/core/test_qualification_gate.py#L14)
- [run](../tests/core/test_qualification_gate.py#L20)
- [python_report](../tests/core/test_qualification_gate.py#L46)
- [native_report](../tests/core/test_qualification_gate.py#L61)
- [gate](../tests/core/test_qualification_gate.py#L73)
- [test_valid_independent_reports_build_native_first_and_preserve_evidence_limits](../tests/core/test_qualification_gate.py#L103)
- [test_relative_python_report_paths_resolve_against_the_qualification_root](../tests/core/test_qualification_gate.py#L115)
- [test_each_language_must_independently_reach_the_unrounded_floor](../tests/core/test_qualification_gate.py#L124)
- [test_empty_executable_denominator_cannot_qualify](../tests/core/test_qualification_gate.py#L138)
- [test_omitted_nested_first_party_python_source_fails_even_with_high_totals](../tests/core/test_qualification_gate.py#L148)
- [test_omitted_first_party_cpp_source_fails_even_with_high_totals](../tests/core/test_qualification_gate.py#L157)
- [test_new_nested_first_party_cpp_source_must_appear_in_coverage](../tests/core/test_qualification_gate.py#L165)
- [test_third_party_coverage_cannot_conceal_a_native_shortfall](../tests/core/test_qualification_gate.py#L175)
- [test_nonproduction_python_coverage_cannot_conceal_a_shortfall](../tests/core/test_qualification_gate.py#L184)
- [test_failed_scope_check_invalidates_a_previous_success_report](../tests/core/test_qualification_gate.py#L194)
- [test_missing_required_core_module_fails_before_commands](../tests/core/test_qualification_gate.py#L204)
- [test_unexpected_top_level_agent_modules_fail_before_commands](../tests/core/test_qualification_gate.py#L212)
- [test_application_folders_fail_before_commands](../tests/core/test_qualification_gate.py#L223)
- [test_failed_verification_subprocess_cannot_publish_qualification](../tests/core/test_qualification_gate.py#L231)
- [test_missing_language_report_cannot_publish_qualification](../tests/core/test_qualification_gate.py#L241)

</details>

<details>

<summary>tests/core/test_runtime.py</summary>


- [ControlledProvider](../tests/core/test_runtime.py#L17)
- [__init__](../tests/core/test_runtime.py#L19)
- [stream](../tests/core/test_runtime.py#L23)
- [Capture](../tests/core/test_runtime.py#L30)
- [__init__](../tests/core/test_runtime.py#L31)
- [send](../tests/core/test_runtime.py#L32)
- [runtime](../tests/core/test_runtime.py#L35)
- [accept](../tests/core/test_runtime.py#L47)
- [test_lifecycle_identity_lock_and_invalid_input](../tests/core/test_runtime.py#L52)
- [test_failed_inference_keeps_visible_output_and_truthful_terminal](../tests/core/test_runtime.py#L85)
- [test_cancellation_fences_queued_work_and_waiting](../tests/core/test_runtime.py#L98)
- [test_large_input_has_exact_deferred_ranges_and_budget_failure](../tests/core/test_runtime.py#L113)
- [test_recovery_interrupts_prior_running_work_without_repeating_effect](../tests/core/test_runtime.py#L129)
- [test_supervised_worker_failure_is_recorded_and_restarted](../tests/core/test_runtime.py#L149)
- [fail_once](../tests/core/test_runtime.py#L152)
- [test_invalid_limits_workspace_and_insufficient_context](../tests/core/test_runtime.py#L168)
- [test_explicit_evolution_configuration_has_no_implicit_source_authority](../tests/core/test_runtime.py#L180)
- [test_scheduler_constraints_control_real_child_file_and_process_effects](../tests/core/test_runtime.py#L198)
- [unsafe](../tests/core/test_runtime.py#L213)
- [call](../tests/core/test_runtime.py#L215)

</details>

<details>

<summary>tests/core/test_scheduling.py</summary>


- [journal](../tests/core/test_scheduling.py#L23)
- [scheduler](../tests/core/test_scheduling.py#L30)
- [admitted](../tests/core/test_scheduling.py#L34)
- [claim](../tests/core/test_scheduling.py#L39)
- [active_goal](../tests/core/test_scheduling.py#L45)
- [checks](../tests/core/test_scheduling.py#L50)
- [children](../tests/core/test_scheduling.py#L55)
- [test_admission_readiness_priority_capacity_global_scope_and_recovery](../tests/core/test_scheduling.py#L59)
- [test_goal_child_grants_cumulative_usage_and_retention](../tests/core/test_scheduling.py#L81)
- [test_late_usage_overage_and_descendant_cancellation_preserve_claims](../tests/core/test_scheduling.py#L112)
- [test_crash_unknown_claim_never_replayed_or_zero_charged](../tests/core/test_scheduling.py#L133)
- [test_paused_goal_fences_new_execution_but_allows_receipts_and_cleanup](../tests/core/test_scheduling.py#L147)
- [test_submission_scope_constraints_and_invalid_input](../tests/core/test_scheduling.py#L176)
- [test_actual_commitment_wait_transaction_restart_and_expiry](../tests/core/test_scheduling.py#L226)
- [test_all_scoped_wake_event_families](../tests/core/test_scheduling.py#L257)
- [test_durable_park_wake_requeue_and_cancellation](../tests/core/test_scheduling.py#L279)
- [test_schedule_catchup_pause_resume_skip_and_bounded_replay](../tests/core/test_scheduling.py#L289)
- [utc](../tests/core/test_scheduling.py#L316)
- [test_calendar_dst_gap_fold_and_schedule_validation](../tests/core/test_scheduling.py#L320)
- [test_actual_tool_receipts_child_workspace_and_cancel_evidence](../tests/core/test_scheduling.py#L334)
- [test_child_evidence_partial_rejected_accepted_and_idempotent_submission](../tests/core/test_scheduling.py#L357)
- [test_child_messages_wait_cycles_resume_and_expiry](../tests/core/test_scheduling.py#L381)
- [test_child_constraints_depth_liveness_and_retained_identity](../tests/core/test_scheduling.py#L415)
- [test_memory_scout_uses_real_native_reads_and_scope_fence](../tests/core/test_scheduling.py#L438)
- [test_process_exit_wait_uses_actual_executor_receipt_not_process_shaped_data](../tests/core/test_scheduling.py#L467)
- [shaped](../tests/core/test_scheduling.py#L475)

</details>

<details>

<summary>tests/integration/test_continuity.py</summary>


- [WireModel](../tests/integration/test_continuity.py#L19)
- [__init__](../tests/integration/test_continuity.py#L20)
- [handle](../tests/integration/test_continuity.py#L23)
- [proposal](../tests/integration/test_continuity.py#L45)
- [eventually](../tests/integration/test_continuity.py#L50)
- [check](../tests/integration/test_continuity.py#L51)
- [harness](../tests/integration/test_continuity.py#L60)
- [collect](../tests/integration/test_continuity.py#L65)
- [send](../tests/integration/test_continuity.py#L91)
- [test_real_world_effect_fixed_context_native_memory_and_wire_delivery](../tests/integration/test_continuity.py#L97)
- [test_steering_during_real_process_preserves_partial_output_and_revokes_effects](../tests/integration/test_continuity.py#L127)
- [test_pending_input_and_visible_text_remain_responsive_during_slow_provider](../tests/integration/test_continuity.py#L149)
- [test_restart_replays_durable_unrouted_input_and_existing_outbox](../tests/integration/test_continuity.py#L164)
- [test_memory_process_missing_does_not_block_foreground](../tests/integration/test_continuity.py#L177)
- [test_acp_prompts_use_the_same_runtime_and_output_path](../tests/integration/test_continuity.py#L187)
- [emit](../tests/integration/test_continuity.py#L190)
- [test_real_channel_ingress_is_bound_and_can_be_reattached_after_invalid_input](../tests/integration/test_continuity.py#L203)
- [test_real_model_child_uses_its_workspace_and_evidence_admission](../tests/integration/test_continuity.py#L219)
- [test_large_tool_body_is_durable_and_explicitly_deferred_from_context](../tests/integration/test_continuity.py#L245)
- [large](../tests/integration/test_continuity.py#L247)
- [test_due_schedule_runs_through_common_runtime_once](../tests/integration/test_continuity.py#L260)
- [test_provider_limits_preserve_partial_text_and_do_not_execute_proposals](../tests/integration/test_continuity.py#L275)
- [test_unreported_provider_tokens_remain_reserved_until_receipt](../tests/integration/test_continuity.py#L290)

</details>

<details>

<summary>tests/integration/test_evidence_admission.py</summary>


- [observer_payload](../tests/integration/test_evidence_admission.py#L16)
- [observe](../tests/integration/test_evidence_admission.py#L22)
- [inspect_attempt](../tests/integration/test_evidence_admission.py#L26)
- [uncertain_effect](../tests/integration/test_evidence_admission.py#L45)
- [lost_acknowledgement](../tests/integration/test_evidence_admission.py#L53)
- [reject](../tests/integration/test_evidence_admission.py#L81)
- [test_observed_real_effect_reconciles_exact_attempt_and_settles_attested_usage](../tests/integration/test_evidence_admission.py#L94)
- [test_narrative_or_untrusted_source_cannot_authorize_completion](../tests/integration/test_evidence_admission.py#L117)
- [test_observation_must_attest_every_proposed_result_field](../tests/integration/test_evidence_admission.py#L131)
- [test_observation_envelope_and_usage_are_exact](../tests/integration/test_evidence_admission.py#L143)
- [test_unrelated_causal_event_and_cross_scope_observation_are_rejected](../tests/integration/test_evidence_admission.py#L150)
- [test_mixed_missing_duplicate_and_malformed_evidence_reject_atomically](../tests/integration/test_evidence_admission.py#L167)
- [test_unattested_or_invalid_usage_cannot_settle_the_attempt](../tests/integration/test_evidence_admission.py#L181)
- [test_terminal_observation_can_preserve_unknown_effect_certainty](../tests/integration/test_evidence_admission.py#L190)
- [test_observed_effect_after_actual_owner_crash_keeps_unmeasured_usage_unsettled](../tests/integration/test_evidence_admission.py#L234)

</details>

<details>

<summary>tests/integration/test_provider_outcomes.py</summary>


- [wire](../tests/integration/test_provider_outcomes.py#L13)
- [finish](../tests/integration/test_provider_outcomes.py#L29)
- [delta](../tests/integration/test_provider_outcomes.py#L33)
- [collect](../tests/integration/test_provider_outcomes.py#L37)
- [test_terminal_cause_preserves_visible_text](../tests/integration/test_provider_outcomes.py#L51)
- [test_truncated_or_filtered_calls_are_never_proposals](../tests/integration/test_provider_outcomes.py#L60)
- [test_refusal_text_overrides_stop_and_withholds_existing_tool_proposal](../tests/integration/test_provider_outcomes.py#L68)
- [test_valid_fragmented_call_and_final_usage_details](../tests/integration/test_provider_outcomes.py#L76)
- [test_malformed_wire_shapes_fail_before_any_tool_proposal](../tests/integration/test_provider_outcomes.py#L114)
- [test_tool_fragments_have_aggregate_byte_part_name_and_call_bounds](../tests/integration/test_provider_outcomes.py#L131)
- [test_conflicting_terminal_identity_and_usage_are_rejected](../tests/integration/test_provider_outcomes.py#L144)
- [test_every_tool_is_validated_before_any_tool_event_is_admitted](../tests/integration/test_provider_outcomes.py#L150)
- [test_identical_final_usage_replay_is_not_double_counted](../tests/integration/test_provider_outcomes.py#L160)
- [test_invalid_provider_resource_limits](../tests/integration/test_provider_outcomes.py#L170)

</details>

<details>

<summary>tests/integration/test_runtime_faults.py</summary>


- [test_single_slot_sleep_preempts_dispatched_native_publish](../tests/integration/test_runtime_faults.py#L16)
- [consolidate](../tests/integration/test_runtime_faults.py#L20)
- [test_sigstop_native_query_keeps_admission_and_wire_output_responsive](../tests/integration/test_runtime_faults.py#L63)
- [start_owner](../tests/integration/test_runtime_faults.py#L157)
- [kill_owner](../tests/integration/test_runtime_faults.py#L177)
- [reopen](../tests/integration/test_runtime_faults.py#L182)
- [test_killed_owner_replays_ingress_gap_and_existing_outbox_once](../tests/integration/test_runtime_faults.py#L189)
- [test_killed_owner_preserves_unknown_effect_and_does_not_reinvoke](../tests/integration/test_runtime_faults.py#L213)
- [test_killed_owner_between_admission_and_turn_update_releases_single_slot](../tests/integration/test_runtime_faults.py#L253)
- [test_killed_owner_background_sleep_cannot_hold_foreground_capacity](../tests/integration/test_runtime_faults.py#L272)

</details>

<details>

<summary>tests/native/test_memory_worker.py</summary>


- [record](../tests/native/test_memory_worker.py#L20)
- [Worker](../tests/native/test_memory_worker.py#L32)
- [__init__](../tests/native/test_memory_worker.py#L33)
- [exact](../tests/native/test_memory_worker.py#L41)
- [receive](../tests/native/test_memory_worker.py#L53)
- [raw](../tests/native/test_memory_worker.py#L58)
- [call](../tests/native/test_memory_worker.py#L63)
- [mutate](../tests/native/test_memory_worker.py#L69)
- [ingest](../tests/native/test_memory_worker.py#L73)
- [begin](../tests/native/test_memory_worker.py#L76)
- [publish](../tests/native/test_memory_worker.py#L81)
- [lease](../tests/native/test_memory_worker.py#L84)
- [close](../tests/native/test_memory_worker.py#L89)
- [worker](../tests/native/test_memory_worker.py#L102)
- [ids](../tests/native/test_memory_worker.py#L108)
- [test_durable_revision_evidence_and_idempotency](../tests/native/test_memory_worker.py#L112)
- [test_lexical_concept_vector_scores_and_time](../tests/native/test_memory_worker.py#L142)
- [test_embedding_spaces_are_explicit_and_scoped](../tests/native/test_memory_worker.py#L169)
- [test_generation_publication_leases_retractions_and_restart](../tests/native/test_memory_worker.py#L186)
- [test_stage_atomicity_parent_copy_and_canonical_precedence](../tests/native/test_memory_worker.py#L228)
- [test_snapshot_watermark_validation_and_lease_expiration](../tests/native/test_memory_worker.py#L257)
- [test_bounded_prune_protects_leases_current_and_canonical_evidence](../tests/native/test_memory_worker.py#L275)
- [test_database_projection_failure_rolls_back_and_blocks_publication](../tests/native/test_memory_worker.py#L312)
- [test_concurrent_workers_revision_and_parent_cas](../tests/native/test_memory_worker.py#L333)
- [test_crash_during_staging_recovers_atomic_batch_and_receipt](../tests/native/test_memory_worker.py#L346)
- [test_invalid_records_have_no_effect](../tests/native/test_memory_worker.py#L389)
- [test_invalid_operations](../tests/native/test_memory_worker.py#L405)
- [test_strict_envelope](../tests/native/test_memory_worker.py#L414)
- [test_malformed_json_does_not_desynchronize](../tests/native/test_memory_worker.py#L424)
- [test_bad_frame_terminates_cleanly](../tests/native/test_memory_worker.py#L431)
- [test_multiple_frames_and_fragmented_input](../tests/native/test_memory_worker.py#L437)
- [test_startup_errors](../tests/native/test_memory_worker.py#L449)
- [test_response_bound_is_truthful](../tests/native/test_memory_worker.py#L463)
- [test_query_scan_bound_reports_partial](../tests/native/test_memory_worker.py#L472)

</details>

## Current regression navigation

The links below were added during sturgis-02. They identify current core fixture
cleanup regressions exercised by the [current full-system gate](sturgis-02/qualification.md);
they were not part of the historical sturgis-01 measurements in this report.

- [test_harness_assertion_failure_closes_blocked_provider_before_waiting_for_servers](../tests/integration/test_continuity.py#L305)
- [fail_after_partial_output](../tests/integration/test_continuity.py#L310)
