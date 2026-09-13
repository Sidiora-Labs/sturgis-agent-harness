"""Check first-party formatting without rewriting or silently ignoring legacy debt.

The initial debt file records exact existing bytes, not path-wide exemptions.
Any new/modified formatting violation fails. --show-debt lists remaining debt;
--record-debt is permitted only when no baseline exists, for initial adoption.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / ".format-debt.json"
ROOTS = (
    "agent",
    "consumer",
    "model_gateway",
    "frontend",
    "native",
    "scripts",
    "tests",
    "documentation",
    "docs",
    ".github",
    ".devcontainer",
    ".research",
)
GENERATED = {
    "node_modules",
    "dist",
    ".astro",
    "__pycache__",
    ".git",
    ".venv",
    ".build",
    ".codegraph",
    ".data",
    ".pytest_cache",
    ".ruff_cache",
}
EXCLUDED = (
    "consumer/web",
    "native/memory/vendor",
    "documentation/coverage",
    "documentation/developers/src/content/docs/reference/files",
    ".research/9router-reference",
)
PRETTIER = {
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".css",
    ".json",
    ".md",
    ".html",
    ".yaml",
    ".yml",
}
CPP = {".cpp", ".cc", ".cxx", ".h", ".hpp"}
VERSIONS = {"ruff": "0.16.7", "prettier": "3.9.6", "clang-format": "18.1.8"}


def run(command, allowed=(0,)):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode not in allowed:
        raise ValueError(
            f"{command[0]} failed ({result.returncode}):\n{result.stdout}{result.stderr}"
        )
    return result.stdout


def inventory():
    paths = [path for path in ROOT.iterdir() if path.is_file()]
    for root in ROOTS:
        for directory, names, files in os.walk(ROOT / root, followlinks=False):
            names[:] = sorted(
                name
                for name in names
                if name not in GENERATED
                and not (Path(directory) / name).is_symlink()
                and (Path(directory) / name).relative_to(ROOT).as_posix() not in EXCLUDED
            )
            paths.extend(Path(directory) / name for name in files)
    return sorted(
        path.relative_to(ROOT).as_posix()
        for path in paths
        if not path.is_symlink()
        and path.name
        not in {
            "package-lock.json",
            "AGENTS.md",
            "CLAUDE.md",
            ".format-debt.json",
        }
        and path.suffix in PRETTIER | CPP | {".py"}
    )


def digest(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def policy():
    ruff = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["ruff"]
    return {
        "version": 1,
        "formatters": VERSIONS,
        "config_sha256": {
            **{
                path: digest(path)
                for path in (".prettierrc.json", ".editorconfig", ".clang-format")
            },
            "tool.ruff": hashlib.sha256(json.dumps(ruff, sort_keys=True).encode()).hexdigest(),
        },
    }


def violations(paths):
    binaries = {
        "ruff": str(Path(sys.executable).parent / "ruff"),
        "clang-format": str(Path(sys.executable).parent / "clang-format"),
        "prettier": str(ROOT / "node_modules/.bin/prettier"),
    }
    for name, binary in binaries.items():
        if VERSIONS[name] not in run([binary, "--version"]).split():
            raise ValueError(f"Install the pinned {name} {VERSIONS[name]} with make bootstrap")
    problems = {}
    python = [path for path in paths if Path(path).suffix == ".py"]
    if python:
        diagnostics = json.loads(
            run(
                [binaries["ruff"], "format", "--check", "--output-format", "json", *python],
                allowed=(0, 1),
            )
        )
        for diagnostic in diagnostics:
            if diagnostic["code"] != "unformatted":
                raise ValueError(f"Python formatter error: {diagnostic}")
            problems[Path(diagnostic["filename"]).relative_to(ROOT).as_posix()] = "ruff"
    web = [path for path in paths if Path(path).suffix in PRETTIER]
    if web:
        output = run([binaries["prettier"], "--list-different", *web], allowed=(0, 1))
        problems.update({path: "prettier" for path in output.splitlines()})
    for path in paths:
        if Path(path).suffix in CPP:
            replacements = ET.fromstring(
                run([binaries["clang-format"], "--output-replacements-xml", path])
            )
            if replacements.attrib.get("incomplete_format") == "true":
                raise ValueError(f"clang-format could not fully parse {path}")
            if list(replacements):
                problems[path] = "clang-format"
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show-debt", action="store_true")
    parser.add_argument("--record-debt", action="store_true")
    options = parser.parse_args()
    paths = inventory()
    problems = violations(paths)
    expected = policy()
    if options.record_debt:
        if BASELINE.exists():
            raise ValueError(
                "Refusing to replace existing formatting debt; format changed files instead"
            )
        BASELINE.write_text(
            json.dumps(
                {
                    **expected,
                    "description": "Unchanged historical formatting debt only; changed or new violations fail.",
                    "files": {
                        path: {"formatter": formatter, "sha256": digest(path)}
                        for path, formatter in sorted(problems.items())
                    },
                },
                indent=2,
            )
            + "\n"
        )
    baseline = json.loads(BASELINE.read_text())
    if any(baseline.get(key) != value for key, value in expected.items()):
        raise ValueError(
            "Formatter versions/config changed; explicitly review the debt policy before adopting it"
        )
    debt, failures = [], []
    for path, formatter in sorted(problems.items()):
        if baseline.get("files", {}).get(path) == {"formatter": formatter, "sha256": digest(path)}:
            debt.append(path)
        else:
            failures.append(path)
    print(
        f"Checked {len(paths)} first-party files; {len(debt)} unchanged legacy formatting deviations (.format-debt.json)."
    )
    if options.show_debt:
        for path in debt:
            print(f"  {problems[path]}: {path}")
    for path in failures:
        print(f"FORMAT REQUIRED ({problems[path]}): {path}")
    if failures:
        raise SystemExit(1)
    print(
        "No new or changed formatting violations. This does not declare the legacy tree fully formatted."
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, ET.ParseError) as error:
        raise SystemExit(f"Formatting check failed: {error}") from None
