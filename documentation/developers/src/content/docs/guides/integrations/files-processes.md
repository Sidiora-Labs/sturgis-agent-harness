---
title: "Rooted files and confined processes"
description: "Enforce exact preimages, delegated workspaces and actual process cleanup."
---

WorkspaceFs uses directory-relative file descriptors and rejects path escapes or symlink substitutions. Writes are atomic, bounded and preimage-checked. Omitting an expected hash means create only, not overwrite whatever happens to exist.

```python
from hashlib import sha256
from agent.tool_runner.files import WorkspaceFs

workspace = WorkspaceFs("/srv/sturgis/example-workspace")
before = workspace.read("notes.txt")
receipt = workspace.write(
    "notes.txt", before + b"Reviewed.\n",
    expected_sha256=sha256(before).hexdigest())
```

If another writer changes the file between read and write, the operation fails with a precondition error. Reread and decide whether the proposed change is still appropriate. Copy and rename separately check source and destination preimages; delete requires the exact existing hash.

This is trusted backend code. Model calls use a registered `file_executor` with an operation-specific schema. Its write argument is text `content`; binary responses use explicit base64. Consumer workspace commands expose their own bounded base64 contract.

## Process isolation

ProcessRunner accepts an argument vector and an explicit allowlist of programs. It launches bubblewrap with resource limits from `prlimit`, a bounded fresh environment, restricted mounts, isolated networking and bounded stdout/stderr. The granted working tree appears as `/workspace`. There is no unrestricted fallback when namespace support is unavailable.

Cancellation tears down the actual process environment before a stopped result is considered confirmed. Generic callback cancellation without termination evidence cannot make the same claim.

## Child constraints

Scheduler supplies canonical `workspace_root` and `workspace_writable` constraints for children. The file wrapper selects that root and denies mutations for a read-only grant. The process wrapper mounts the child root with the corresponding write permission. An executor that lacks constraint support rejects delegated dispatch.

Hosted consumer workers have a different outer isolation policy and share the host network namespace. Do not confuse that mode with ProcessRunner's isolated tool network. See [private workers](../consumer/workers-ipc.md).

Keep journal, vault and control roots outside publicly writable content paths. File success proves the observed file operation, not an unrelated business goal.

## Implementation sources

- [agent/tool_runner/files.py](/reference/files/agent/tool_runner/files.py/)
- [agent/tool_runner/process.py](/reference/files/agent/tool_runner/process.py/)
- [agent/tool_runner/__init__.py](/reference/files/agent/tool_runner/__init__.py/)

[Developer manual](../index.md)

