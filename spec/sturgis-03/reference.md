# Examined gateway reference

The complete source audit is [9router gateway architecture and provider inventory](../../.research/9router-gateway.md). It examines `decolua/9router` at commit `17c4cc76877bd1755030a8414f8d0083f48dcccf`, with 122 registry files and 119 active entries. Its MIT source distinguishes ordinary API keys, anonymous public endpoints, device authorization, PKCE and specialized upstream inference envelopes.

The implemented Sturgis boundary follows independent provider/account ownership, gateway client keys, live catalogs, explicit routing and separate executors/translators. Native account implementations cover GitHub Copilot, Codex, Gemini CLI and Kiro. Source presence in the reference does not certify current provider availability; retired GitHub Models was removed after checking current primary documentation.
