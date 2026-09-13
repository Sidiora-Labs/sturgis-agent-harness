"""Formatting adoption cannot excuse new code, changed debt or invalid source."""

import json
import sys

import pytest

from scripts import check_format


@pytest.fixture
def repository(tmp_path, monkeypatch):
    original = check_format.ROOT
    for name in (".prettierrc.json", ".editorconfig", ".clang-format", "pyproject.toml"):
        (tmp_path / name).write_bytes((original / name).read_bytes())
    (tmp_path / "node_modules").symlink_to(original / "node_modules", target_is_directory=True)
    monkeypatch.setattr(check_format, "ROOT", tmp_path)
    monkeypatch.setattr(check_format, "BASELINE", tmp_path / ".format-debt.json")
    return tmp_path


def invoke(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["check_format.py", *arguments])
    check_format.main()


def test_actual_formatters_enforce_modified_and_new_files_without_rewriting(
    repository, monkeypatch, capsys
):
    fixtures = {
        "old.py": "result=1\n",
        "old.mjs": "const result=1;\n",
        "old.cpp": "int main(){return 0;}\n",
    }
    for name, content in fixtures.items():
        (repository / name).write_text(content)
    invoke(monkeypatch, "--record-debt")
    baseline = json.loads(check_format.BASELINE.read_text())
    assert {row["formatter"] for row in baseline["files"].values()} == {
        "ruff",
        "prettier",
        "clang-format",
    }
    invoke(monkeypatch, "--show-debt")
    output = capsys.readouterr().out
    assert "ruff: old.py" in output
    assert "prettier: old.mjs" in output
    assert "clang-format: old.cpp" in output
    for name, content in fixtures.items():
        assert (repository / name).read_text() == content
    (repository / "old.mjs").write_text("const result=2;\n")
    with pytest.raises(SystemExit) as failure:
        invoke(monkeypatch)
    assert failure.value.code == 1
    assert (repository / "old.mjs").read_text() == "const result=2;\n"
    (repository / "old.mjs").write_text(fixtures["old.mjs"])
    (repository / "new.py").write_text("result=2\n")
    with pytest.raises(SystemExit) as failure:
        invoke(monkeypatch)
    assert failure.value.code == 1
    (repository / "new.py").write_text("result = 2\n")
    invoke(monkeypatch)


def test_baseline_cannot_be_replaced_and_config_changes_require_explicit_review(
    repository, monkeypatch
):
    (repository / "old.py").write_text("result=1\n")
    invoke(monkeypatch, "--record-debt")
    original = check_format.BASELINE.read_bytes()
    with pytest.raises(ValueError, match="Refusing to replace"):
        invoke(monkeypatch, "--record-debt")
    assert check_format.BASELINE.read_bytes() == original
    (repository / ".prettierrc.json").write_text('{"printWidth":100}\n')
    with pytest.raises(ValueError, match="config changed"):
        invoke(monkeypatch)
    assert check_format.BASELINE.read_bytes() == original


def test_invalid_python_cannot_be_recorded_as_formatting_debt(repository, monkeypatch):
    (repository / "invalid.py").write_text("def broken(\n")
    with pytest.raises(ValueError, match="formatter error|failed"):
        invoke(monkeypatch, "--record-debt")
    assert not check_format.BASELINE.exists()


def test_inventory_includes_tests_manuals_research_and_hidden_tooling_but_not_generated_or_vendor(
    repository,
):
    authored = {
        "frontend/src/app.tsx",
        "tests/test_source.py",
        "docs/guide.md",
        "documentation/users/src/content/docs/guide.md",
        ".github/workflows/ci.yaml",
        ".devcontainer/devcontainer.json",
        ".research/design.md",
        "README.md",
    }
    excluded = {
        "consumer/web/assets/bundle.js",
        "native/memory/vendor/vendor.cpp",
        "documentation/developers/src/content/docs/reference/files/generated.md",
        "documentation/coverage/generated.json",
        ".research/9router-reference/vendor.js",
        "frontend/node_modules/dependency.js",
        "AGENTS.md",
        "package-lock.json",
    }
    for name in authored | excluded:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source\n")
    (repository / "tests/link.py").symlink_to(repository / "tests/test_source.py")
    paths = set(check_format.inventory())
    assert authored <= paths
    assert not excluded & paths
    assert "tests/link.py" not in paths
