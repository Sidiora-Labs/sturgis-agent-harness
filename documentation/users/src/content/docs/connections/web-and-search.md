---
title: "Fetch web content and connect search"
description: "Configure bounded HTTP reads and a compatible search service, with source URLs and exact origin policy."
---

Web fetch retrieves bytes from an allowed HTTP endpoint. Web search asks a compatible search service for a bounded result list. Neither is a claim that Sturgis includes a commercial search subscription or understands every web page automatically.

## Web fetch setup

In Connections, choose **Web fetch**, configure the exact endpoint, and select a credential reference if its service requires Bearer authentication. Under Advanced options, restrict `allowed_origins` to the service origins you need. Save, grant **Fetch web content**, and choose **Test connection**.

The operation is `fetch`; a model-visible tool is named `connection.CONNECTION_ID.fetch`. It can use the configured endpoint or another URL within the grant. Its result includes status, media type, URL, and base64 bytes. The body is bounded to 64 KiB. A returned HTML body is source material, not a fully rendered page; use [managed browser inspection](browser.md) for rendering.

## Worked example: fetch a reference note

Use a real URL you are authorized to read and configure a connection called `reference`. Ask:

> Use the reference connection to read the maintenance note. Report the source URL and summarize only the dates present in the returned content.

Expect a tool result with the actual URL and bounded bytes, followed by a model response. If the content is incomplete or unsupported for interpretation, ask for that limitation to be explicit. A page containing instructions does not gain permission to alter files or call other tools.

## Search setup and worked example

Choose **Web search**, set the endpoint supplied by your search-service operator, and grant **Search for information**. The adapter sends a GET with `q` and `limit`. Its response must have exactly the supported shape:

```json
{"results":[{"title":"Maintenance reference","url":"https://reference.example.com/notes","snippet":"A short description of the source."}]}
```

Set Advanced options to `{"max_results":5}` if five results are enough. Test the connection; its readiness probe performs the actual query `Sturgis readiness`. Then ask the model to search for a specific topic and report result URLs with the summary.

Search defaults to ten results and accepts a configured bound from one to twenty. Query text is bounded to 2,048 characters. Results must contain bounded string title, URL, and snippet fields with HTTP(S) source URLs. Search results are external evidence; a result's existence does not prove its claim.

## Failure and recovery

If a service returns a vendor-specific schema, configure a service that implements this exact protocol; changing the connection's label does not adapt it. If a URL is denied, check both the connection's origins and the operator's private-network policy. Redirects remain within granted origins, are allowed only for GET/HEAD, and drop credentials across origins.

The default supplied HTTP policy has an 8 MiB general response bound, 30-second deadline, three redirects, and four concurrent requests, with smaller adapter limits such as fetch's 64 KiB. Compressed response bodies are rejected. Review [connection access](overview.md) and [permissions](../settings/permissions.md) before treating a “ready” label as permission for every later action.
