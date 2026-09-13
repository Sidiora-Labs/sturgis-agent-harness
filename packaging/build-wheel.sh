#!/usr/bin/env bash
# Build a platform-specific Python wheel containing compiled native/web assets.
set -euo pipefail
release_source=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
release_output=${1:-"$release_source/.build/release"}
release_python=${STURGIS_BUILD_PYTHON:-"$release_source/.venv/bin/python"}
release_stage=$(mktemp -d "${TMPDIR:-/tmp}/sturgis-release.XXXXXX")
trap 'rm -rf -- "$release_stage"' EXIT
mkdir -p -- "$release_output"
release_output=$(CDPATH='' cd -- "$release_output" && pwd)
cd -- "$release_source"
if [[ "${STURGIS_USE_BUILT_WEB:-0}" != "1" ]]; then
    npm run build:web
fi
cmake -S "$release_source" -B "$release_stage/native" -DSTURGIS_COVERAGE=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build "$release_stage/native" --target sturgis-memory --parallel 2
"$release_python" - "$release_source" "$release_stage" <<'PY'
from pathlib import Path
import shutil
import sys

source, stage = map(Path, sys.argv[1:])
package = stage / "package"
package.mkdir()
for name in ("agent", "consumer", "model_gateway"):
    shutil.copytree(source / name, package / name,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.gcda"))
shutil.copy2(source / "pyproject.toml", package / "pyproject.toml")
for name in ("README.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
    shutil.copy2(source / name, package / name)
binary = package / "consumer/bin/sturgis-memory"
binary.parent.mkdir(exist_ok=True)
shutil.copy2(stage / "native/sturgis-memory", binary)
binary.chmod(0o755)
shutil.copy2(source / "native/memory/vendor/LICENSE.MIT", binary.parent / "LICENSE.MIT")
shutil.copy2(source / "native/memory/vendor/README.md", binary.parent / "vendor-source.md")
# A bundled ELF binary makes this a platform wheel, never a universal wheel.
(package / "setup.py").write_text(
    "from setuptools import Distribution, setup\n"
    "class NativeDistribution(Distribution):\n"
    "    def has_ext_modules(self): return True\n"
    "setup(distclass=NativeDistribution)\n")
PY
"$release_python" -m pip wheel "$release_stage/package" --no-deps --no-build-isolation --wheel-dir "$release_output"
"$release_python" - "$release_output" <<'PY'
from pathlib import Path
import hashlib
import sys
for wheel in Path(sys.argv[1]).glob("sturgis_core-*.whl"):
    wheel.with_suffix(wheel.suffix + ".sha256").write_text(hashlib.sha256(wheel.read_bytes()).hexdigest() + "  " + wheel.name + "\n")
PY
