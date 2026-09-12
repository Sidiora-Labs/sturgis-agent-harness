"""Exercise real syntax extraction, complete inventory and tamper rejection."""

import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.docs_reference import audit, build
from scripts.docs_reference.builder import read_record
from scripts.docs_reference.cpp_parser import parse_cpp
from scripts.docs_reference.clang_parser import parse_translation_unit
from scripts.docs_reference.inventory import ReferenceError, inventory
from scripts.docs_reference.javascript import parse_javascript
from scripts.docs_reference.python_parser import parse_python
from scripts.docs_reference.render import anchor


PYTHON = '''"""Module contract."""
from typing import overload
class Service(Base):
    """A literal source description."""
    @decorate(flag={"text": "</script><img src=x onerror=alert(1)>"})
    async def run(self, value: int = 3, /, *items: str, retry: bool = False, **extra) -> str:
        """Run documentation, preserved as evidence."""
        def nested(value: str = "x"):
            return value
        if not retry:
            raise ValueError("denied") from cause
        result = await client.read(value)
        db.execute("SELECT body FROM events WHERE id=?", (value,))
        self.result = result
        return nested(result)
@overload
def pick(value: int) -> int: ...
@overload
def pick(value: str) -> str: ...
def stream():
    yield 1
    yield from [2]
    raise
def other():
    def nested(): return None
    return nested()
'''


def fixture_tree(tmp_path):
    root = tmp_path / "repo"
    paths = {
        "agent/example.py": PYTHON,
        "frontend/src/view.tsx": 'export type Item = {name: string}; export async function render(item: Item, count = 2): Promise<string> { if (!item.name) throw new Error("empty"); return await fetch(item.name).then(x => x.text()); }\n',
        "native/sample.cpp": 'namespace test { using Count = int; struct Value { int get(int n=1) const { if(n<0) throw n; return n; } }; int same(int x); double same(double x); }\n',
        "packaging/Dockerfile": "FROM scratch\n",
        "frontend/src/style.css": "body { color: red; }\n",
        "pyproject.toml": '[project]\nname="fixture"\n',
        "package-lock.json": '{"lockfileVersion":3,"packages":{"":{"name":"fixture"}}}\n',
        "documentation/requirements.txt": "tree-sitter==0.26.0\n",
        "documentation/developers/src/content/docs/reference/files/old.md": "generated\n",
        "consumer/web/assets/generated.js": "doNotRead()\n",
        "native/vendor/thirdparty.cpp": "not C++\n",
        ".env": "PRIVATE_SECRET_NEVER_INCLUDE=secret\n",
    }
    for name, data in paths.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
    return root, tmp_path / "pages", tmp_path / "manifest.json"


def test_python_qualified_overloads_and_complete_contracts():
    parsed = parse_python(PYTHON)
    symbols = parsed["symbols"]
    assert len(symbols) == sum(isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(ast.parse(PYTHON)))
    run = next(s for s in symbols if s["qualified_name"] == "Service.run")
    assert run["kind"] == "async_function"
    assert run["signature"].endswith("-> str:")
    assert run["start_line"] == 5 and run["line"] == 6
    assert run["returns_annotation"] == "str"
    assert run["parameters"][1] == {"name": "value", "kind": "positional_only", "annotation": "int", "has_default": True, "default": "3"}
    assert run["parameters"][3]["name"] == "retry" and run["parameters"][3]["default"] == "False"
    assert run["parameters"][-1]["kind"] == "variadic_keyword"
    assert "decorate(flag=" in run["decorators"][0]["source"]
    assert any(f["kind"] == "raise" and f["expression"] == 'ValueError("denied")' for f in run["facts"])
    assert any(f["kind"] == "sql_literal_argument" for f in run["facts"])
    assert any(f["kind"] == "await" for f in run["facts"])
    assert "Service.run.<locals>.nested" in {s["qualified_name"] for s in symbols}
    assert "other.<locals>.nested" in {s["qualified_name"] for s in symbols}
    assert not any(f["kind"] == "return" and f["expression"] == "value" for f in run["facts"])
    picks = [s for s in symbols if s["name"] == "pick"]
    assert len(picks) == 2 and anchor(picks[0]) != anchor(picks[1])
    stream = next(s for s in symbols if s["name"] == "stream")
    assert {"yield", "yieldfrom", "raise"} <= {f["kind"] for f in stream["facts"]}


def test_python_keyword_required_annotation_complex_default_and_same_line_signature():
    parsed = parse_python('def f(a: dict[str, int] = {"x": 1}, *, required: str, other=None): return a\n')
    fn = parsed["symbols"][0]
    assert fn["signature"] == 'def f(a: dict[str, int] = {"x": 1}, *, required: str, other=None):'
    assert fn["parameters"][1]["has_default"] is False
    assert fn["parameters"][2]["default"] == "None"


def test_real_babel_nesting_defaults_overloads_and_throw():
    source = '''export interface User { name: string }
export type Alias = User;
export class Service {
  async run(value: string = "<script>", ...rest: number[]): Promise<string> {
    const nested = (x: number = 1) => x + 1;
    if (!value) throw new Error("empty");
    localStorage.setItem("key", value);
    return await fetch(value).then(response => response.text());
  }
}
function overloaded(a: string): string;
function overloaded(a: number): number;
function overloaded(a: any) { return a; }
const wrapped = wrapper((first: number) => (second: number) => first + second);
let resolve: (response: string) => void = () => {};
'''
    parsed = parse_javascript("fixture.ts", source)
    names = {s["qualified_name"] for s in parsed["symbols"]}
    assert {"User", "Alias", "Service", "Service.run", "Service.run.nested"} <= names
    overloaded = [s for s in parsed["symbols"] if s["name"] == "overloaded"]
    assert len(overloaded) == 3 and len({anchor(s) for s in overloaded}) == 3
    method = next(s for s in parsed["symbols"] if s["qualified_name"] == "Service.run")
    assert method["parameters"][0]["default"] == '"<script>"'
    assert method["parameters"][1]["kind"] == "rest"
    assert method["returns_annotation"] == ": Promise<string>"
    assert {"call", "raise", "return", "await", "branch_or_control", "possible_io_or_mutation_call"} <= {f["kind"] for f in method["facts"]}
    assert any("<anonymous@" in s["name"] for s in parsed["symbols"])
    with pytest.raises(ReferenceError, match="Babel rejected"):
        parse_javascript("invalid.ts", "export function broken(")


def test_real_cpp_qualified_methods_declarations_aliases_and_errors():
    source = '''namespace test {
using Count = int;
typedef int Other;
struct A {
  A(int n = 1): n(n) {}
  int n;
  int get(int value) const;
};
int A::get(int value) const { if (value < 0) throw Error(); return value; }
int same(int x);
double same(double x);
auto callback = [](int n) { return n; };
}
'''
    parsed = parse_cpp(source)
    symbols = parsed["symbols"]
    assert {"test", "test::Count", "test::Other", "test::A", "test::A::A", "test::A::get"} <= {s["qualified_name"] for s in symbols}
    assert len([s for s in symbols if s["name"] == "same"]) == 2
    constructor = next(s for s in symbols if s["qualified_name"] == "test::A::A")
    assert constructor["parameters"][0]["default"] == "1"
    implementation = next(s for s in symbols if s["kind"] == "function_definition" and s["name"] == "A::get")
    assert {"raise", "return", "branch_or_control", "call"} <= {f["kind"] for f in implementation["facts"]}
    assert any(s["kind"] == "lambda_expression" for s in symbols)
    with pytest.raises(ReferenceError, match="parser errors"):
        parse_cpp("class Broken { void nope(")


def test_full_inventory_deterministic_pages_and_independent_audit(tmp_path):
    root, output, manifest = fixture_tree(tmp_path)
    first = build(root, output, manifest)
    assert first["files"] == 8 and first["metadata_only_files"] == 1
    original = manifest.read_bytes()
    assert audit(root, output, manifest)["verified"]
    assert build(root, output, manifest) == first
    assert manifest.read_bytes() == original
    page = (output / "agent/example.py.md").read_text()
    assert 'slug: "reference/files/agent/example.py"' in page
    assert "</script><img" not in page
    assert "&lt;/script&gt;&lt;img" in page
    for line in range(1, len(PYTHON.splitlines()) + 1):
        assert page.count(f'id="L{line}"') == 1
    contents = json.loads(manifest.read_text())
    assert all("PRIVATE_SECRET" not in json.dumps(row) for row in contents["files"])
    assert len(contents["inventory"]["excluded"]) >= 3
    assert first["rendered_browser_coverage_claimed"] is False


@pytest.mark.parametrize("mutation, expected", [
    ("new_source", "inventory drift"), ("missing_source", "inventory drift"),
    ("changed_source", "Source/contract/hash drift"), ("missing_page", "page inventory"),
    ("extra_page", "page inventory"), ("changed_page", "content changed"),
    ("missing_symbol", "symbol omission"), ("duplicate_file", "missing, duplicate"),
    ("missing_file_and_inventory", "inventory drift"), ("false_summary", "summary differs"),
])
def test_independent_audit_rejects_missing_source_symbols_and_tampering(tmp_path, mutation, expected):
    root, output, manifest = fixture_tree(tmp_path)
    build(root, output, manifest)
    data = json.loads(manifest.read_text())
    if mutation == "new_source": (root / "agent/new.py").write_text("def omitted(): pass\n")
    elif mutation == "missing_source": (root / "agent/example.py").unlink()
    elif mutation == "changed_source": (root / "agent/example.py").write_text(PYTHON + "\n# drift\n")
    elif mutation == "missing_page": (output / "agent/example.py.md").unlink()
    elif mutation == "extra_page": (output / "extra.md").write_text("unrelated")
    elif mutation == "changed_page": (output / "agent/example.py.md").write_text("tampered")
    elif mutation == "missing_symbol": data["files"][0]["structure"]["symbols"].pop()
    elif mutation == "duplicate_file": data["files"].append(data["files"][0])
    elif mutation == "missing_file_and_inventory":
        data["files"].pop(0); data["inventory"]["files"].pop(0)
    elif mutation == "false_summary": data["summary"]["symbols"] = 0
    manifest.write_text(json.dumps(data))
    with pytest.raises(ReferenceError, match=expected):
        audit(root, output, manifest)


def test_inventory_symlinks_and_unsafe_outputs_rejected(tmp_path):
    root, output, manifest = fixture_tree(tmp_path)
    with pytest.raises(ReferenceError, match="overwrite"):
        build(root, output, root / "pyproject.toml")
    with pytest.raises(ReferenceError, match="outside inventoried"):
        build(root, root / "agent/generated", manifest)
    (root / "agent/link.py").symlink_to(root / "agent/example.py")
    with pytest.raises(ReferenceError, match="symlink"):
        inventory(root)


def test_real_cli_build_audit_and_clean_failure(tmp_path):
    root, output, manifest = fixture_tree(tmp_path)
    base = [sys.executable, "-m", "scripts.docs_reference"]
    args = ["--root", str(root), "--output", str(output), "--manifest", str(manifest)]
    completed = subprocess.run([*base, "build", *args], text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"]
    checked = subprocess.run([*base, "audit", *args], text=True, capture_output=True)
    assert checked.returncode == 0 and json.loads(checked.stdout)["verified"]
    (root / "agent/example.py").write_text("def broken(")
    failed = subprocess.run([*base, "audit", *args], text=True, capture_output=True)
    assert failed.returncode == 1 and json.loads(failed.stderr)["ok"] is False
    assert "Traceback" not in failed.stderr


def test_binary_metadata_and_astro_source_positions(tmp_path):
    p = tmp_path / "icon.bin";p.write_bytes(b"\xff\x00")
    record, text = read_record(tmp_path, "icon.bin")
    assert text is None and record["structure"]["parser"] == "binary-asset-metadata"
    p = tmp_path / "Page.astro"
    p.write_text("---\nfunction title(name: string) { return name; }\n---\n<h1>{title('test')}</h1>\n")
    record, text = read_record(tmp_path, "Page.astro")
    assert record["structure"]["symbols"][0]["line"] == 2
    assert "template" in record["structure"]["parser_quality"]


def test_python_type_alias_class_fields_and_unicode_byte_locations():
    source = '''type Result[T: str] = tuple[T, int]
class Config:
    label: str = "café"
    def read(self, key="clé"):
        return "café:" + key
'''
    parsed = parse_python(source)
    alias = parsed["symbols"][0]
    assert alias["kind"] == "type_alias" and alias["parameters"][0]["annotation"] == "str"
    config = parsed["symbols"][1]
    assert config["fields"][0]["default"] == '"café"'
    read = parsed["symbols"][2]
    assert read["parameters"][1]["default"] == '"clé"'
    assert read["facts"][0]["source"] == 'return "café:" + key'


def test_named_and_anonymous_python_lambdas_are_not_lost_or_misattributed():
    parsed = parse_python("def owner():\n    named = lambda x=1: other(x)\n    return map(lambda y: y + 1, [])\n")
    assert parsed["parser_callable_count"] == 3
    named = next(s for s in parsed["symbols"] if s["name"] == "named")
    assert named["qualified_name"] == "owner.<locals>.named" and named["parameters"][0]["default"] == "1"
    assert any(f["kind"] == "implicit_return" and f["expression"] == "other(x)" for f in named["facts"])
    owner = parsed["symbols"][0]
    assert not any(f["expression"] == "other" for f in owner["facts"])


def test_compiler_ast_valid_unsigned_casts_required_name_and_own_file_filter(tmp_path):
    (tmp_path / "external.hpp").write_text("inline int external(int x) { return x; }\n")
    filename = tmp_path / "actual.cpp"
    source = '''#include "external.hpp"
namespace own {
struct State { int count; };
int frame(int required = 1) { unsigned n = unsigned(required) << 2; if(n) return external(n); throw n; }
int frame(double required);
}
'''
    filename.write_text(source)
    parsed = parse_translation_unit(filename, source)
    assert parsed["parser"] == "libclang-translation-unit"
    assert all(s["name"] != "external" for s in parsed["symbols"])
    frames = [s for s in parsed["symbols"] if s["name"] == "frame"]
    assert len(frames) == 2
    definition = next(s for s in frames if s["is_definition"])
    assert definition["parameters"][0]["default"] == "1"
    call = next(f for f in definition["facts"] if f["kind"] == "call")
    assert call["resolved_callee"] == "external"
    assert {"raise", "return", "branch_or_control", "assignment_or_mutation"} <= {f["kind"] for f in definition["facts"]}
    assert next(s for s in parsed["symbols"] if s["name"] == "State")["fields"][0]["annotation"] == "int"
    with pytest.raises(ReferenceError, match="compiler rejected"):
        parse_translation_unit(filename, "int broken( {")


def test_actual_native_compiler_contracts_use_cmake_and_retain_sql_and_effect_calls():
    path = Path(__file__).resolve().parents[2] / "native/memory/engine.cpp"
    parsed = parse_translation_unit(path, path.read_text())
    assert "-std=c++20" in parsed["compiler_arguments"]
    assert any("native/memory/vendor" in arg for arg in parsed["compiler_arguments"])
    assert any(s["qualified_name"] == "sturgis::Engine::dispatch" for s in parsed["symbols"])
    facts = [f for s in parsed["symbols"] for f in s["facts"]]
    assert any(f["kind"] == "sql_literal_candidate" for f in facts)
    assert any(f["kind"] == "possible_io_or_mutation_call" and "sqlite" in f["expression"] for f in facts)
    assert any(f.get("resolved_callee", "").startswith("sturgis::") for f in facts)


def test_guide_manifest_links_are_hashed_and_do_not_define_inventory(tmp_path):
    root, output, manifest = fixture_tree(tmp_path)
    mapping = root / "documentation/coverage/developers.json"
    mapping.parent.mkdir(parents=True)
    mapping.write_text(json.dumps({"pages": [
        {"path": "guides/core-owners.md", "title": "Core & ownership", "sources": ["agent/"]},
        {"path": "source-contracts.md", "title": "Contracts", "sources": ["agent/*.py"]},
        {"path": "index.md", "title": "Manual home", "sources": ["agent/"]},
        {"path": "guides/nested/index.mdx", "title": "Nested home", "sources": ["agent/"]},
    ]}))
    build(root, output, manifest)
    page = (output / "agent/example.py.md").read_text()
    assert 'href="/guides/core-owners/"' in page and "Core &amp; ownership" in page
    assert 'href="/guides/source-contracts/"' in page
    assert 'href="/guides/"' in page and 'href="/guides/nested/"' in page
    assert 'href="/guides/index/"' not in page and 'href="/guides/nested/index/"' not in page
    assert "### Arguments" not in page and "<h3>Arguments and declared contract</h3>" in page
    assert audit(root, output, manifest)["verified"]
    data = json.loads(manifest.read_text())
    assert data["guide_manifest"]["path"] == "documentation/coverage/developers.json"
    mapping.write_text('{"pages":[]}')
    with pytest.raises(ReferenceError, match="Guide manifest drift"):
        audit(root, output, manifest)


def test_new_generated_cache_does_not_change_authored_denominator(tmp_path):
    root, output, manifest = fixture_tree(tmp_path)
    build(root, output, manifest)
    generated = root / "documentation/developers/dist/page.html"
    generated.parent.mkdir(parents=True)
    generated.write_text("<h1>generated</h1>")
    assert audit(root, output, manifest)["verified"]


def test_invalid_guide_manifest_empty_tree_stale_pages_and_manifest(tmp_path):
    root, output, manifest = fixture_tree(tmp_path)
    mapping = tmp_path / "bad-guide.json"
    mapping.write_text('{"pages":[{"path":"../escape.md","sources":["agent/"]}]}')
    with pytest.raises(ReferenceError, match="guide path"):
        build(root, output, manifest, mapping)
    mapping.write_text('{"pages": false}')
    with pytest.raises(ReferenceError, match="pages must"):
        build(root, output, manifest, mapping)
    empty = tmp_path / "empty"; empty.mkdir()
    with pytest.raises(ReferenceError, match="No authored"):
        build(empty, output, manifest)
    build(root, output, manifest)
    (output / "stale.md").write_text("stale")
    with pytest.raises(ReferenceError, match="stale or unrelated"):
        build(root, output, manifest)
    manifest.write_text("not json")
    with pytest.raises(ReferenceError, match="cannot be read"):
        audit(root, output, manifest)


def test_stable_spec_configuration_and_executable_included_coordination_excluded(tmp_path):
    root, _, _ = fixture_tree(tmp_path)
    names = ["spec/specgen", "spec/workflow.kvx", "spec/feature/contracts.md",
             "spec/feature/spec.kvx", "spec/feature/spec.kvx.lock", "spec/feature/tasks.md"]
    for name in names:
        p = root / name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/usr/bin/env python3\ndef wrapper(): return 1\n" if name == "spec/specgen" else "declared configuration")
    found = inventory(root)
    assert set(names[:3]) <= set(found["files"])
    assert not set(names[3:]) & set(found["files"])
    record, _ = read_record(root, "spec/specgen")
    assert record["structure"]["symbols"][0]["name"] == "wrapper"


def test_standalone_historical_docs_have_complete_source_pages_and_audited_inventory(tmp_path):
    root, output, manifest = fixture_tree(tmp_path)
    for name in ("docs/qualification.md", "docs/sturgis-02/qualification.md"):
        source = root / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# Qualification history\nRecorded source evidence.\n")
    build(root, output, manifest)
    records = {r["path"]: r for r in json.loads(manifest.read_text())["files"]}
    for name in ("docs/qualification.md", "docs/sturgis-02/qualification.md"):
        page = (output / (name + ".md")).read_text()
        assert f'slug: "reference/files/{name}"' in page
        assert records[name]["line_count"] == 2 and records[name]["source_view"]
        assert 'id="L1"' in page and 'id="L2"' in page
    assert audit(root, output, manifest)["verified"]
    (root / "docs/new-contract.md").write_text("A newly authored contract.\n")
    with pytest.raises(ReferenceError, match="inventory drift"):
        audit(root, output, manifest)


def test_source_change_during_build_cannot_publish_manifest(tmp_path, monkeypatch):
    import scripts.docs_reference.builder as builder
    root, output, manifest = fixture_tree(tmp_path)
    original = builder.render
    changed = False
    def change_after_read(record, source):
        nonlocal changed
        page = original(record, source)
        if not changed:
            changed = True
            (root / "agent/example.py").write_text(PYTHON + "\n# admitted source drift\n")
        return page
    monkeypatch.setattr(builder, "render", change_after_read)
    with pytest.raises(ReferenceError, match="Source changed during"):
        build(root, output, manifest)
    assert not manifest.exists()
