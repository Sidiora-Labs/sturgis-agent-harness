---
title: "Limits and compatibility at a glance"
description: "Find the concrete request, content, workflow, connector, archive, and runtime bounds enforced by this release."
---

Bounds make failures explicit and keep operations finite. A limit in one layer does not increase the limit in another: a 4 MiB upload can still exceed text extraction, model-context, or direct-download limits. Operator and adapter limits can be stricter than a general maximum.

## Content and transport

| Area | Current boundary |
| --- | --- |
| Consumer JSON request | 8 MiB; strict JSON objects, no duplicate keys/non-finite numbers/unknown fields. |
| Generic consumer identifier | ASCII letters, digits, underscore, hyphen; 1–128 characters. |
| Attachment upload and consumer workspace read/write | At most 4 MiB of bytes. |
| UTF-8 source extraction | 256 KiB of extracted text; larger content is deferred, not silently truncated into a complete source. |
| Supported text media types | `text/plain`, `text/markdown`, `text/csv`, `application/json`, `text/html`. |
| Attachment/artifact direct download | Bounded to 4 MiB; larger artifacts need supported read ranges. |
| Public event command | `after >= 0`, `limit` from 1–1,000; HTTP event route uses bounded pages and wait from 0–25 seconds. |
| Backup ZIP | 4 MiB compressed, 32 MiB expanded, 512 files, 8 MiB per file, bounded logical arrays of up to 10,000 records. |

The browser composer/source inclusion can use a smaller context prefix than the full extracted document. That prefix and its omissions remain labeled. Unsupported binary interpretation, including PDF/OCR/office decoding, is not bundled.

## Work and extensions

| Area | Current boundary |
| --- | --- |
| UI workflow | 1–64 nonempty sequential steps; at least one token and model turn per step. |
| API workflow | At most 256 steps, valid dependency graph without cycles. |
| Interval schedule | At least one second through API; UI uses whole minutes. |
| Calendar schedule | IANA timezone, weekdays 0–6, explicit fold policy; DST gaps skipped. |
| Outcome checks | At most 128 checks; explicit scoped evidence required for certified completion. |
| Procedure manifest | 64 KiB, required nonempty triggers/steps and typed list fields. |
| One-time approval | Default 300 seconds, configurable 1–3,600 seconds. |

## Connections and hosted workers

The supplied HTTP policy defaults to 8 MiB response bytes, 30 seconds, three redirects, and four concurrent requests. Fetch narrows its body to 64 KiB. Search returns 1–20 configured results. MCP permits 1–32 exact allowed tool names and bounded 64 KiB call arguments. Devices permit 1–32 declared operation schemas within 32 KiB, pairing 1–900 seconds, and at most 16 requests per poll. Signed external routes allow 120 requests per connection per minute; webhook bodies are at most 1 MiB.

Managed browser inspection defaults to 20 seconds, 64 requests, 16 MiB retrieved bytes, and 65,536 rendered characters. Hosted workers default to a hard 2 GiB virtual address-space ceiling per process, operator-configurable from 128 MiB to 256 GiB. This is not an RSS or aggregate physical-memory quota. The measured self-hosted browser needs large virtual reservations; hosted nested-browser networking remains unavailable on the qualification host.

## Worked example: a valid upload that cannot be indexed

You upload a 1 MiB UTF-8 document. Upload succeeds because it fits 4 MiB, but extraction reports deferred because its text exceeds 256 KiB. The expected recovery is to preserve the original and provide a smaller supported text source, not to assume the full document was indexed.

For a limit error, inspect the relevant feature's exact bound before changing operator settings. [Source library](../knowledge/source-library.md), [media](../connections/media.md), [browser](../connections/browser.md), and [backup/restore](../settings/backup-restore.md) explain their distinct recovery paths.
