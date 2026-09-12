"""Final core-scope, behavior and independent language coverage qualification."""
from pathlib import Path
import json
import subprocess
import sys
import time
import hashlib
import platform

ROOT = Path(__file__).resolve().parents[1]
MODULES = "acp attention awareness channel_adapters channel_core commitments delivery framing goals initiative meta_harness planner plugin_host plugin_sdk profile protocol provider_adapters provider_catalog scheduler self_evolution skills subagents tool_runner context runtime memory state artifacts".split()


def source_snapshot():
    paths = list((ROOT/"agent").rglob("*.py"))
    paths += [p for p in (ROOT/"native/memory").rglob("*") if p.is_file() and p.suffix in {".cpp", ".hpp", ".h", ".cc", ".cxx"}]
    paths += [ROOT/p for p in ("pyproject.toml", "CMakeLists.txt", "scripts/qualify.py", "scripts/verify_native.py") if (ROOT/p).is_file()]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    out = ROOT/"evidence/sturgis-01"
    (out/"qualification.json").unlink(missing_ok=True)
    missing = [m for m in MODULES if not (ROOT/"agent"/m/"__init__.py").is_file()]
    forbidden = [p for p in ("app", "apps", "ui", "frontend", "web", "desktop") if (ROOT/p).exists()]
    unexpected = [p.name for p in (ROOT/"agent").iterdir() if p.is_dir() and p.name != "__pycache__" and p.name not in MODULES]
    unexpected.extend(p.name for p in (ROOT/"agent").glob("*.py") if p.name != "__init__.py")
    if missing or forbidden or unexpected:
        raise SystemExit(f"Core scope failed: missing={missing}, forbidden={forbidden}, unexpected={unexpected}")
    out.mkdir(parents=True, exist_ok=True)
    source_before = source_snapshot()
    # Fresh checkouts must build the actual worker before Python integration uses it.
    subprocess.run([sys.executable,"scripts/verify_native.py"],cwd=ROOT,check=True)
    subprocess.run([sys.executable,"-m","pytest","tests/core","tests/integration","--cov=agent","--cov-branch",f"--cov-report=json:{out/'python-coverage.json'}",f"--cov-report=xml:{out/'python-coverage.xml'}","--cov-report=term-missing","-q"], cwd=ROOT,check=True)
    data = json.loads((out/"python-coverage.json").read_text())
    totals = data["totals"]
    python_lines = 100 * totals["covered_lines"] / totals["num_statements"] if totals["num_statements"] else 0
    measured = {str((ROOT/Path(f)).resolve().relative_to(ROOT)) for f in data["files"]}
    expected = {str(p.relative_to(ROOT)) for p in (ROOT/"agent").rglob("*.py")}
    if measured-expected:
        raise SystemExit(f"Non-production Python files in coverage: {sorted(measured-expected)}")
    if expected-measured:
        raise SystemExit(f"Production files absent from coverage: {sorted(expected-measured)}")
    if python_lines < 90:
        raise SystemExit(f"Python line coverage {python_lines:.4f}% is below 90%")
    native=json.loads((out/"native-coverage.json").read_text())
    measured_native = {str((ROOT/Path(f["file"])).resolve().relative_to(ROOT)) for f in native["files"]}
    native_sources = {str(p.relative_to(ROOT)) for p in (ROOT/"native/memory").rglob("*")
                      if p.suffix in {".cpp", ".hpp", ".h", ".cc", ".cxx"} and "vendor" not in p.relative_to(ROOT/"native/memory").parts}
    if measured_native-native_sources:
        raise SystemExit(f"Non-production native files in coverage: {sorted(measured_native-native_sources)}")
    expected_native = {str(p.relative_to(ROOT)) for p in (ROOT/"native/memory").rglob("*.cpp") if "vendor" not in p.relative_to(ROOT/"native/memory").parts}
    if not expected_native or expected_native-measured_native:
        raise SystemExit(f"Native production files absent from coverage: {sorted(expected_native-measured_native)}")
    lines=[line for f in native["files"] for line in f["lines"] if not line.get("gcovr/noncode",False)]
    native_lines=100*sum(line["count"]>0 for line in lines)/len(lines) if lines else 0
    if native_lines < 90:
        raise SystemExit(f"Native line coverage {native_lines:.4f}% is below 90%")
    if source_before != source_snapshot():
        raise SystemExit("Production source changed during qualification")
    manifest = json.dumps(source_before, sort_keys=True, indent=2) + "\n"
    (out/"source-manifest.json").write_text(manifest)
    report={"spec":"sturgis-01","completed_at":time.time(),"python_line_percent":python_lines,"python_branches":{k:v for k,v in totals.items() if "branch" in k},"native_line_percent":native_lines,"required_line_percent":90,"modules":MODULES,"scope":"core only","live_remote_provider_certified":False}
    report.update(source_manifest_sha256=hashlib.sha256(manifest.encode()).hexdigest(), python_version=platform.python_version(), system=platform.system())
    (out/"qualification.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
