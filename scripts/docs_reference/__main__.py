"""Command-line reference generation and independent source coverage audit."""

import argparse
import json
from pathlib import Path
import sys

from .builder import audit, build
from .inventory import ReferenceError


def main(argv=None) -> int:
    """Build or audit a complete source reference without running project code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "audit"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--guide-manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        result = (build if args.command == "build" else audit)(args.root, args.output, args.manifest, args.guide_manifest)
    except (ReferenceError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
