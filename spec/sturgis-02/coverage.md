# Qualification policy

The user requires at least 90% test coverage. Sturgis-02 enforces **90% executable line coverage independently** for:

1. All first-party Python in `agent/` and `consumer/`, including unimported modules.
2. Production C++ under `native/memory/`, excluding only vendored/compiler/generated library code.
3. All authored TypeScript and JavaScript under `frontend/src/`, including zero-runtime type files and unimported/tree-shaken modules in the validated file set. Production browser coverage is source-mapped from the exact Vite bundles under `consumer/web/`; all generated assets, maps, fonts and the dependency lock are hashed. Third-party runtime libraries are not first-party coverage targets.

Branch coverage is enabled and reported separately. Percentages are not blended across languages. No production file may be excluded to reach the floor. The executable gate validates report file sets, invalidates stale successes, checks source stability, writes hashes and fails on missing reports, failed commands, missing files or a below-floor result. Sturgis-01 evidence is retained as historical evidence; sturgis-02 publishes its own current manifest and results.

Focused tests use real temporary SQLite stores, encrypted vault files, process workers, framed sockets, namespace isolation, filesystem effects and the compiled memory worker. Remote model/MCP/media/search/device fixtures exercise actual HTTP/protocol adapters and are labeled as controlled endpoints. Browser/CDP and consumer web journeys must use a real browser. No secret-dependent vendor login, live payment, public deployment or paid cloud proof is claimed without execution.

Required integrated journeys: bootstrap/login/provider readiness; create agent and send/stream/steer/cancel/reconnect; uploaded source reaches bounded labeled context; work/schedule/approval becomes recorded core activity; connection revocation prevents new dispatch; two accounts cannot access each other's agent, events, files or credentials; worker loss preserves accepted input/output and unknown effects; hosted suspend/wake retains accepted work; backup/restore preserves data without restoring execution authority; both installation modes use the same HTTP/CLI behavior.

The web validation records real interactions at desktop and narrow widths, keyboard usability, safe untrusted output rendering, loading/empty/error states and production-source coverage. Packaging verification checks actual installed entry points/static assets and a runnable private worker. Any unexercised hosting environment remains an explicit limit.
