---
title: "Contribute to both documentation sites"
description: "Maintain separate user and developer manuals with independent completeness and browser qualification."
---

Sturgis has two documentation sites. The user site explains setup and actual product journeys. The developer site explains architecture, extension contracts, operations and the complete source reference. Each is a complete audience-specific manual; neither should be a landing page that delegates its missing substance to the other.

The sites use separate Astro Starlight content trees and produce independent static outputs at `documentation/dist/users` and `documentation/dist/developers`. Local developer servers use ports 4321 and 4322 respectively.

```sh
npm --prefix documentation ci
.venv/bin/python -m pip install -r documentation/requirements.txt
npm --prefix documentation run dev:users
```

Run the developer preview in a separate terminal with `npm --prefix documentation run dev:developers`.

## Worked contribution

When adding a command, update its user journey with real required fields, explain the developer boundary and failure behavior, and refresh the generated command/source reference. Map the guide to exact source files and a worked example in the documentation coverage manifest.

A prose guide needs useful explanations of inputs, expected outcomes and why the invariant matters. A function list or pasted code file complements that explanation but does not replace it. Keep examples tied to actual signatures and distinguish executable demonstrations from embedding snippets whose transports or credentials the caller supplies.

## Independent docs gate

```sh
python3 scripts/docs_gate.py full
python3 scripts/docs_gate.py verify-receipt
python3 scripts/docs_gate.py close
```

The full gate checks both sites' authored coverage, reference completeness, local links/anchors, builds and browser behavior. Receipt verification checks freshness against the actual documentation inputs. The close command requires that fresh full receipt before closing the documentation workflow; it cannot turn a stale successful build into current evidence.

Do not fix coverage by counting generated file/function pages as substantive tutorials. Do not suppress a broken local source link by silently converting it into plain text. Preserve third-party notices and keep exact source references aligned with the checkout.

Run [the first-mechanisms tutorial](first-mechanisms.md) to understand the kind of concrete example the manual should provide. Product runtime qualification remains separate, as described in [qualification](qualification.md).

## Implementation sources

- [scripts/docs_gate.py](/reference/files/scripts/docs_gate.py/)
- [documentation/package.json](/reference/files/documentation/package.json/)
- [documentation/requirements.txt](/reference/files/documentation/requirements.txt/)
- [documentation/examples/first_mechanisms.py](/reference/files/documentation/examples/first_mechanisms.py/)

[Developer manual](../index.md)

