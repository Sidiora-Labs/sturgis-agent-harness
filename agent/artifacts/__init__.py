"""Scoped immutable evidence bodies with integrity-checked retrieval."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile

from agent.protocol import CoreError, canonical_json, strict_loads, validate_scope


@dataclass(frozen=True)
class Artifact:
    id: str
    scope: str
    size: int
    sha256: str
    media_type: str


class ArtifactStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)

    def _directory(self, scope):
        validate_scope(scope)
        path = self.root/hashlib.sha256(scope.encode()).hexdigest()
        if path.is_symlink():
            raise CoreError("path_escape", "Artifact directory is a symlink")
        path.mkdir(exist_ok=True)
        return path

    def put(self, scope, data: bytes, media_type="application/octet-stream"):
        if not isinstance(data,bytes) or not isinstance(media_type,str) or not media_type:
            raise CoreError("invalid_artifact", "Artifact requires bytes and media type")
        digest = hashlib.sha256(data).hexdigest()
        id = "art_"+hashlib.sha256(digest.encode()+b"\0"+media_type.encode()).hexdigest()
        artifact = Artifact(id,scope,len(data),digest,media_type)
        directory = self._directory(scope)
        for target,content in [(directory/id,data),(directory/(id+".json"),canonical_json(artifact.__dict__).encode())]:
            fd,name = tempfile.mkstemp(dir=directory)
            try:
                with os.fdopen(fd,"wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name,target)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return artifact

    def get(self, scope, artifact_id):
        if not isinstance(artifact_id,str) or len(artifact_id)!=68 or not artifact_id.startswith("art_") or any(c not in "0123456789abcdef" for c in artifact_id[4:]):
            raise CoreError("invalid_artifact", "Invalid artifact identifier")
        directory = self._directory(scope)
        body,meta = directory/artifact_id,directory/(artifact_id+".json")
        if body.is_symlink() or meta.is_symlink():
            raise CoreError("path_escape", "Artifact path is a symlink")
        try:
            record = strict_loads(meta.read_bytes())
            data = body.read_bytes()
        except FileNotFoundError as exc:
            raise CoreError("not_found", "Artifact unavailable in this scope") from exc
        if record.get("scope")!=scope or record.get("id")!=artifact_id or record.get("sha256")!=hashlib.sha256(data).hexdigest() or record.get("size")!=len(data):
            raise CoreError("integrity", "Artifact content or metadata changed")
        return data
