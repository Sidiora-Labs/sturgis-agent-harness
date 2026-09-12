"""Incremental native build, real worker tests, and independent 90% line gate."""
from pathlib import Path
import subprocess
import sys
import argparse

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evidence/sturgis-01")
    args = parser.parse_args()
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=True)
    commands = [
        ["cmake", "-S", ".", "-B", ".build/native", "-DSTURGIS_COVERAGE=ON"],
        ["cmake", "--build", ".build/native", "--parallel", "2"],
        [sys.executable, "-m", "pytest", "tests/native", "-q"],
        [sys.executable, "-m", "gcovr", "--root", ".", "--filter", "native/memory/", "--exclude", "native/memory/vendor/", "--object-directory", ".build/native", "--json", str(out/"native-coverage.json"), "--xml", str(out/"native-coverage.xml"), "--txt", str(out/"native-coverage.txt"), "--fail-under-line", "90"],
    ]
    for index,cmd in enumerate(commands):
        if index==2:
            for counter in (ROOT/".build/native").rglob("*.gcda"):
                counter.unlink()
        subprocess.run(cmd, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
