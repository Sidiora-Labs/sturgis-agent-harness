---
title: "Media adapters and the managed browser"
description: "Use explicit protocol subsets and truthful capability probes."
---

Media adapters use configured compatible services; Sturgis does not bundle model weights or infer that every vendor accepts the same wire format. They share NetworkPolicy and credential/connection revision checks.

| Kind | Request | Accepted result |
| --- | --- | --- |
| Speech to text | Multipart POST `/audio/transcriptions` with model and an existing audio artifact as file | JSON containing text |
| Text to speech | POST `/audio/speech` with model, input, voice and `response_format: "wav"` | Valid bounded WAV bytes stored as an artifact |
| Image generation | POST `/images/generations` with model, prompt, `n: 1`, `response_format: "b64_json"` | Exactly one bounded PNG stored as an artifact |

Readiness probes query the configured model catalog. They report `operation_verified: false`: catalog membership is not proof that a transcription, speech or image generation call has worked.

For transcription, first upload the audio bytes, retain the resulting artifact ID, and invoke the granted connection operation with that ID. Do not send an arbitrary host path to a service. For generated output, use the returned artifact reference and verify/download its bytes through the normal scoped artifact boundary.

## Managed browser

BrowserBackend uses an operator-provided Chromium executable with a temporary profile, CDP pipes and no workspace/vault mounts. It does not connect to arbitrary remote CDP endpoints or download a browser at normal startup.

The browser process has its own network namespace. Page HTTP is mediated through NetworkPolicy, while raw browser networking stays isolated. Inspection returns bounded rendered text and screenshot artifacts. Default inspection bounds include 20 seconds, 64 requests, 16 MiB retrieved bytes and 65,536 text characters.

Chromium renderer compatibility can require a large virtual address-space reservation. That is not equivalent to physical RAM usage; the sampled process-tree RSS watchdog is not a hard kernel RSS quota. The browser cannot raise a lower inherited address-space limit.

On the qualification environment, nested hosted-browser execution was unavailable. The UI and probes report actual availability instead of displaying a configured path as ready. Do not claim hosted browser or live vendor qualification from a self-hosted local fixture.

See [network policy](../consumer/network-connections.md), [artifacts](../core/artifacts.md) and [debugging](../operations/debugging.md).

## Implementation sources

- [consumer/connectors.py](/reference/files/consumer/connectors.py/)
- [consumer/browser.py](/reference/files/consumer/browser.py/)
- [consumer/network.py](/reference/files/consumer/network.py/)
- [consumer/config.py](/reference/files/consumer/config.py/)

[Developer manual](../index.md)

