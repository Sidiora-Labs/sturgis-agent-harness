"""Invoke the installed real Babel parser using a bounded local subprocess."""

import json
from pathlib import Path
import subprocess

from .inventory import ReferenceError


def parse_javascript(path: str, source: str) -> dict:
    """Parse JS/TS source via stdin; source text is never shell code or executed."""
    try:
        result = subprocess.run(["node", str(Path(__file__).with_name("babel.mjs"))],
            input=json.dumps({"path": path, "source": source}), text=True,
            capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReferenceError(f"Babel parser unavailable for {path}: {type(exc).__name__}") from exc
    if result.returncode:
        raise ReferenceError(f"Babel rejected {path}: {result.stderr[-2000:]}")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise ReferenceError(f"Babel returned invalid structural data for {path}") from exc
