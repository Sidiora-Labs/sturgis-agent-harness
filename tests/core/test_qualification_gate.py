"""Gate decisions over temporary reports; these doubles are not execution proof."""
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


class ReportBoundary:
    """Replace only subprocess execution with explicit report-producing fixtures."""

    def __init__(self, root, python_report, native_report):
        self.root, self.python_report, self.native_report = root, python_report, native_report
        self.calls = []
        self.fail = None
        self.omit_report = None

    def run(self, command, *, cwd, check):
        assert Path(cwd) == self.root and check is True
        native = command[1:] == ["scripts/verify_native.py"]
        python = command[1:3] == ["-m", "pytest"]
        assert native or python, f"Unexpected qualification command: {command!r}"
        kind = "native" if native else "python"
        self.calls.append(kind)
        if self.fail == kind:
            raise subprocess.CalledProcessError(2, command)
        output = self.root / "evidence/sturgis-01"
        if native:
            # A fresh-checkout ordering sentinel, never an executable or native proof.
            binary = self.root / ".build/native/sturgis-memory"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text("unit-test build-boundary sentinel\n")
            data, name = self.native_report, "native-coverage.json"
        else:
            assert (self.root / ".build/native/sturgis-memory").is_file(), "Native verification must precede Python integration tests"
            assert {"tests/core", "tests/integration", "--cov=agent", "--cov-branch"} <= set(command)
            assert f"--cov-report=json:{output / 'python-coverage.json'}" in command
            data, name = self.python_report, "python-coverage.json"
        if self.omit_report != kind:
            (output / name).write_text(json.dumps(data))
        return subprocess.CompletedProcess(command, 0)


def python_report(paths, *, covered=90, total=100):
    remaining_covered = covered
    files = {}
    for index, path in enumerate(paths):
        count = total // len(paths) + (index < total % len(paths))
        executed = min(count, remaining_covered)
        remaining_covered -= executed
        files[str(path)] = {"executed_lines": list(range(1, executed + 1)),
                            "missing_lines": list(range(executed + 1, count + 1)),
                            "summary": {"covered_lines": executed, "num_statements": count}}
    return {"meta": {"format": 3, "branch_coverage": True}, "files": files,
            "totals": {"covered_lines": covered, "num_statements": total,
                       "num_branches": 20, "covered_branches": 10, "missing_branches": 10}}


def native_report(*, covered=90, total=100):
    split = total // 2
    entries = []
    for name, start, end in (("engine.cpp", 0, split), ("main.cpp", split, total)):
        lines = [{"line_number": offset - start + 1, "count": int(offset < covered), "branches": []}
                 for offset in range(start, end)]
        lines.append({"line_number": total + 100, "count": 0, "gcovr/noncode": True})
        entries.append({"file": "native/memory/" + name, "lines": lines})
    return {"gcovr/format_version": "0.11", "files": entries}


@pytest.fixture
def gate(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/qualify.py"
    specification = importlib.util.spec_from_file_location("qualification_under_test", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    root = tmp_path / "isolated-core"
    root.mkdir()
    agent = root / "agent"
    agent.mkdir()
    (agent / "__init__.py").write_text("# temporary package\n")
    for name in module.MODULES:
        package = agent / name
        package.mkdir()
        (package / "__init__.py").write_text("# temporary production source\n")
    (agent / "runtime/extra_source.py").write_text("# must be measured too\n")
    (agent / "__pycache__").mkdir()
    native = root / "native/memory"
    native.mkdir(parents=True)
    for name in ("engine.cpp", "main.cpp", "engine.hpp"):
        (native / name).write_text("// temporary source identity\n")
    (native / "vendor").mkdir()
    (native / "vendor/third_party.cpp").write_text("// excluded third-party fixture\n")
    paths = sorted(agent.rglob("*.py"))
    boundary = ReportBoundary(root, python_report(paths), native_report())
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "subprocess", boundary)
    return SimpleNamespace(module=module, root=root, boundary=boundary, paths=paths,
                           output=root / "evidence/sturgis-01/qualification.json")


def test_valid_independent_reports_build_native_first_and_preserve_evidence_limits(gate, capsys):
    gate.module.main()
    assert gate.boundary.calls == ["native", "python"]
    result = json.loads(gate.output.read_text())
    assert result["python_line_percent"] == result["native_line_percent"] == 90
    assert result["required_line_percent"] == 90
    assert result["python_branches"] == {"num_branches": 20, "covered_branches": 10, "missing_branches": 10}
    assert result["live_remote_provider_certified"] is False
    assert result["scope"] == "core only" and result["modules"] == gate.module.MODULES
    assert json.loads(capsys.readouterr().out) == result


def test_relative_python_report_paths_resolve_against_the_qualification_root(gate):
    gate.boundary.python_report["files"] = {
        str(Path(path).relative_to(gate.root)): coverage
        for path, coverage in gate.boundary.python_report["files"].items()}
    gate.module.main()
    assert json.loads(gate.output.read_text())["python_line_percent"] == 90


@pytest.mark.parametrize("language", ["python", "native"])
def test_each_language_must_independently_reach_the_unrounded_floor(gate, language):
    # 89.999% rounds to 90.00 for display, but still fails the executable-line gate.
    if language == "python":
        gate.boundary.python_report = python_report(gate.paths, covered=89999, total=100000)
        gate.boundary.native_report = native_report(covered=100)
    else:
        gate.boundary.python_report = python_report(gate.paths, covered=100)
        gate.boundary.native_report = native_report(covered=89999, total=100000)
    with pytest.raises(SystemExit, match="below 90%"):
        gate.module.main()
    assert not gate.output.exists()


@pytest.mark.parametrize("language", ["python", "native"])
def test_empty_executable_denominator_cannot_qualify(gate, language):
    if language == "python":
        gate.boundary.python_report = python_report(gate.paths, covered=0, total=0)
    else:
        gate.boundary.native_report = native_report(covered=0, total=0)
    with pytest.raises(SystemExit):
        gate.module.main()
    assert not gate.output.exists()


def test_omitted_nested_first_party_python_source_fails_even_with_high_totals(gate):
    gate.boundary.python_report = python_report(gate.paths, covered=100)
    omitted = gate.root / "agent/runtime/extra_source.py"
    del gate.boundary.python_report["files"][str(omitted)]
    with pytest.raises(SystemExit, match="extra_source.py"):
        gate.module.main()
    assert not gate.output.exists()


def test_omitted_first_party_cpp_source_fails_even_with_high_totals(gate):
    gate.boundary.native_report = native_report(covered=100)
    gate.boundary.native_report["files"] = gate.boundary.native_report["files"][:1]
    with pytest.raises(SystemExit, match="main.cpp"):
        gate.module.main()
    assert not gate.output.exists()


def test_new_nested_first_party_cpp_source_must_appear_in_coverage(gate):
    directory = gate.root / "native/memory/storage"
    directory.mkdir()
    (directory / "projection.cpp").write_text("// first-party production source\n")
    with pytest.raises(SystemExit, match="projection.cpp"):
        gate.module.main()
    assert not gate.output.exists()


@pytest.mark.parametrize("reported_path", ["native/memory/vendor/third_party.cpp", "external/library.cpp"])
def test_third_party_coverage_cannot_conceal_a_native_shortfall(gate, reported_path):
    gate.boundary.native_report = native_report(covered=89)
    gate.boundary.native_report["files"].append({"file": reported_path,
        "lines": [{"line_number": index + 1, "count": 1} for index in range(100)]})
    with pytest.raises(SystemExit, match="Non-production native files in coverage|below 90%"):
        gate.module.main()
    assert not gate.output.exists()


def test_nonproduction_python_coverage_cannot_conceal_a_shortfall(gate):
    gate.boundary.python_report = python_report(gate.paths, covered=89)
    gate.boundary.python_report["files"][str(gate.root / "tests/fixture.py")] = {
        "executed_lines": list(range(1, 101)), "missing_lines": []}
    gate.boundary.python_report["totals"].update(covered_lines=189, num_statements=200)
    with pytest.raises(SystemExit, match="Non-production Python files in coverage"):
        gate.module.main()
    assert not gate.output.exists()


def test_failed_scope_check_invalidates_a_previous_success_report(gate):
    gate.output.parent.mkdir(parents=True)
    gate.output.write_text(json.dumps({"python_line_percent": 100, "native_line_percent": 100}))
    (gate.root / "app").mkdir()
    with pytest.raises(SystemExit, match="Core scope failed"):
        gate.module.main()
    assert not gate.output.exists()
    assert gate.boundary.calls == []


def test_missing_required_core_module_fails_before_commands(gate):
    (gate.root / "agent/memory/__init__.py").unlink()
    with pytest.raises(SystemExit, match="memory"):
        gate.module.main()
    assert gate.boundary.calls == []


@pytest.mark.parametrize("path", ["agent/unlisted/__init__.py", "agent/application.py"])
def test_unexpected_top_level_agent_modules_fail_before_commands(gate, path):
    extra = gate.root / path
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("# outside declared core modules\n")
    gate.boundary.python_report = python_report(sorted((gate.root / "agent").rglob("*.py")))
    with pytest.raises(SystemExit, match="Core scope failed"):
        gate.module.main()
    assert gate.boundary.calls == []


@pytest.mark.parametrize("folder", ["app", "apps", "ui", "frontend", "web", "desktop"])
def test_application_folders_fail_before_commands(gate, folder):
    (gate.root / folder).mkdir()
    with pytest.raises(SystemExit, match=folder):
        gate.module.main()
    assert gate.boundary.calls == []


@pytest.mark.parametrize("language", ["python", "native"])
def test_failed_verification_subprocess_cannot_publish_qualification(gate, language):
    gate.boundary.fail = language
    with pytest.raises(subprocess.CalledProcessError):
        gate.module.main()
    assert not gate.output.exists()
    if language == "native":
        assert gate.boundary.calls == ["native"]


@pytest.mark.parametrize("language", ["python", "native"])
def test_missing_language_report_cannot_publish_qualification(gate, language):
    gate.boundary.omit_report = language
    with pytest.raises((FileNotFoundError, SystemExit)):
        gate.module.main()
    assert not gate.output.exists()
