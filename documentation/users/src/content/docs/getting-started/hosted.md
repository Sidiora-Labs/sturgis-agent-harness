---
title: "Use or operate a hosted installation"
description: "Connect through an operator's HTTPS origin and understand account, namespace, resource, and network boundaries."
sidebar:
  order: 3
---

If someone else operates Sturgis, open their supplied web address and use [account setup](account-and-agents.md). You do not select an agent's filesystem roots or deployment mode in the browser. The rest of this page is for the operator of that service.

## Prerequisites and boundaries

Install a compatible platform wheel as described in [self-hosted installation](self-hosted.md). Hosted workers additionally require bubblewrap and util-linux's `prlimit`. The service identity and outer container or host must permit unprivileged user, mount, PID, IPC, and UTS namespaces. The worker fails with `isolation_unavailable` if these facilities cannot be established.

Each worker sees its own writable runtime, workspace, and private directories, with the program and dependencies mounted read-only. Other agent roots, the control root, and unrelated home directories are not mounted. Capabilities are dropped. This provides filesystem and process confinement, not a separate network namespace for the entire worker or a hostile-kernel security boundary.

## Worked example: serve behind an HTTPS proxy

After installing the wheel under a dedicated OS account:

```sh
sturgis serve --host 127.0.0.1 --port 8787 \
  --public-origin https://agents.example.com --mode hosted
```

`agents.example.com` is an example you must replace with your actual origin. Configure your reverse proxy to preserve the external Host and Origin values and proxy `/`, `/assets/`, and `/api/v1/`. Preserve long-poll/stream delivery rather than buffering away incremental updates. Sturgis does not infer authority from arbitrary forwarded headers.

Additional account registration is disabled by default. Add `--allow-signup` only when you intend to permit it. Hosted mode alone does not open signup. A wildcard listener such as `--host 0.0.0.0` requires an explicit public origin.

## Resources and optional services

The default hosted hard virtual address-space limit is 2 GiB per process. The operator can choose 128 MiB through 256 GiB using `--worker-memory-bytes`; this is not an aggregate physical-memory quota. Other launcher limits include 1,024 descriptors, 256 for `RLIMIT_NPROC`, and 86,400 CPU seconds with normal OS semantics.

For a local model service, allow its exact origin with `--private-origin http://127.0.0.1:8080`. Normal supported HTTP access defaults to public destinations and still requires each connection's origin grant. This policy does not constrain arbitrary code that an operator installs as a plugin.

## Expected result and recovery

The browser should authenticate against the exact public origin, and an agent's runtime status should identify Linux namespace isolation. A running server with a failed private worker is not a healthy agent. Inspect [diagnostics](../settings/usage-diagnostics.md) and fix the host requirement instead of relying on a silent fallback.

Managed Chromium has a separate limitation: on the qualification host, nested browser networking is denied inside hosted workers even with a larger address-space ceiling. [Browser inspection](../connections/browser.md) explains the typed unavailable result. No public cloud deployment, Docker launch, commercial-vendor certification, or multi-host placement is implied by this manual.
