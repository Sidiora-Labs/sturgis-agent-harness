"""Create/validate real portable ZIP bytes and exercise the CLI archive transport."""
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import zipfile

from agent.protocol import CoreError
from consumer.cli import _remote
from consumer.portability import _export, _unpack


def main():
    if sys.argv[1] == "validate":
        value = json.load(sys.stdin)
        try:
            manifest, entries, _, _ = _unpack(value["archive_base64"], value["sha256"])
            result = {"ok": True, "name": manifest["name"], "files": sorted(entries)}
        except CoreError as error:
            result = {"ok": False, "error": {"code": error.code, "message": str(error)}}
        print(json.dumps(result))
        return
    with tempfile.TemporaryDirectory(prefix="sturgis-ui-archive-") as directory:
        root = Path(directory).resolve()
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "notes.txt").write_text("Exact Cedar workspace source.\n")
        backup = _export(SimpleNamespace(root=root, workspace=workspace, scope="fixture_agent"), "Cedar research")
        _unpack(backup["archive_base64"], backup["sha256"])

        class Transport:
            def request(self, method, route, *, data):
                assert method == "POST"
                if route.endswith("/backup"):
                    assert data == {}
                    return backup
                assert route == "/agents/restore"
                return data

        output = root / "cli.sturgis"
        receipt = _remote(Transport(), None, SimpleNamespace(action="backup", agent="fixture_agent", output=str(output)))
        restored = _remote(Transport(), None, SimpleNamespace(action="restore", archive=str(output), name="Restored space", request_id=None))
        assert restored["archive_base64"] == backup["archive_base64"]
        assert restored["sha256"] == backup["sha256"]
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        with zipfile.ZipFile(io.BytesIO(output.read_bytes())) as archive:
            assert archive.read("workspace/notes.txt") == b"Exact Cedar workspace source.\n"
        print(json.dumps({"backup": backup, "cli": {key: receipt[key] for key in ("sha256", "bytes", "mode")}}))


if __name__ == "__main__":
    main()
