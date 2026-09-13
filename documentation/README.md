# Sturgis documentation sites

Two independent static sites are part of the Sturgis specification, implementation
and qualification workflow:

| Audience | Authored content | Built site | Local preview |
| --- | --- | --- | --- |
| Users | `users/src/content/docs/` | `dist/users/` | `http://127.0.0.1:4321` |
| Developers | `developers/src/content/docs/guides/` and generated `reference/files/` | `dist/developers/` | `http://127.0.0.1:4322` |

For the independent Model Gateway, begin with the [user connection manual](users/src/content/docs/guides/model-gateway.md),
[native account guide](users/src/content/docs/guides/gateway-accounts.md),
[routing and keys](users/src/content/docs/guides/gateway-routing-keys.md), and
[standalone installation](users/src/content/docs/getting-started/standalone-gateway.md).
Developer coverage starts with [gateway architecture](developers/src/content/docs/guides/gateway/architecture.md),
[HTTP contracts](developers/src/content/docs/guides/gateway/http-api.md),
[authentication](developers/src/content/docs/guides/gateway/authentication.md), and
[frontend implementation](developers/src/content/docs/guides/gateway/frontend.md).
The source inventory includes the complete `model_gateway/` package independently of agent modules.

User manuals teach actual product tasks, with prerequisites, worked examples,
expected results, errors and recovery. Developer guides explain architecture,
contracts, data ownership, important control paths, extensions, frontend behavior,
operations and testing. Generated source references retain every first-party file,
qualified definition, signature and numbered line. They expose syntax-derived
facts; they complement authored explanations and do not establish runtime proof.

## Build and read

Run from the repository root, after the normal Python development setup:

```sh
npm --prefix documentation ci
.venv/bin/python -m pip install -r documentation/requirements.txt
python3 scripts/docs_gate.py full
```

The full gate generates current source references and builds both complete sites.
It also enforces separate 90 percent executable Python line coverage for the
documentation checker and reference builder, with no excluded source lines.
These checks preserve the independent product coverage requirements.
Serve the outputs with any static HTTP server:

```sh
python3 -m http.server 4321 --bind 127.0.0.1 --directory documentation/dist/users
```

In a second terminal:

```sh
python3 -m http.server 4322 --bind 127.0.0.1 --directory documentation/dist/developers
```

During writing, `npm --prefix documentation run dev:users` and
`npm --prefix documentation run dev:developers` provide live previews. Static search
is generated during a production build; verify search against built output.
Node is a documentation build dependency, not a runtime dependency of the agent
or the finished static sites. Astro requires the Node version pinned in this
package's engine declaration. The separate lockfile preserves the qualified
consumer application's dependency graph.

To build for different origins, set `STURGIS_USER_DOCS_ORIGIN` and
`STURGIS_DEVELOPER_DOCS_ORIGIN` before building. Each output can be served from its
own domain or origin. Root-relative links assume each site is hosted at `/`.
No publication or access-control boundary is implied by building static files.
The developer site contains the project's authored source: apply the same access
policy as the source repository when serving it.

## Required spec flow

1. Map every new or changed feature to the user manual, including its actual
   UI/CLI/API exposure and a worked example. Update `coverage/users.json` and
   the individual recipe in the complete command cookbook. The gate independently
   compares recipes and field tables with the current registered command schema.
2. Update developer guides for architecture, invariants, parameter contracts,
   effects, error paths and operational implications. Update `coverage/developers.json`.
   Both audience manifests record reviewed implementation hashes. Review affected
   prose before updating those hashes; the gate deliberately does not refresh them.
3. Generate the complete source reference. The builder independently inventories
   source trees and rejects parser failures, omitted files, stale hashes and
   missing qualified definitions. Do not edit generated references by hand.
4. Run `python3 scripts/docs_gate.py full`. It builds both sites, audits rendered
   links and anchors, verifies source and feature completeness, validates examples,
   and exercises real search, navigation, themes, copy controls and mobile layouts.
5. Qualify the spec's final documentation task. During the documentation stage,
   maintain Codify's configured targets and claim ledger. Run
   `python3 scripts/docs_gate.py close` to require a fresh full-site receipt before
   `cg docs close`. Changed source or manuals invalidate the receipt.

A Markdown page, source-symbol list, screenshot, link-only check or a declared
completion status does not satisfy the documentation requirement. The permanent
rule is in `spec/workflow.kvx`; sturgis-02 requirement 14 makes the correction
explicit without rewriting the original runtime qualification evidence.

## Evidence and boundaries

`evidence/sturgis-docs/qualification.json` binds the complete result to source,
content, reference and test evidence. Per-audience content reports, rendered-link
reports, example results, browser results and screenshots remain beside it.
External URLs are reported separately and are not treated as locally verified
content. Static syntax and browser tests do not certify remote model vendors.

The source-reference denominator includes authored implementation, test, build and
configuration files. The inventory explicitly classifies generated assets,
third-party dependencies, caches and secret/runtime data. Secret files are excluded
before their contents are read. A dependency lock or binary asset may receive a
provenance record instead of fabricated source semantics.

## Site framework

The sites use [Astro](https://docs.astro.build/en/guides/deploy/) and
[Starlight](https://starlight.astro.build/getting-started/) with separate search
indexes, a shared Sturgis Material-inspired theme and normal Markdown sources.
Their pinned versions and exact dependency graph are in this directory's package
manifest and lockfile. Link rewriting keeps authored `.md` navigation readable in
the repository while publishing canonical site routes.
