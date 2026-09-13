"""A receipt must prove the exact current source and all independent language floors."""
import json
import platform

import pytest

from scripts import qualify_gateway as receipt


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))


@pytest.fixture
def qualified(tmp_path, monkeypatch):
    root, out = tmp_path, tmp_path / "evidence/sturgis-03"
    counts = {"agent/a.py": 100, "consumer/a.py": 1, "model_gateway/a.py": 10}
    for name, lines in counts.items():
        path = root / name
        path.parent.mkdir(parents=True)
        path.write_text("value = 1\n" * lines)
    for name in ("native/memory/a.cpp", "frontend/src/a.ts", "frontend/tests/a.test.ts", ".build/native/sturgis-memory"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("qualification input\n")
    def web_snapshot(root):
        return {"files": {name: receipt._digest(root / name) for name in
            ("frontend/src/a.ts", "frontend/tests/a.test.ts", ".build/native/sturgis-memory")}}
    monkeypatch.setattr(receipt, "_web_snapshot", web_snapshot)
    python = {"files": {name: {"executed_lines": list(range(1, total + 1)), "missing_lines": [],
        "summary": {"covered_lines": total, "num_statements": total, "missing_lines": 0, "excluded_lines": 0}}
        for name, total in counts.items()}, "totals": {"covered_lines": 111, "num_statements": 111, "missing_lines": 0, "excluded_lines": 0}}
    native = {"files": [{"file": "native/memory/a.cpp", "lines": [{"count": 1, "branches": [{"count": 1}]}]}]}
    web = {"files": {"frontend/src/a.ts": {"lines": {"covered": 1, "total": 1}}},
           "totals": {"lines": {"covered": 1, "total": 1}, "branches": {"covered": 0, "total": 0}}}
    write(out / "source-manifest.json", receipt.gate.snapshot(root))
    write(out / "web-source-manifest.json", web_snapshot(root))
    write(out / "browser-v8.json", {})
    write(root / ".build/web-vitest/coverage-final.json", {})
    web.update(source_manifest_sha256=receipt._digest(out / "web-source-manifest.json"), evidence_sha256={
        "evidence/sturgis-03/browser-v8.json": receipt._digest(out / "browser-v8.json"),
        ".build/web-vitest/coverage-final.json": receipt._digest(root / ".build/web-vitest/coverage-final.json")})
    for name, value in [("python-coverage.json", python), ("native-coverage.json", native), ("web-coverage.json", web),
                        ("browser-quality.json", {"accessibility": []}), ("gateway-browser-quality.json", {"status": "passed"})]:
        write(out / name, value)
    report = {"spec": "sturgis-03", "required_line_percent": 90.0,
        "live_remote_provider_certified": False, "public_deployment_certified": False,
        "python_version": platform.python_version(), "system": platform.system(), "completed_at": 12345,
        "source_manifest_sha256": receipt._digest(out / "source-manifest.json"),
        "python_line_percent": 100.0, "gateway_python_line_percent": 100.0, "native_line_percent": 100.0, "javascript_line_percent": 100.0,
        "executable_lines": {"python": {"covered": 111, "total": 111}, "gateway_python": {"covered": 10, "total": 10},
                             "native": {"covered": 1, "total": 1}, "javascript": {"covered": 1, "total": 1}},
        "python_branches": {}, "javascript_branches": {"covered": 0, "total": 0}, "native_branches": {"covered": 1, "total": 1}}
    def seal():
        report["evidence_sha256"] = {name: receipt._digest(out / name) for name in receipt.ARTIFACTS}
        write(out / "qualification.json", report)
    seal()
    return root, out, report, seal


def test_exact_receipt_passes_without_running_product_verification(qualified, monkeypatch):
    root, out, _, _ = qualified
    def no_run(*args, **kwargs):
        pytest.fail("Receipt validation must not run product qualification")
    monkeypatch.setattr(receipt.gate, "main", no_run)
    result = receipt.verify_receipt(out, root=root)
    assert result["status"] == "passed" and result["gateway_python_line_percent"] == 100


@pytest.mark.parametrize("field,value", [("spec", "sturgis-02"), ("required_line_percent", 80),
    ("live_remote_provider_certified", True), ("public_deployment_certified", True),
    ("python_version", "unknown"), ("system", "unknown"), ("completed_at", float("nan")), ("completed_at", True),
    ("python_line_percent", 99), ("gateway_python_line_percent", 99), ("executable_lines", {}), ("native_branches", {})])
def test_wrong_identity_environment_or_counts_cannot_pass(qualified, field, value):
    root, out, report, seal = qualified
    report[field] = value
    seal()
    with pytest.raises(SystemExit):
        receipt.verify_receipt(out, root=root)


@pytest.mark.parametrize("path", ["agent/a.py", "model_gateway/a.py", "frontend/src/a.ts", "frontend/tests/a.test.ts", ".build/native/sturgis-memory"])
def test_changed_production_and_web_build_inputs_invalidate_receipt(qualified, path):
    root, out, _, _ = qualified
    (root / path).write_text("changed\n")
    with pytest.raises(SystemExit, match="changed"):
        receipt.verify_receipt(out, root=root)


@pytest.mark.parametrize("path", [*receipt.ARTIFACTS, "source-manifest.json", "browser-v8.json", "../../.build/web-vitest/coverage-final.json"])
def test_changed_artifacts_invalidate_receipt(qualified, path):
    root, out, _, _ = qualified
    target = (out / path).resolve()
    target.write_text(target.read_text() + "\n")
    with pytest.raises(SystemExit):
        receipt.verify_receipt(out, root=root)


def test_failed_gateway_browser_cannot_pass_with_rehashed_artifacts(qualified):
    root, out, _, seal = qualified
    write(out / "gateway-browser-quality.json", {"status": "failed"})
    seal()
    with pytest.raises(SystemExit, match="browser qualification did not pass"):
        receipt.verify_receipt(out, root=root)


def test_missing_gateway_file_cannot_be_hidden_by_rehashing(qualified):
    root, out, _, seal = qualified
    data = json.loads((out / "python-coverage.json").read_text())
    del data["files"]["model_gateway/a.py"]
    write(out / "python-coverage.json", data)
    seal()
    with pytest.raises(SystemExit, match="denominator"):
        receipt.verify_receipt(out, root=root)


def test_gateway_floor_cannot_be_diluted_by_core_coverage(qualified):
    root, out, report, seal = qualified
    data = json.loads((out / "python-coverage.json").read_text())
    item = data["files"]["model_gateway/a.py"]
    item["executed_lines"], item["missing_lines"] = list(range(1, 9)), [9, 10]
    item["summary"].update(covered_lines=8, missing_lines=2)
    data["totals"].update(covered_lines=109, missing_lines=2)
    report["python_line_percent"] = 100 * 109 / 111
    write(out / "python-coverage.json", data)
    seal()
    with pytest.raises(SystemExit, match="below 90"):
        receipt.verify_receipt(out, root=root)


def test_missing_or_wrongly_shaped_receipt_is_rejected(qualified):
    root, out, _, _ = qualified
    (out / "qualification.json").unlink()
    with pytest.raises(SystemExit, match="receipt rejected"):
        receipt.verify_receipt(out, root=root)
    write(out / "qualification.json", [])
    with pytest.raises(SystemExit, match="Expected an object"):
        receipt.verify_receipt(out, root=root)


def test_check_cli_never_calls_the_full_runner(monkeypatch, capsys, tmp_path):
    def no_run(*args, **kwargs):
        pytest.fail("--check must only verify evidence")
    monkeypatch.setattr(receipt.gate, "main", no_run)
    monkeypatch.setattr(receipt, "verify_receipt", lambda out: {"status": "passed", "output": str(out)})
    receipt.main(["--check", "--output", str(tmp_path)])
    assert json.loads(capsys.readouterr().out)["output"] == str(tmp_path)


def test_full_cli_retains_all_required_gates(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(receipt.gate, "main", lambda args: calls.append(args))
    receipt.main(["--output", str(tmp_path)])
    assert calls == [["--spec", "sturgis-03", "--gateway-browser", "--output", str(tmp_path)]]


def test_web_snapshot_reads_actual_runtime_and_test_inputs():
    root = receipt.gate.ROOT
    manifest = receipt._web_snapshot(root)
    for name in ("model_gateway/service.py", "frontend/src/gateway-main.tsx", "tests/gateway/browser_journey.mjs"):
        assert manifest["files"][name]["sha256"] == receipt._digest(root / name)
