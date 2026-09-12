---
title: "Immutable artifacts and bounded views"
description: "Retain exact bytes while exposing explicit excerpts and safe downloads."
---

Artifacts preserve exact bodies that should not be flattened into a prompt or truncated response. An artifact reference identifies scoped content with a media type, length and hash. Reading it verifies integrity rather than trusting a filename.

```python
from tempfile import TemporaryDirectory
from agent.artifacts import ArtifactStore

with TemporaryDirectory() as root:
    store = ArtifactStore(root)
    artifact = store.put("demo", b"first line\nsecond line\n", "text/plain")
    content = store.get("demo", artifact.id)
    assert content == b"first line\nsecond line\n"
```

Artifact storage does not grant access across scopes. The HTTP application additionally authorizes the human's agent before resolving an artifact, and downloads use safe content handling. A caller does not get to turn an artifact ID into an arbitrary host filesystem path.

## Large tool results

Runtime retains the complete tool result in the journal and artifact store. Its model/channel representation is bounded and includes a reference plus explicit missing ranges. That representation must not be labeled as a complete inline observation.

A consumer needing more content requests a bounded range through the application service. Binary data uses base64 with declared size instead of replacing undecodable bytes. The browser may download an authenticated artifact without rendering untrusted HTML as part of the application.

## Distinguish bytes from interpretation

A text extractor can successfully store a PDF or image while reporting that interpretation is unsupported. That is a valid source record, not extracted knowledge. Conversely, a text excerpt may be useful while leaving a suffix unread. Retain the original bytes, the extraction status and the source inclusion evidence separately.

When debugging corrupted content, compare the artifact's expected hash, stored bytes and transport length. Do not silently repair a mismatch by changing metadata. See [content and work services](../consumer/content-work.md) and [backup verification](../consumer/backup-restore.md).

## Implementation sources

- [agent/artifacts/__init__.py](/reference/files/agent/artifacts/__init__.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [consumer/content.py](/reference/files/consumer/content.py/)
- [consumer/api.py](/reference/files/consumer/api.py/)

[Developer manual](../index.md)
