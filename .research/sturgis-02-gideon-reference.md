# Gideon source reference for Sturgis 02

Inspected 2026-09-12. This is a read-only source audit and transfer analysis. No donor tests, builds, dependency installation, services, model calls, credentials, runtime homes, or deployment state were accessed. Only this research artifact was created in Sturgis. Source presence is distinct from operational qualification throughout.

## Useful checkouts and provenance

| Current path | Observed metadata | Reference value |
|---|---|---|
| `/root/Gideon/Gideon-ai` | Independent Git root; HEAD `70b043fb8f9b653a25b4e268fdea3cb8047c0ba2`, 2026-09-03. Modified core/frontend source plus additional untracked hosted runtime, cloud shell and deployment source. | Full consumer surface plus hosted additions. Its older commit does not make its additional hosted work redundant. |
| `/root/Gideon/tmp/Gideon-ai` | Independent Git root; HEAD `cf3fac6c256dd89ff1177fd81b94e138ce08a4a3`, 2026-09-09; clean Git status at inspection. | Newer general runtime, browser/computer-use, extension and consumer UI source. It does not contain the other tree's hosted additions. |
| `/root/Gideon/Gideon-ai-apps` | Git resolves to parent `/root/Gideon`, whose branch has no commits. App files appear as staged source there. Exactly 52 direct child app manifests. | External provider/channel/tool/skill/sync/application catalog; no installed-app or independent release status inferred. |
| `/root/Gideon/gideon-cloud` | Same parent Git root with no commits; this subtree is untracked. Actual Go central, reconciler and edge source exists. | Hosted identity, allocation, routing, credentials and runtime lifecycle boundaries. |
| `/root/private-neo-v1/gideon-agent` | No Git repository resolves. Separate `gideon_agent` interface, full `gideon` backend and 52 copied provider bundles. | Integration with a host application through a separately owned runtime process. |

A byte comparison of `src/gideon/**/*.py`, excluding `tests_fixtures`, found 1,084 Python files in the September 3 tree and 1,104 in the September 9 tree. Between them, 223 common paths differ, 16 exist only in the former and 36 only in the latter. All 1,104 inspected September 9 runtime Python files are byte-identical to the corresponding files in the reusable extraction. This is a Python-source comparison, not whole-repository identity.

Newer-only source includes browser grants/mirror/plans, computer overlay and macOS permission handling, Docker/Lima sandbox providers, self-QA evidence/watch, and provider failure presentation. The older working tree separately contributes hosted integration. Neither checkout should be assumed universally authoritative.

The extraction [README:3](/root/private-neo-v1/gideon-agent/README.md:3) names the September 9 donor, while [AGENTS:3](/root/private-neo-v1/gideon-agent/AGENTS.md:3) retains an older relative source description. The current byte comparison supports the README's runtime provenance.

Applicable AGENTS instructions were read. Their implementation workflows did not broaden this read-only task. Codify root/survey was attempted before targeted reads: most named roots reported no usable project; the apps tree resolved the parent index and returned an app survey. No graph initialization, memory retrieval, task claim or donor repair was performed. Subsequent findings use exact current files.

## Consumer feature breadth

Every row below is source-present unless an explicit unsupported boundary is stated. None was executed or qualified during this audit.

| Capability | Current source behavior and boundary | Evidence |
|---|---|---|
| Full workspace UI | Home, Chat, Projects, Knowledge, Tasks, Inbox, Triggers, Files, Artifacts, Terminal, Agents, Tools, Skills, Learning, Prompts, Workflows, Store and Settings. Extra routes include notifications, Discover, loops/code, installed apps and Mission Control. | [navigation:72](/root/Gideon/Gideon-ai/web/src/app/App.tsx:72), [page dispatch:161](/root/Gideon/Gideon-ai/web/src/app/App.tsx:161) |
| Onboarding | Name → import → essential apps → try one → ready. Model/search/speech/channel are separate lanes. Provider readiness and model binding matter beyond merely saving a key. The flow reuses Store/Settings APIs and consent components. | [Onboarding:28](/root/Gideon/tmp/Gideon-ai/web/src/app/Onboarding.tsx:28), [EssentialsStep:31](/root/Gideon/tmp/Gideon-ai/web/src/app/onboarding/EssentialsStep.tsx:31), [lane selection:73](/root/Gideon/tmp/Gideon-ai/web/src/app/onboarding/EssentialsStep.tsx:73) |
| Providers | Model-provider CRUD/test/catalog/search/pull/delete; separate agent-runtime catalog and readiness. Listing reports credential status without returning secret values. App manifests declare capabilities, dependencies and sensitive settings. | [provider APIs:4](/root/Gideon/tmp/Gideon-ai/src/gideon/dashboard/handlers/providers.py:4), [credential status:40](/root/Gideon/tmp/Gideon-ai/src/gideon/dashboard/handlers/providers.py:40), [model manifest:18](/root/Gideon/Gideon-ai-apps/openai-models/app.json:18) |
| Credentials | Shared credential API, opt-in OS keychain and default mode-0600 dotenv backend, explicit backend read/write/migration behavior. Only handling code was read. | [credentials:12](/root/Gideon/tmp/Gideon-ai/src/gideon/config/credentials.py:12) |
| CLI | Installed `gideon` entry point, interactive/noninteractive setup, deployment mode, provider and named credential configuration. CLI setup points to the dashboard for guided first success. | [entry point:220](/root/Gideon/tmp/Gideon-ai/pyproject.toml:220), [CLI setup:105](/root/Gideon/tmp/Gideon-ai/src/gideon/cli_setup.py:105), [noninteractive setup:215](/root/Gideon/tmp/Gideon-ai/src/gideon/cli_setup.py:215) |
| Desktop | Electron shell owns a local gateway child on an assigned port and can connect to a paired gateway. Tray, push-to-talk, native notifications and shutdown handling are present. Packaging declares macOS DMG and Linux AppImage/deb, not a Windows desktop target. | [gateway ownership:104](/root/Gideon/tmp/Gideon-ai/desktop/main.js:104), [spawn:141](/root/Gideon/tmp/Gideon-ai/desktop/main.js:141), [package:9](/root/Gideon/tmp/Gideon-ai/desktop/package.json:9), [bundled backend:65](/root/Gideon/tmp/Gideon-ai/desktop/package.json:65) |
| Chat controls | Scoped approval choices, separate task modes, voice settings, cancellable attachment upload and queued-turn events. These are product concepts to assess individually rather than copy wholesale. | [approval/task choices:127](/root/Gideon/tmp/Gideon-ai/web/src/pages/ChatPage.tsx:127), [attachment cancellation:650](/root/Gideon/tmp/Gideon-ai/web/src/pages/ChatPage.tsx:650) |
| Channels | Discord, email, Slack and Telegram external channel bundles; mail-inbox is separately typed as an inbox provider. Setup/doctor hooks and channel capabilities are manifest-declared. Connected accounts and delivery were not checked. | [Slack manifest:12](/root/Gideon/Gideon-ai-apps/slack-channel/app.json:12), complete manifest inventory below |
| Inbox | List/pending/seen/update/restore, draft/send, favorites/digest, notes, proposals and apply APIs. Draft generation checks whether the source supports replies before spending a model call. | [draft:449](/root/Gideon/tmp/Gideon-ai/src/gideon/dashboard/handlers_inbox.py:449), [send:510](/root/Gideon/tmp/Gideon-ai/src/gideon/dashboard/handlers_inbox.py:510), [proposal apply:927](/root/Gideon/tmp/Gideon-ai/src/gideon/dashboard/handlers_inbox.py:927) |
| Workflows | Author/start/inspect/observe/edit/cancel/pause/steer/resume/rewind/fork; workspace review, outbox and introspection. The newer tree has 32 direct bundled workflow manifests covering research, coding, publishing, knowledge maintenance, review, monitoring and goal pursuit. | [native registration:2](/root/Gideon/tmp/Gideon-ai/src/gideon/apps/native/gideon-workflows/app.json:2), [start:543](/root/Gideon/tmp/Gideon-ai/src/gideon/workflows/service.py:543), [outbox:1350](/root/Gideon/tmp/Gideon-ai/src/gideon/workflows/service.py:1350), [pause/steer:1898](/root/Gideon/tmp/Gideon-ai/src/gideon/workflows/service.py:1898), [rewind/fork:2116](/root/Gideon/tmp/Gideon-ai/src/gideon/workflows/service.py:2116) |
| Knowledge/documents | Keyword/graph/vector retrieval, document readers, source ingestion and knowledge/graph/reading UI. The retriever executes active search arms and fuses results. DOCX/XLSX/PPTX/PDF writers are offered only if their implementations load. | [retriever:95](/root/Gideon/tmp/Gideon-ai/src/gideon/knowledge/retrieval.py:95), [reader:65](/root/Gideon/tmp/Gideon-ai/src/gideon/knowledge/readers.py:65), [source engine:48](/root/Gideon/tmp/Gideon-ai/src/gideon/knowledge/source_engine.py:48), [writers:47](/root/Gideon/tmp/Gideon-ai/src/gideon/documents/registry.py:47) |
| Browser | Gateway and user-browser targets, explicit target resolution, task-scoped grants, step mirroring, browse loop/submission verification, persisted page-watching/flow plans. Restarting an external action still needs effect reconciliation; a persisted cursor alone is not exactly-once execution proof. | [targets:54](/root/Gideon/tmp/Gideon-ai/src/gideon/browse/target.py:54), [grant:144](/root/Gideon/tmp/Gideon-ai/src/gideon/browse/grant.py:144), [mirror:71](/root/Gideon/tmp/Gideon-ai/src/gideon/browse/mirror.py:71), [submission verification:370](/root/Gideon/tmp/Gideon-ai/src/gideon/browse/loop.py:370), [plan record:68](/root/Gideon/tmp/Gideon-ai/src/gideon/browse/plans.py:68) |
| Computer use | Central admission chain, bounded driver subprocess and refreshed/redacted results. macOS actuator source is present. Windows/Linux modules explicitly return unsupported responses and are not implemented actuators. | [dispatch:1](/root/Gideon/tmp/Gideon-ai/src/gideon/computer_use/service.py:1), [driver selection:45](/root/Gideon/tmp/Gideon-ai/src/gideon/computer_use/driver_host.py:45), [Linux refusal:31](/root/Gideon/tmp/Gideon-ai/src/gideon/computer_use/linux_driver.py:31), [Windows refusal:43](/root/Gideon/tmp/Gideon-ai/src/gideon/computer_use/windows_driver.py:43) |
| Media/apps | Model-type bundles include speech recognition/synthesis, diarization, embeddings and image generation. Duplex voice separates transcript confirmation from submission. Minutes declares meetings, linked knowledge/media, summaries/actions; Growth and menu-bar companion are other apps. Provider availability remains unverified. | [duplex:1](/root/Gideon/tmp/Gideon-ai/src/gideon/voice/duplex.py:1), [Minutes:5](/root/Gideon/Gideon-ai-apps/minutes/app.json:5), [meeting routes:17](/root/Gideon/Gideon-ai-apps/minutes/app.json:17) |
| Skills/learning | Local loading, proposals, accept/reject, refinements, aging/archive/restore. Proposals surface in Inbox; external marketplace supplies search/install/uninstall. | [loader:167](/root/Gideon/tmp/Gideon-ai/src/gideon/skills/loader.py:167), [proposals:170](/root/Gideon/tmp/Gideon-ai/src/gideon/skills/proposals.py:170), [accept:447](/root/Gideon/tmp/Gideon-ai/src/gideon/skills/proposals.py:447), [aging:121](/root/Gideon/tmp/Gideon-ai/src/gideon/skills/curator.py:121), [marketplace:13](/root/Gideon/Gideon-ai-apps/skills-sh/app.json:13) |
| App lifecycle | Manifest/SDK boundary, staged installation and scanning, provider registration, setup hooks and injected app UI. Removal distinguishes deactivation, removing code while preserving data, and deleting everything. | [installation:8](/root/Gideon/tmp/Gideon-ai/src/gideon/apps/app_manager.py:8), [removal:16](/root/Gideon/tmp/Gideon-ai/src/gideon/apps/app_manager.py:16), [SDK rule:16](/root/Gideon/Gideon-ai-apps/AGENTS.md:16), [app navigation:93](/root/Gideon/Gideon-ai/web/src/app/App.tsx:93) |
| Host-app interface | `AgentClient` exposes HTTP/SSE/WebSocket, chat/approval/stop; `AgentProcess` owns a child with explicit home/workspace and drains stdout. Backend dashboard modules remain because they own APIs/orchestration. React UI is not required by this interface. | [client:110](/root/private-neo-v1/gideon-agent/src/gideon_agent/client.py:110), [streams:128](/root/private-neo-v1/gideon-agent/src/gideon_agent/client.py:128), [process:182](/root/private-neo-v1/gideon-agent/src/gideon_agent/client.py:182), [extraction boundary:31](/root/private-neo-v1/gideon-agent/README.md:31) |

## Self-hosting and hosted boundaries

Self-host source includes Python package/CLI, SPA, Electron-bundled backend and compose gateway/web/optional-Slack definitions. The compose gateway has a persistent `/data` home and loopback host port binding. Published image names are declarations, not checked registry artifacts. See [compose:13](/root/Gideon/tmp/Gideon-ai/deploy/compose/compose.yaml:13) and [optional Slack:62](/root/Gideon/tmp/Gideon-ai/deploy/compose/compose.yaml:62).

| Hosted boundary | Observed responsibility | Evidence |
|---|---|---|
| Shared central | Authenticated account/session and connection endpoints, shared web serving, route policy, identity-to-runtime lookup. Sleeping requests enqueue wake and return a waking status. | [composition:127](/root/Gideon/gideon-cloud/cmd/central/main.go:127), [account allocation:100](/root/Gideon/gideon-cloud/internal/accounts/handler.go:100), [routing:90](/root/Gideon/gideon-cloud/internal/router/handler.go:90) |
| Separate reconciler | Ensure compute and volume, apply configuration, start pinned image digest, resolve private target, probe readiness and preserve volume identity across replacement. Fly and Blaxel driver source is present; neither was exercised. | [ensure pipeline:377](/root/Gideon/gideon-cloud/internal/fleet/reconciler.go:377), [volume continuity:364](/root/Gideon/gideon-cloud/internal/fleet/reconciler.go:364) |
| Per-runtime edge | Verify signed hop/body and route policy, then proxy to required loopback gateway using local authentication. | [construction:40](/root/Gideon/gideon-cloud/internal/edge/handler.go:40), [verification:76](/root/Gideon/gideon-cloud/internal/edge/handler.go:76) |
| Private Python runtime | Existing gateway with hosted identity/readiness/lifecycle/schedule/usage hooks. `/data` persists home/workspace. Gateway listens on loopback behind the edge. | [runtime image:64](/root/Gideon/Gideon-ai/deploy/hosted/Dockerfile.runtime:64), [gateway/edge entrypoint:55](/root/Gideon/Gideon-ai/deploy/hosted/entrypoint.sh:55), [internal endpoints:43](/root/Gideon/Gideon-ai/src/gideon/hosted/__init__.py:43) |
| Credential delivery | Cloud has encrypted versioned bundles and a key-encryption interface. Runtime verifies allocation identity and loads a separately mounted bundle outside the persistent home. No configured keys/bundles were read. | [key interface:15](/root/Gideon/gideon-cloud/internal/secrets/crypto.go:15), [encryption:65](/root/Gideon/gideon-cloud/internal/secrets/crypto.go:65), [runtime loader:151](/root/Gideon/Gideon-ai/src/gideon/hosted/secrets.py:151) |

Hosted mode hides specific host-management routes: self-update, local token/bootstrap, service install, local discovery, raw logs and inbox restart. Its Store requires curated bundle signatures. These restrictions are specific source facts, not proof that every other feature is reachable or operational. See [deny declarations:27](/root/Gideon/Gideon-ai/src/gideon/hosted/__init__.py:27), [middleware:17](/root/Gideon/Gideon-ai/src/gideon/hosted/mode.py:17), [curated verification:42](/root/Gideon/Gideon-ai/src/gideon/hosted/apps.py:42).

The hosted shell separately narrows navigation to Home, Chat, Work, Intelligence, Connections and Settings plus app routes. Work maps Projects; Intelligence maps Knowledge. Backend source retention therefore does not establish equivalent discoverability to the full consumer shell. This is a product choice to assess, not an automatic Sturgis recommendation. See [hosted navigation:100](/root/Gideon/Gideon-ai/web/src/app/App.tsx:100) and [hosted dispatch:149](/root/Gideon/Gideon-ai/web/src/app/App.tsx:149).

The router's sleeping branch records a wake operation and returns before proxying the original user request. It does not durably admit that original prompt in this branch. Sturgis should distinguish a waking/retry response from accepted input and preserve its own ingress contract. This is a transfer implication, not a reproduced defect. See [sleeping branch:110](/root/Gideon/gideon-cloud/internal/router/handler.go:110).

Root's parallel source inspection identified a drain → active-work recheck → checkpoint sequence in [sleep policy:50](/root/Gideon/gideon-cloud/internal/fleet/sleep_policy.go:50). That sequence is useful for evaluating hosted suspension. Runtime suspension and the user's daily memory-consolidation sleep are separate operations and should not share an ambiguous state label.

## Manifest counts and inventory

Exactly **52 external app manifests** were parsed from `/root/Gideon/Gideon-ai-apps/*/app.json`. The extraction contains 52 copied manifests under `provider-bundles`, not an additional 52 distinct integrations. Each inspected core tree also has **30 native app manifests** under `src/gideon/apps/native/*/app.json`. Thus the newer core plus external catalog contains 82 manifest directories across two layers, not 82 external services. Installed state was not consulted.

External declared types: 22 model, 7 search, 4 agent, 4 channel, 4 sync, 3 tool, 3 applications without a single provider declaration, 2 action, 1 inbox, 1 skills and 1 trigger. Model includes media/embedding engines; it is not a count of chat LLM vendors.

| External bundle | Declared type | Declared capabilities / application entry |
|---|---|---|
| [a2a-action](/root/Gideon/Gideon-ai-apps/a2a-action/app.json:2) | action | execute |
| [alibaba-models](/root/Gideon/Gideon-ai-apps/alibaba-models/app.json:2) | model | chat, code_tools, streaming, embedding, image_gen |
| [anthropic-compatible](/root/Gideon/Gideon-ai-apps/anthropic-compatible/app.json:2) | model | chat, code_tools, streaming, vision |
| [anthropic-models](/root/Gideon/Gideon-ai-apps/anthropic-models/app.json:2) | model | chat, code_tools, streaming, vision |
| [bedrock-models](/root/Gideon/Gideon-ai-apps/bedrock-models/app.json:2) | model | chat, code_tools, streaming, vision, embedding, image_gen, video_gen, stt |
| [brave-search](/root/Gideon/Gideon-ai-apps/brave-search/app.json:2) | search | search |
| [claude-code-agent](/root/Gideon/Gideon-ai-apps/claude-code-agent/app.json:2) | agent | acp |
| [claude-subscription](/root/Gideon/Gideon-ai-apps/claude-subscription/app.json:2) | model | chat, code_tools, streaming, vision |
| [codex-agent](/root/Gideon/Gideon-ai-apps/codex-agent/app.json:2) | agent | acp |
| [deepseek-models](/root/Gideon/Gideon-ai-apps/deepseek-models/app.json:2) | model | chat, code_tools, streaming |
| [diarization-onnx](/root/Gideon/Gideon-ai-apps/diarization-onnx/app.json:2) | model | diarization |
| [diarization-pyannote](/root/Gideon/Gideon-ai-apps/diarization-pyannote/app.json:2) | model | diarization |
| [dir-sync](/root/Gideon/Gideon-ai-apps/dir-sync/app.json:2) | sync | sync |
| [discord-channel](/root/Gideon/Gideon-ai-apps/discord-channel/app.json:2) | channel | messaging, reactions, threads |
| [duckduckgo-search](/root/Gideon/Gideon-ai-apps/duckduckgo-search/app.json:2) | search | search |
| [email-channel](/root/Gideon/Gideon-ai-apps/email-channel/app.json:2) | channel | messaging, threads, attachments |
| [exa-search](/root/Gideon/Gideon-ai-apps/exa-search/app.json:2) | search | search, fetch |
| [fal-image](/root/Gideon/Gideon-ai-apps/fal-image/app.json:2) | model | image_gen, video_gen |
| [faster-whisper](/root/Gideon/Gideon-ai-apps/faster-whisper/app.json:2) | model | stt |
| [gemini-cli-agent](/root/Gideon/Gideon-ai-apps/gemini-cli-agent/app.json:2) | agent | acp |
| [git-sync](/root/Gideon/Gideon-ai-apps/git-sync/app.json:2) | sync | sync |
| [google-models](/root/Gideon/Gideon-ai-apps/google-models/app.json:2) | model | chat, code_tools, streaming, vision, embedding, image_gen, video_gen, tts |
| [groq-models](/root/Gideon/Gideon-ai-apps/groq-models/app.json:2) | model | chat, code_tools, streaming |
| [growth](/root/Gideon/Gideon-ai-apps/growth/app.json:2) | application | backend/server.py |
| [kiro-cli-agent](/root/Gideon/Gideon-ai-apps/kiro-cli-agent/app.json:2) | agent | acp |
| [mail-inbox](/root/Gideon/Gideon-ai-apps/mail-inbox/app.json:2) | inbox | poll, attachments, send |
| [mcp-tools](/root/Gideon/Gideon-ai-apps/mcp-tools/app.json:2) | tool | tool_execution, tool_discovery |
| [menu-bar-companion](/root/Gideon/Gideon-ai-apps/menu-bar-companion/app.json:2) | application | Companion/application entry declared |
| [meta-muse-spark](/root/Gideon/Gideon-ai-apps/meta-muse-spark/app.json:2) | model | chat, streaming, vision |
| [minutes](/root/Gideon/Gideon-ai-apps/minutes/app.json:2) | application | backend/server.py |
| [mistral-models](/root/Gideon/Gideon-ai-apps/mistral-models/app.json:2) | model | chat, code_tools, streaming, vision |
| [ollama-models](/root/Gideon/Gideon-ai-apps/ollama-models/app.json:2) | model | chat, embedding |
| [openai-compatible](/root/Gideon/Gideon-ai-apps/openai-compatible/app.json:2) | model | chat, code_tools, streaming, embedding, vision |
| [openai-models](/root/Gideon/Gideon-ai-apps/openai-models/app.json:2) | model | chat, code_tools, streaming, embedding, vision |
| [openai-tools](/root/Gideon/Gideon-ai-apps/openai-tools/app.json:2) | tool | tool_execution, tool_discovery |
| [openrouter-models](/root/Gideon/Gideon-ai-apps/openrouter-models/app.json:2) | model | chat, code_tools, streaming, vision, embedding, image_gen, video_gen |
| [perplexity-search](/root/Gideon/Gideon-ai-apps/perplexity-search/app.json:2) | search | search |
| [piper-tts](/root/Gideon/Gideon-ai-apps/piper-tts/app.json:2) | model | tts |
| [rsync-sync](/root/Gideon/Gideon-ai-apps/rsync-sync/app.json:2) | sync | sync |
| [s3-sync](/root/Gideon/Gideon-ai-apps/s3-sync/app.json:2) | sync | sync |
| [searxng-search](/root/Gideon/Gideon-ai-apps/searxng-search/app.json:2) | search | search |
| [sentence-transformers](/root/Gideon/Gideon-ai-apps/sentence-transformers/app.json:2) | model | embedding |
| [shared-automations](/root/Gideon/Gideon-ai-apps/shared-automations/app.json:2) | trigger | rows, write-back |
| [skills-sh](/root/Gideon/Gideon-ai-apps/skills-sh/app.json:2) | skills | search, install, uninstall |
| [slack-channel](/root/Gideon/Gideon-ai-apps/slack-channel/app.json:2) | channel | messaging, reactions, threads |
| [tavily-search](/root/Gideon/Gideon-ai-apps/tavily-search/app.json:2) | search | search, fetch |
| [telegram-channel](/root/Gideon/Gideon-ai-apps/telegram-channel/app.json:2) | channel | messaging, threads |
| [together-models](/root/Gideon/Gideon-ai-apps/together-models/app.json:2) | model | chat, code_tools, streaming, vision |
| [vllm-models](/root/Gideon/Gideon-ai-apps/vllm-models/app.json:2) | model | chat, code_tools, streaming, embedding, vision |
| [web-tools](/root/Gideon/Gideon-ai-apps/web-tools/app.json:2) | tool | web |
| [webhook-action](/root/Gideon/Gideon-ai-apps/webhook-action/app.json:2) | action | execute |
| [wikipedia-search](/root/Gideon/Gideon-ai-apps/wikipedia-search/app.json:2) | search | search |

The 30 native names are bash-action, browse-action, create-task-action, filesystem-inbox, gideon-artifacts, gideon-automation-tools, gideon-code-map, gideon-computer-use-tools, gideon-inbox-tools, gideon-knowledge-tools, gideon-memory, gideon-project-tools, gideon-prompts, gideon-subagents, gideon-tasks-tools, gideon-tools, gideon-ui-docs, gideon-workflows, inbox-op-action, invoke-agent-action, native-agents, native-knowledge, native-prompts, native-skills, native-tasks, native-vector-memory, notify-action, run-prompt-action, run-script-action and send-message-action.

## Transfer analysis for later Sturgis choices

These recommendations are analysis, not approved implementation scope:

1. Preserve the capability inventory before simplifying navigation. Progressive disclosure still needs discoverable workflows, inbox actions, knowledge, apps, media and tool evidence. The hosted shell shows why the same backend and the same product breadth are different claims.
2. Make onboarding reach one observed successful interaction: compatible provider setup, readiness and binding, then a real task. Saved configuration is not successful readiness evidence.
3. Keep desktop, CLI, remote browser and hosted service as adapters around the runtime. The extraction's separate process/home is useful precedent; Sturgis should retain its own canonical I/O and authority contracts.
4. Preserve app data independently of app code and express capability/dependency/UI/lifecycle requirements in manifests. Distinguish available source, installed, configured, authorized and currently working capabilities.
5. Present streaming output, approvals, workflow state, delivery outcomes, source-linked knowledge and recovery as consistent views of the same state. These directly support the user's stable digital reality requirement.
6. Describe browser/computer availability by target and platform. A manifest or driver module must not become a successful capability claim; Windows/Linux actuators here are explicitly absent. Persisted browser progress still requires external-effect reconciliation.
7. Evaluate central/reconciler/private-runtime separation independently of UI simplification. Preserve input during wake transitions and distinguish service suspension from memory consolidation. No hosting vendor or deployment has been selected by this audit.

## Qualification and inspection limits

No donor code was imported or executed. No test, render smoke, build, model call, channel send, computer action, hosted account or deployed runtime was verified. Test files and packaging source do not establish passing results. The complete app manifest set was enumerated; individual vendor implementations were not exhaustively audited. Consumer routes and representative backend seams were inspected, not every page's complete behavior. Cloud lifecycle/suspension received parallel source inspection but no operational exercise.

Known unsupported computer drivers and full-versus-hosted navigation differences are source-supported limitations. All remaining capabilities are source-present with runtime qualification outstanding. This report changes no Sturgis specification, core code, implementation status or UI choice.
