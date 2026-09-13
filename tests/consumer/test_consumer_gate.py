import importlib.util
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

from coverage import Coverage
import pytest

SPEC = importlib.util.spec_from_file_location("consumer_gate", Path(__file__).resolve().parents[2] / "scripts/qualify_consumer.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def test_language_floors_use_unrounded_executable_lines():
    assert gate.line_gate(90, 100, "test") == 90
    for covered, total in [(0, 0), (8999, 10000), (101, 100), (True, 1), (-1, 10)]:
        with pytest.raises(SystemExit):
            gate.line_gate(covered, total, "test")


def test_complete_denominators_and_totals(tmp_path):
    for name in ("agent/a.py", "consumer/a.py", "model_gateway/a.py", "tests/gateway/wire.py", "native/memory/a.cpp", "frontend/src/a.ts"):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("source\n")
    py = {"files": {name: {"executed_lines": [1], "missing_lines": [], "summary": {
        "covered_lines": 1, "num_statements": 1, "missing_lines": 0, "excluded_lines": 0}}
        for name in ("agent/a.py", "consumer/a.py", "model_gateway/a.py")}, "totals": {
        "covered_lines": 3, "num_statements": 3, "missing_lines": 0, "excluded_lines": 0}}
    assert gate.validate_python(py, tmp_path) == 100
    assert gate.gateway_report(py, tmp_path)["totals"]["covered_lines"] == 1
    py["totals"]["covered_lines"] = 1
    with pytest.raises(SystemExit, match="totals"):
        gate.validate_python(py, tmp_path)
    del py["files"]["consumer/a.py"]
    with pytest.raises(SystemExit, match="denominator"):
        gate.validate_python(py, tmp_path)
    native = {"files": [{"file": "native/memory/a.cpp", "lines": [{"count": 1}, {"count": 0, "gcovr/noncode": True}]}]}
    assert gate.validate_native(native, tmp_path) == 100
    native["files"] *= 2
    with pytest.raises(SystemExit):
        gate.validate_native(native, tmp_path)
    js = {"files": {"frontend/src/a.ts": {"lines": {"covered": 1, "total": 1}}}, "totals": {"lines": {"covered": 1, "total": 1}}}
    assert gate.validate_web(js, tmp_path) == 100
    js["totals"]["lines"]["total"] = 2
    with pytest.raises(SystemExit):
        gate.validate_web(js, tmp_path)
    assert set(gate.snapshot(tmp_path)) == {"agent/a.py", "consumer/a.py", "model_gateway/a.py", "tests/gateway/wire.py", "native/memory/a.cpp", "frontend/src/a.ts"}
    before = gate.snapshot(tmp_path)
    (tmp_path / "consumer/a.py").write_text("new source\n")
    assert before != gate.snapshot(tmp_path)


def test_gateway_coverage_cannot_be_diluted_by_other_python(tmp_path):
    for name, lines in (("agent/a.py", 100), ("model_gateway/a.py", 10)):
        path = tmp_path / name
        path.parent.mkdir(parents=True)
        path.write_text("value = 1\n" * lines)
    def record(total, covered):
        return {"executed_lines": list(range(1, covered + 1)), "missing_lines": list(range(covered + 1, total + 1)),
                "summary": {"covered_lines": covered, "num_statements": total, "missing_lines": total - covered, "excluded_lines": 0}}
    report = {"files": {"agent/a.py": record(100, 100), "model_gateway/a.py": record(10, 8)},
              "totals": {"covered_lines": 108, "num_statements": 110, "missing_lines": 2, "excluded_lines": 0}}
    assert gate.validate_python(report, tmp_path) > 90
    with pytest.raises(SystemExit, match="below 90"):
        gate.gateway_report(report, tmp_path)
    del report["files"]["model_gateway/a.py"]
    with pytest.raises(SystemExit, match="denominator"):
        gate.gateway_report(report, tmp_path)


def test_qualification_output_isolated_and_python_scope_complete(tmp_path):
    assert gate.arguments([]).output == gate.ROOT / "evidence/sturgis-02"
    options = gate.arguments(["--spec", "sturgis-03", "--gateway-browser"])
    assert options.output == gate.ROOT / "evidence/sturgis-03" and options.gateway_browser
    assert gate.arguments(["--output", str(tmp_path)]).output == tmp_path
    command = gate.python_command(tmp_path)
    assert {"--cov=agent", "--cov=consumer", "--cov=model_gateway", "tests/gateway"} <= set(command)
    assert "--cov-report=json:" + str(tmp_path / "python-coverage.json") in command


@pytest.fixture
def actual_python_report(tmp_path):
    sources = {
        "agent/__init__.py": '\"\"\"Package documentation.\"\"\"\nVALUE = 1\n',
        "consumer/__init__.py": '\"\"\"Documentation-only module.\"\"\"\n',
        "consumer/empty.py": "",
        "agent/decision.py": ('\"\"\"Decision documentation.\"\"\"\n'
            'def choose(value):\n    if value:\n        return "yes"\n    return "no"\n'
            'observed = choose(True)\n' + ''.join(f'extra_{i} = {i}\n' for i in range(8))),
    }
    for name, source in sources.items():
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(source)
    # A nested collector must not replace pytest-cov's active parent tracer or
    # merge fixture sources into the real qualification data file.
    environment = {key: value for key, value in os.environ.items()
        if not key.startswith(("COV_CORE_", "COVERAGE_"))}
    driver = '''
from pathlib import Path
from coverage import Coverage
root = Path.cwd()
cov = Coverage(config_file=False, data_file=str(root / "fixture.coverage"), branch=True,
    source=[str(root / "agent"), str(root / "consumer")])
cov.start()
for folder in ("agent", "consumer"):
    for path in sorted((root / folder).glob("*.py")):
        exec(compile(path.read_text(), str(path), "exec"), {"__name__": "fixture_target"})
cov.stop()
cov.save()
cov.json_report(outfile=str(root / "fixture-report.json"))
'''
    parent_collector = Coverage.current()
    subprocess.run([sys.executable, "-c", driver], cwd=tmp_path, env=environment,
        check=True, capture_output=True, text=True, timeout=20)
    assert Coverage.current() is parent_collector
    return tmp_path, json.loads((tmp_path / "fixture-report.json").read_text())


def test_real_coverage_docstrings_empty_modules_and_missing_branch(actual_python_report):
    root, report = actual_python_report
    files = report["files"]
    assert files["consumer/__init__.py"]["executed_lines"] == [1]
    assert files["consumer/__init__.py"]["summary"]["num_statements"] == 0
    assert files["consumer/empty.py"]["summary"]["num_statements"] == 0
    assert files["agent/decision.py"]["missing_lines"] == [5]
    assert sum(len(value["executed_lines"]) for value in files.values()) > report["totals"]["covered_lines"]
    expected = 100 * report["totals"]["covered_lines"] / report["totals"]["num_statements"]
    assert 90 < gate.validate_python(report, root) == expected < 100


@pytest.mark.parametrize("tamper,match", [
    ("summary", "summary"), ("totals", "totals"),
    ("omit_executed", "partition"), ("omit_missing", "partition"),
    ("omit_empty_module", "denominator"), ("duplicate_path", "duplicate source paths"),
    ("duplicate_line", "invalid line data"), ("boolean_line", "invalid line data"),
    ("reported_exclusion", "exclusions"),
])
def test_real_python_report_tampering_is_rejected(actual_python_report, tamper, match):
    root, original = actual_python_report
    report = copy.deepcopy(original)
    decision = report["files"]["agent/decision.py"]
    if tamper == "summary": decision["summary"]["covered_lines"] += 1
    elif tamper == "totals": report["totals"]["num_statements"] -= 1
    elif tamper == "omit_executed": decision["executed_lines"].remove(2)
    elif tamper == "omit_missing": decision["missing_lines"].clear()
    elif tamper == "omit_empty_module": del report["files"]["consumer/empty.py"]
    elif tamper == "duplicate_path": report["files"]["agent/../agent/decision.py"] = decision
    elif tamper == "duplicate_line": decision["executed_lines"].append(2)
    elif tamper == "boolean_line": decision["executed_lines"][0] = True
    elif tamper == "reported_exclusion": decision["excluded_lines"] = [5]
    with pytest.raises(SystemExit, match=match):
        gate.validate_python(report, root)


def test_real_missing_execution_cannot_hide_below_the_floor(actual_python_report):
    root, report = actual_python_report
    decision = report["files"]["agent/decision.py"]
    # Even a self-consistent lower-coverage report must fail the unchanged floor.
    decision["executed_lines"].remove(2)
    decision["missing_lines"].append(2)
    for summary in (decision["summary"], report["totals"]):
        summary["covered_lines"] -= 1
        summary["missing_lines"] += 1
    with pytest.raises(SystemExit, match="below 90"):
        gate.validate_python(report, root)
