"""Qualify sturgis-03 with all language gates and both real browser journeys."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess

if __package__:
    from . import qualify_consumer as gate
else:
    import qualify_consumer as gate

ARTIFACTS = ("python-coverage.json", "native-coverage.json", "web-coverage.json", "web-source-manifest.json",
             "browser-quality.json", "gateway-browser-quality.json")


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path.name}")
    return value


def _web_snapshot(root):
    """Recompute authored/runtime/build provenance without executing tests or a browser."""
    result = subprocess.run(["node", "--input-type=module", "-e",
        'import { snapshot } from "./tests/consumer_web/coverage.mjs"; console.log(JSON.stringify(await snapshot(process.cwd())));'],
        cwd=root, text=True, capture_output=True, check=True, timeout=30)
    return json.loads(result.stdout)


def verify_receipt(out=None, *, root=None):
    """Accept only complete evidence for the exact currently qualified source and assets."""
    root = Path(root or gate.ROOT).resolve()
    out = Path(out or root / "evidence/sturgis-03").resolve()
    try:
        report = _json(out / "qualification.json")
        if (report.get("spec") != "sturgis-03" or report.get("required_line_percent") != gate.FLOOR
                or report.get("live_remote_provider_certified") is not False or report.get("public_deployment_certified") is not False
                or report.get("python_version") != platform.python_version() or report.get("system") != platform.system()
                or type(report.get("completed_at")) not in (int, float) or not math.isfinite(report["completed_at"]) or report["completed_at"] <= 0):
            raise ValueError("Qualification identity, environment or floor does not match sturgis-03")
        source = _json(out / "source-manifest.json")
        if report.get("source_manifest_sha256") != _digest(out / "source-manifest.json") or source != gate.snapshot(root):
            raise ValueError("Qualified production source changed")
        digests = report.get("evidence_sha256", {})
        if set(digests) != set(ARTIFACTS) or any(digests[name] != _digest(out / name) for name in ARTIFACTS):
            raise ValueError("Qualification artifact hashes do not match")
        python, native, web = (_json(out / name) for name in ARTIFACTS[:3])
        measured = {"python_line_percent": gate.validate_python(python, root),
                    "native_line_percent": gate.validate_native(native, root),
                    "javascript_line_percent": gate.validate_web(web, root)}
        gateway = gate.gateway_report(python, root)
        measured["gateway_python_line_percent"] = gate.line_gate(gateway["totals"]["covered_lines"], gateway["totals"]["num_statements"], "Gateway Python")
        if any(report.get(key) != value for key, value in measured.items()):
            raise ValueError("Recorded language coverage differs from independently validated evidence")
        lines = [line for file in native["files"] for line in file["lines"] if not line.get("gcovr/noncode", False)]
        expected_lines = {"python": {"covered": python["totals"]["covered_lines"], "total": python["totals"]["num_statements"]},
                          "gateway_python": {"covered": gateway["totals"]["covered_lines"], "total": gateway["totals"]["num_statements"]},
                          "native": {"covered": sum(line["count"] > 0 for line in lines), "total": len(lines)},
                          "javascript": web["totals"]["lines"]}
        if report.get("executable_lines") != expected_lines:
            raise ValueError("Recorded executable line counts differ from coverage evidence")
        branches = [branch for line in lines for branch in line.get("branches", [])]
        if (report.get("python_branches") != {key: value for key, value in python["totals"].items() if "branch" in key}
                or report.get("javascript_branches") != web["totals"]["branches"]
                or report.get("native_branches") != {"covered": sum(branch["count"] > 0 for branch in branches), "total": len(branches)}):
            raise ValueError("Recorded branch counts differ from coverage evidence")
        if _json(out / "gateway-browser-quality.json").get("status") != "passed":
            raise ValueError("Gateway browser qualification did not pass")
        web_manifest = _json(out / "web-source-manifest.json")
        if web.get("source_manifest_sha256") != _digest(out / "web-source-manifest.json") or _web_snapshot(root) != web_manifest:
            raise ValueError("Qualified web inputs changed")
        web_inputs = {os.path.relpath(out / "browser-v8.json", root): out / "browser-v8.json",
                      ".build/web-vitest/coverage-final.json": root / ".build/web-vitest/coverage-final.json"}
        hashes = web.get("evidence_sha256", {})
        if set(hashes) != set(web_inputs) or any(hashes[name] != _digest(path) for name, path in web_inputs.items()):
            raise ValueError("Raw browser or component coverage changed")
        if gate.snapshot(root) != source:
            raise ValueError("Production source changed while checking the receipt")
    except (OSError, ValueError, TypeError, KeyError, OverflowError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Gateway qualification receipt rejected: {error}") from None
    return {"status": "passed", "spec": "sturgis-03", "receipt": str(out / "qualification.json"),
            "completed_at": report["completed_at"], **measured}


def main(argv=None):
    """Keep gateway qualification evidence separate from the completed prior spec."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="Verify current source and exact successful evidence without rerunning qualification")
    options = parser.parse_args(argv)
    if options.check:
        print(json.dumps(verify_receipt(options.output), indent=2))
    else:
        gate.main(["--spec", "sturgis-03", "--gateway-browser",
            *(["--output", str(options.output)] if options.output else [])])


if __name__ == "__main__":
    main()
