---
title: "Inspect pages with the managed browser"
description: "Use contained Chromium page inspection and understand operator prerequisites, artifacts, and hosted unavailability."
---

Managed browser inspection opens a page in an operator-managed Chromium process and can return rendered text and a viewport screenshot. It is read-oriented page inspection, not unrestricted desktop automation or a connection to an arbitrary remote CDP browser.

## Operator prerequisites

The operator installs Chromium and its supporting files as a read-only tree and supplies the canonical executable with `--browser-binary /absolute/path/to/chrome` or `STURGIS_BROWSER_BINARY`. Bubblewrap, `prlimit`, libraries, and fonts are also required. Normal Sturgis startup does not download a browser.

The browser uses CDP pipes, a private temporary profile, its own network namespace, and no workspace or vault mounts. Page HTTP is fulfilled through the supplied NetworkPolicy; raw browser networking remains isolated. The endpoint's origin must still be granted.

## Worked example: inspect a public reference page

In Connections, choose **Managed browser**. Set an actual page URL as the endpoint and an exact `allowed_origins` list if more than its origin is needed. Save, grant **Inspect pages in the isolated browser**, and choose **Test connection**.

Only after readiness succeeds, ask:

> Inspect the reference page with the managed browser. Summarize the rendered maintenance instructions and provide the resulting screenshot artifact. Do not assume links outside the allowed origins were visited.

The expected result includes real rendered text and an artifact reference when screenshot creation succeeds. Use the authenticated artifact download presented with a result, or inspect `artifacts.read` through the CLI/API. A running Chromium control process by itself is not proof that a renderer can load and inspect the page.

## Bounds

Default inspection bounds include 20 seconds, 64 requests, 16 MiB of total retrieved bytes, and 65,536 characters of rendered text. The screenshot is a viewport result, not a promise to capture every part of an arbitrarily long page. Cross-origin page resources must satisfy the same allowed-origin policy or may be omitted/denied.

On the qualification host, a usable self-hosted renderer needed a 256 GiB virtual address-space ceiling; 64 GiB started only the control process. The launcher sets a ceiling no higher than 274,877,906,944 bytes and does not raise a lower inherited limit. This is virtual-address reservation compatibility, not a requirement for 256 GiB of physical RAM. Its sampled process-tree RSS watchdog is not a hard RSS quota.

## Hosted limitation and recovery

Hosted workers keep a 2 GiB default virtual limit. Choosing a larger operator ceiling does not fix the separately observed nested namespace restriction: on the qualification host, readiness returns `browser_unavailable` with reason `network_namespace_denied`. A lower inherited ceiling can instead report `renderer_address_space_limit`.

Treat those results as unavailable capability. Do not relax worker isolation or attach an uncontained browser to make the label turn green. The agent can still use other configured features, including [bounded web fetch](web-and-search.md). For page errors, inspect readiness and allowed origins before retrying. Browser content remains external source material and does not gain instruction or approval authority.
