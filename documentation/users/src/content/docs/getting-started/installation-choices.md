---
title: "Choose an installation"
description: "Decide whether to join a hosted installation or run Sturgis yourself, and understand what each choice includes."
sidebar:
  order: 1
---

You can use Sturgis through an operator's web address or install its Python service yourself. Both modes serve the same built React interface. The choice changes who operates the service and what operating-system boundaries protect agent workers; it does not create a separate edition of the manual.

## Before you choose

You need a supported model endpoint for model responses, but no model account is required to start Sturgis, create an agent, or use local content services. The release does not include model weights, paid subscriptions, device applications, or a universal connector for every vendor.

| Choice | What you provide | Next page |
| --- | --- | --- |
| Join an existing installation | Its web address and an account or operator invitation to register | [Account and agents](account-and-agents.md) |
| Self-host on Linux | A compatible wheel, Python, a private data directory, and model configuration | [Self-hosted installation](self-hosted.md) |
| Operate hosted workers | The same service plus Linux namespace support, access policy, and HTTPS hosting | [Hosted operation](hosted.md) |

Self-hosted workers run as ordinary processes under the operator's OS identity. Hosted workers use private Linux filesystem and process namespaces. Their network is still shared with the host; the supplied HTTP adapters enforce origin policy. Neither mode is a claim of protection from a compromised host kernel.

## Worked example: choose the smallest useful setup

For a private research assistant on your Linux workstation, install the platform wheel and use the default loopback address. Create one agent, configure one model, and upload a text document. You do not need a reverse proxy, signup for additional accounts, or a managed browser to complete that learning path.

For an installation reached by other people, have the operator configure its exact HTTPS origin and decide whether signup is allowed. Each account creates its own agents; administrative status does not grant access to another owner's agents.

## Expected result and limits

After installation, you should reach the sign-in or first-owner screen. A successful health check proves that the control service responds; it does not prove model readiness or connection availability.

The wheel bundles the native memory executable and web assets. Node is needed when building the release, not while serving the installed application. Native browser inspection is optional and has additional requirements. On the qualification host, self-hosted browser inspection works with its documented virtual-address reservation, while the hosted worker's nested browser namespace is unavailable.

If you cannot create an account, check whether bootstrap is still required or signup is disabled. If a hosted worker cannot start, have the operator resolve its namespace requirements; do not switch modes casually to conceal that failure. Continue with [troubleshooting](../reference/troubleshooting.md) if the service and runtime states differ.
