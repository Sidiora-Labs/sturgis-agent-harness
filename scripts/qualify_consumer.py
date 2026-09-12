"""Run real consumer journeys and independent, complete language coverage gates."""
from pathlib import Path
import hashlib
import json
import platform
import subprocess
import sys
import time

from coverage import Coverage
from coverage.exceptions import CoverageException

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evidence/sturgis-02"
FLOOR = 90.0
NATIVE_SUFFIXES = {".cpp", ".cc", ".cxx", ".h", ".hpp"}


def production_files(root=ROOT):
    files = {p for folder in ("agent", "consumer") for p in (root / folder).rglob("*.py")}
    files |= {p for p in (root / "consumer/web").rglob("*") if p.is_file()}
    files |= {p for p in (root / "frontend/src").rglob("*") if p.is_file()}
    files |= {p for p in (root / "frontend/licenses").rglob("*") if p.is_file()}
    files |= {p for p in (root / "frontend").glob("*") if p.is_file()}
    files |= {p for p in (root / "native/memory").rglob("*") if p.suffix in NATIVE_SUFFIXES and "vendor" not in p.parts}
    files |= {p for folder in ("scripts", "packaging") for p in (root / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    files |= {root / p for p in ("pyproject.toml", "package.json", "package-lock.json", "CMakeLists.txt") if (root / p).is_file()}
    return sorted(files)


def snapshot(root=ROOT):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in production_files(root)}


def exact_files(actual, expected, label):
    if actual != expected:
        raise SystemExit(f"{label} denominator mismatch: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")


def line_gate(covered, total, label):
    if type(covered) is not int or type(total) is not int or not 0 <= covered <= total or total <= 0:
        raise SystemExit(f"{label} invalid executable line counts")
    percent = 100 * covered / total
    if percent < FLOOR:
        raise SystemExit(f"{label} executable line coverage {percent:.6f}% is below {FLOOR}%")
    return percent


def _python_lines(values, filename):
    if (not isinstance(values, list) or any(type(line) is not int or line <= 0 for line in values)
            or len(values) != len(set(values))):
        raise SystemExit(f"Python invalid line data: {filename}")
    return set(values)


def validate_python(data, root=ROOT):
    root = Path(root).resolve()
    expected = {str(p.relative_to(root)) for folder in ("agent", "consumer") for p in (root / folder).rglob("*.py")}
    try:
        actual = {str((root / p).resolve().relative_to(root)) for p in data["files"]}
    except ValueError:
        raise SystemExit("Python denominator includes files outside the production root") from None
    exact_files(actual, expected, "Python")
    if len(actual) != len(data["files"]):
        raise SystemExit("Python denominator includes duplicate source paths")
    analysis = Coverage(config_file=False, data_file=None)
    counts = {"covered_lines": 0, "num_statements": 0, "missing_lines": 0, "excluded_lines": 0}
    for filename, record in data["files"].items():
        try:
            _, statements, excluded, _, _ = analysis.analysis2(str(root / filename))
        except CoverageException as error:
            raise SystemExit(f"Python source analysis failed: {filename}: {error}") from None
        executable = set(statements)
        executed = _python_lines(record["executed_lines"], filename)
        missing = _python_lines(record["missing_lines"], filename)
        if excluded or _python_lines(record.get("excluded_lines", []), filename):
            raise SystemExit(f"Python executable source exclusions are not accepted: {filename}")
        if missing != executable - executed:
            raise SystemExit(f"Python executable line partition disagrees with source: {filename}")
        # Runtime tracing can include docstrings and other non-statements. Only
        # coverage.py's independently parsed executable statements enter the gate.
        observed = {"covered_lines": len(executable & executed), "num_statements": len(executable),
                    "missing_lines": len(missing), "excluded_lines": 0}
        summary = record.get("summary", {})
        if any(type(summary.get(key)) is not int or summary[key] != value for key, value in observed.items()):
            raise SystemExit(f"Python per-file summary disagrees with executable source: {filename}")
        for key, value in observed.items():
            counts[key] += value
    if any(type(data["totals"].get(key)) is not int or data["totals"][key] != value for key, value in counts.items()):
        raise SystemExit("Python totals disagree with per-file executable counts")
    return line_gate(counts["covered_lines"], counts["num_statements"], "Python")


def validate_native(data, root=ROOT):
    expected = {str(p.relative_to(root)) for p in (root / "native/memory").rglob("*") if p.suffix in {".cpp", ".cc", ".cxx"} and "vendor" not in p.parts}
    allowed = {str(p.relative_to(root)) for p in (root / "native/memory").rglob("*") if p.suffix in NATIVE_SUFFIXES and "vendor" not in p.parts}
    actual = {str((root / f["file"]).resolve().relative_to(root)) for f in data["files"]}
    if not expected or expected - actual or actual - allowed or len(actual) != len(data["files"]):
        raise SystemExit("Native denominator mismatch")
    lines = [line for f in data["files"] for line in f["lines"] if not line.get("gcovr/noncode", False)]
    return line_gate(sum(line["count"] > 0 for line in lines), len(lines), "C++")


def validate_web(data, root=ROOT):
    expected = {str(p.relative_to(root)) for p in (root / "frontend/src").rglob("*") if p.suffix in {".ts", ".tsx", ".js", ".jsx"}}
    exact_files(set(data["files"]), expected, "JavaScript")
    covered = sum(f["lines"]["covered"] for f in data["files"].values())
    total = sum(f["lines"]["total"] for f in data["files"].values())
    if (covered, total) != (data["totals"]["lines"]["covered"], data["totals"]["lines"]["total"]):
        raise SystemExit("JavaScript totals disagree with per-file counts")
    return line_gate(covered, total, "JavaScript")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "qualification.json").unlink(missing_ok=True)
    subprocess.run(["npm", "run", "build:web"], cwd=ROOT, check=True)
    source_before = snapshot()
    subprocess.run([sys.executable, "scripts/verify_native.py", "--output", str(OUT)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "-m", "pytest", "tests/core", "tests/integration", "tests/consumer", "tests/consumer_integration", "-o", "faulthandler_timeout=60", "--cov=agent", "--cov=consumer", "--cov-branch", f"--cov-report=json:{OUT/'python-coverage.json'}", f"--cov-report=xml:{OUT/'python-coverage.xml'}", "--cov-report=term-missing", "-q"], cwd=ROOT, check=True, timeout=900)
    subprocess.run(["npm", "run", "test:web"], cwd=ROOT, check=True)
    python = json.loads((OUT / "python-coverage.json").read_text())
    native = json.loads((OUT / "native-coverage.json").read_text())
    web = json.loads((OUT / "web-coverage.json").read_text())
    report = {"spec": "sturgis-02", "required_line_percent": FLOOR,
              "python_line_percent": validate_python(python), "native_line_percent": validate_native(native),
              "javascript_line_percent": validate_web(web),
              "python_branches": {k: v for k, v in python["totals"].items() if "branch" in k},
              "javascript_branches": web["totals"]["branches"],
              "live_remote_provider_certified": False, "public_deployment_certified": False,
              "python_version": platform.python_version(), "system": platform.system()}
    native_lines = [line for f in native["files"] for line in f["lines"] if not line.get("gcovr/noncode", False)]
    native_branches = [branch for line in native_lines for branch in line.get("branches", [])]
    report["executable_lines"] = {
        "python": {"covered": python["totals"]["covered_lines"], "total": python["totals"]["num_statements"]},
        "native": {"covered": sum(line["count"] > 0 for line in native_lines), "total": len(native_lines)},
        "javascript": web["totals"]["lines"],
    }
    report["native_branches"] = {"covered": sum(branch["count"] > 0 for branch in native_branches), "total": len(native_branches)}
    report["evidence_sha256"] = {
        name: hashlib.sha256((OUT / name).read_bytes()).hexdigest()
        for name in ("python-coverage.json", "native-coverage.json", "web-coverage.json", "web-source-manifest.json", "browser-quality.json")
    }
    if source_before != snapshot():
        raise SystemExit("Production source changed during qualification")
    manifest = json.dumps(source_before, sort_keys=True, indent=2) + "\n"
    (OUT / "source-manifest.json").write_text(manifest)
    report.update(completed_at=time.time(), source_manifest_sha256=hashlib.sha256(manifest.encode()).hexdigest())
    (OUT / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
