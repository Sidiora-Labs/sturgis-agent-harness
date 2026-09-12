"""Documentation gate boundaries without repeating costly product qualification."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import docs_gate as gate


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    for name in ("agent", "consumer", "native/memory", "frontend", "scripts", "tests", "packaging",
                 "documentation/coverage", "documentation/users/src/content/docs", "documentation/developers/src/content/docs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in ("pyproject.toml", "CMakeLists.txt", "package.json", "package-lock.json"):
        (root / name).write_text("{}")
    (root / "consumer/api.py").write_text('READ_COMMANDS = frozenset("read.a".split())\nWRITE_COMMANDS = frozenset("write.a".split())\n')
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(gate, "DOCS", root / "documentation")
    monkeypatch.setattr(gate, "EVIDENCE", root / "evidence")
    monkeypatch.setattr(gate, "PYTHON", Path(sys.executable))
    return root


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def report(name):
    return json.loads((gate.EVIDENCE / name).read_text())


def manuals(audience, count=None):
    rows = [{"path": f"topic-{i}.md", "sources": ["consumer/api.py"], "commands": ["read.a", "write.a"],
             "examples": [{"id": f"example-{i}"}]} for i in range(count or (30 if audience == "users" else 35))]
    for row in rows:
        path = gate.DOCS / audience / "src/content/docs" / ("guides/" if audience == "developers" else "") / row["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        prose = "---\ntitle: Fixture guide\ndescription: Source grounded fixture\n---\nconsumer/api.py read.a write.a\n"
        prose += " ".join(["An admitted request retains identity while its recorded outcome remains separate from delivery."] * 18)
        path.write_text(prose)
    write_json(gate.DOCS / "coverage" / f"{audience}.json", {"pages": rows, "source_sha256": {"consumer/api.py": gate.sha(gate.ROOT / "consumer/api.py")}})
    return rows


def test_manual_denominator_uses_prose_instead_of_generated_source(repo):
    for audience in ("users", "developers"):
        rows = manuals(audience)
        generated = gate.DOCS / audience / "src/content/docs/reference/files/source.py.md"
        generated.parent.mkdir(parents=True)
        generated.write_text("Generated lines cannot substitute for explanatory prose.")
        result = gate.content(audience)
        assert result["status"] == "passed"
        assert result["manuals"] == result["examples"] == len(rows)
        assert result["mapped_commands"] == 2 and result["mapped_sources"] == 1
        assert "reference/files/source.py.md" not in gate.prose_pages(audience)
        assert report(f"content-{audience}.json") == result


def test_manual_errors_are_accumulated_and_written_before_failure(repo):
    rows = manuals("developers", 2)
    (gate.DOCS / "developers/src/content/docs/guides/topic-0.md").write_text("TODO very short\n")
    rows[0].update(examples=[], sources=["missing-source.py"])
    rows.extend([dict(rows[0]), {"path": "absent.md", "examples": []}])
    module = repo / "agent/uncovered/__init__.py"
    module.parent.mkdir()
    module.write_text("class Owner: pass\n")
    (repo / "consumer/uncovered.py").write_text("class ConsumerOwner: pass\n")
    write_json(gate.DOCS / "coverage/developers.json", {"pages": rows})
    with pytest.raises(ValueError, match="content qualification"):
        gate.content("developers")
    errors = "\n".join(report("content-developers.json")["errors"])
    for expected in ("Duplicate page", "Missing manual", "insufficient explanatory", "Missing title", "No worked example",
                     "Source mapping lacks", "Missing source", "Command mapping lacks", "Unfinished manual", "expected at least",
                     "Core modules missing", "Consumer implementation missing"):
        assert expected in errors


def test_users_must_map_every_live_command_even_if_text_mentions_it(repo):
    rows = manuals("users")
    for row in rows:
        row["commands"] = ["read.a"]
    write_json(gate.DOCS / "coverage/users.json", {"pages": rows})
    with pytest.raises(ValueError, match="content qualification"):
        gate.content("users")
    assert any("write.a" in e for e in report("content-users.json")["errors"])


@pytest.mark.parametrize("read, message", [
    ('frozenset("write.a".split())', "Ambiguous"),
    ('frozenset(["read.a"])', "split"),
    ('frozenset()', "Unrecognized"),
    ('frozenset("read.a".split(), "extra")', "Unrecognized"),
    ('frozenset(values="read.a".split())', "Unrecognized"),
])
def test_malformed_or_ambiguous_allowlists_fail_predictably(repo, read, message):
    (repo / "consumer/api.py").write_text(f'READ_COMMANDS = {read}\nWRITE_COMMANDS = frozenset("write.a".split())\n')
    with pytest.raises(ValueError, match=message):
        gate.command_inventory()


def test_missing_allowlist_is_not_a_valid_empty_contract(repo):
    (repo / "consumer/api.py").write_text('READ_COMMANDS = frozenset("read.a".split())\n')
    with pytest.raises(ValueError, match="Cannot independently"):
        gate.command_inventory()


def registrations(root, domain):
    (root / "consumer/api.py").write_text('READ_COMMANDS = frozenset("read.a work.one permission.one device.one own.one".split())\nWRITE_COMMANDS = frozenset("write.a plugins.install plugins.remove".split())\n')
    (root / "consumer/application.py").write_text(f'''
class AgentApplication:
    def own(self, target, *, mode="safe"): pass
    def register(self):
        add("read.a", handler, "key mode", "key")
        add("write.a", handler)
        domain = {domain}
        for action in ("install", "remove"):
            add("plugins." + action, handler, "name revision", "name")
''')
    (root / "consumer/work.py").write_text("class WorkViews:\n    async def one(self, item, /, *, strict, optional=None): pass\n")
    (root / "consumer/permissions.py").write_text("class Permissions:\n    def one(self, capability, *, reviewer=None): pass\n")
    (root / "consumer/device.py").write_text("class DeviceService:\n    def one(self, device, version=1): pass\n")


def test_bound_contracts_include_required_keywords_and_exclude_injected_reviewer(repo):
    registrations(repo, "{'work.one': self.work.one, 'permission.one': self.permissions.one, 'device.one': self.devices.one, 'own.one': self.own}")
    data = gate.command_contracts()
    assert data["work.one"] == {"required": ["item", "strict"], "optional": ["optional"], "classification": "read"}
    assert data["permission.one"] == {"required": ["capability"], "optional": [], "classification": "read"}
    assert data["device.one"]["optional"] == ["version"] and data["own.one"]["optional"] == ["mode"]
    assert data["plugins.install"] == {"required": ["name"], "optional": ["revision"], "classification": "write"}
    assert data["write.a"]["required"] == []


@pytest.mark.parametrize("domain, message", [
    ("registry()", "Unknown domain registration"), ("{'work.one': other.service}", "Unknown bound command owner"),
    ("{'work.one': self.work.one}", "Unresolved registered commands"),
])
def test_registration_changes_cannot_silently_shrink_the_documented_contract(repo, domain, message):
    registrations(repo, domain)
    with pytest.raises(ValueError, match=message):
        gate.command_contracts()


def test_unbounded_domain_signature_cannot_publish_a_bounded_contract(repo):
    registrations(repo, "{'work.one': self.work.one}")
    (repo / "consumer/work.py").write_text("class WorkViews:\n    def one(self, *args): pass\n")
    with pytest.raises(ValueError, match="Unbounded domain"):
        gate.command_contracts()


def test_contract_catalog_and_published_table_are_independently_checked(repo, monkeypatch):
    expected = {"read.a": {"required": ["key"], "optional": ["mode"], "classification": "read"},
                "write.a": {"required": [], "optional": [], "classification": "write"}}
    monkeypatch.setattr(gate, "command_contracts", lambda: expected)
    write_json(gate.DOCS / "coverage/commands.json", {"commands": {"read.a": {**expected["read.a"], "optional": []}}})
    page = gate.DOCS / "developers/src/content/docs/guides/consumer/http-commands.md"
    page.parent.mkdir(parents=True)
    tick = chr(96)
    page.write_text(f"| {tick}read.a{tick} | write | {tick}key{tick} | {tick}mode{tick} |\n")
    with pytest.raises(ValueError, match="Command contract documentation failed"):
        gate.contracts()
    errors = "\n".join(report("command-contracts.json")["errors"])
    for text in ("catalog does not match", "Stale required", "Published command schema differs", "Missing published schema row"):
        assert text in errors


def test_actual_markdown_parser_checks_fences_and_executes_only_the_scratch_tutorial(repo):
    pytest.importorskip("markdown_it")
    fence = chr(96) * 3
    page = gate.DOCS / "users/src/content/docs/examples.md"
    blocks = [("json", '{"request_id":"stable"}'), ("python", 'raise RuntimeError("compile only")'),
              ("bash", 'printf "%s" "syntax only"'), ("", "untyped text"), ("text", "not a program")]
    page.write_text("# Examples\n\n" + "\n".join(f"{fence}{lang}\n{body}\n{fence}\n" for lang, body in blocks))
    tutorial = gate.DOCS / "examples/first_mechanisms.py"
    tutorial.parent.mkdir(parents=True)
    tutorial.write_text("from pathlib import Path\nPath('tutorial-ran').write_text('executed fixture')\n")
    result = gate.examples()
    assert result["counts"] == {"json": 1, "python_compiled": 1, "shell_syntax": 1}
    assert (repo / "tutorial-ran").read_text() == "executed fixture"
    assert result["tutorial_sha256"] == gate.sha(tutorial)


def test_invalid_json_python_and_shell_are_reported_before_tutorial_execution(repo, monkeypatch):
    pytest.importorskip("markdown_it")
    fence = chr(96) * 3
    page = gate.DOCS / "users/src/content/docs/bad.md"
    page.write_text("\n".join(f"{fence}{lang}\n{body}\n{fence}\n" for lang, body in
                               [("json", '{"missing": }'), ("py", "def broken("), ("sh", "if then")]))
    calls = []
    monkeypatch.setattr(gate, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="Invalid copyable examples"):
        gate.examples()
    errors = report("examples.json")["errors"]
    assert len(errors) == 3 and all("users/bad.md:" in error for error in errors)
    assert calls == []


def recipe_fixture(monkeypatch):
    expected = {"read.a": {"required": ["key"], "optional": ["mode"], "classification": "read"},
                "write.a": {"required": [], "optional": [], "classification": "write"},
                "providers.credentials.put": {"required": ["name", "secret"], "optional": [], "classification": "write"}}
    monkeypatch.setattr(gate, "command_contracts", lambda: expected)
    explanation = "\n**Expected result.** Recorded outcome.\n**Recovery.** Reuse the admitted identity.\n"
    sections = {
        "read.a": "sturgis command AGENT_ID read.a --params-file - <<'JSON'\n" + '{"key":"example"}\nJSON\n' + explanation,
        "write.a": "sturgis --output-file receipt.json command AGENT_ID write.a --request-id stable\n" + explanation,
        "providers.credentials.put": "sturgis credential AGENT_ID model_key\n" + explanation,
    }
    path = gate.DOCS / "users/src/content/docs/reference/command-recipes.md"
    path.parent.mkdir(parents=True)
    return path, sections


def put_recipes(path, sections):
    path.write_text("\n".join(f"### {name}\n{body}" for name, body in sections.items()))


def test_recipes_cover_real_fields_write_identity_and_private_credential_prompt(repo, monkeypatch):
    path, sections = recipe_fixture(monkeypatch)
    put_recipes(path, sections)
    result = gate.recipes()
    assert result == {"status": "passed", "recipes": 3, "parameter_examples": 1, "private_credential_prompt": True, "errors": []}


@pytest.mark.parametrize("mutation, message", [
    ("missing", "exactly cover"), ("extra", "exactly cover"), ("outcome", "outcome or recovery"),
    ("cli", "executable CLI"), ("unknown_field", "fields differ"), ("missing_field", "fields differ"),
    ("ambiguous", "Ambiguous recipe"), ("array", "Ambiguous recipe"), ("identity", "retained request identity"),
    ("credential", "private prompt"),
])
def test_bad_recipe_cannot_hide_behind_a_complete_command_name_list(repo, monkeypatch, mutation, message):
    path, sections = recipe_fixture(monkeypatch)
    if mutation == "missing": del sections["read.a"]
    elif mutation == "extra": sections["unknown.command"] = sections["read.a"]
    elif mutation == "outcome": sections["read.a"] = sections["read.a"].replace("**Recovery.**", "Recovery")
    elif mutation == "cli": sections["read.a"] = sections["read.a"].replace("sturgis command", "something else")
    elif mutation == "unknown_field": sections["read.a"] = sections["read.a"].replace('{"key":"example"}', '{"key":"example","unknown":1}')
    elif mutation == "missing_field": sections["read.a"] = sections["read.a"].replace('{"key":"example"}', "{}")
    elif mutation == "ambiguous": sections["read.a"] += "\n<<'JSON'\n{}\nJSON\n"
    elif mutation == "array": sections["read.a"] = sections["read.a"].replace('{"key":"example"}', "[]")
    elif mutation == "identity": sections["write.a"] = sections["write.a"].replace("--request-id stable", "")
    elif mutation == "credential": sections["providers.credentials.put"] += "--params-file secrets.json\n"
    put_recipes(path, sections)
    with pytest.raises(ValueError, match="Command cookbook failed"):
        gate.recipes()
    assert any(message in error for error in report("command-recipes.json")["errors"])


def test_source_and_distribution_inventories_track_real_bytes_with_explicit_exclusions(repo):
    retained = ["agent/core.py", "consumer/web/assets/built.js", "frontend/src/view.ts", "documentation/shared/theme.css",
                "documentation/users/src/content/docs/manual.md"]
    omitted = ["native/memory/vendor/dependency.hpp", "frontend/node_modules/dependency.js", "documentation/coverage/reference.json",
               "documentation/developers/src/content/docs/reference/files/a.py.md", "documentation/dist/users/index.html",
               "tests/__pycache__/fixture.pyc"]
    for name in retained + omitted:
        p = repo / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(name)
    (repo / "agent/link.py").symlink_to(repo / "agent/core.py")
    before = gate.source_manifest()
    assert set(retained) <= set(before) and not set(omitted) & set(before)
    assert "agent/link.py" not in before
    (repo / "documentation/shared/theme.css").write_text("changed")
    assert gate.source_manifest() != before
    assert gate.distribution_manifest() == {"users/index.html": gate.sha(repo / "documentation/dist/users/index.html")}


def receipt_fixture(root):
    source = root / "spec/workflow.kvx"
    source.parent.mkdir()
    source.write_text('workflow = "stable"\n')
    page = gate.DOCS / "developers/src/content/docs/reference/files/spec/workflow.kvx.md"
    page.parent.mkdir(parents=True)
    page.write_text("exact generated reference")
    write_json(gate.DOCS / "coverage/reference.json", {"files": [{"path": "spec/workflow.kvx", "page": "spec/workflow.kvx.md",
                                                               "sha256": gate.sha(source), "page_sha256": gate.sha(page)}]})
    for audience in ("users", "developers"):
        write_json(gate.DOCS / "coverage" / f"{audience}.json", {"pages": []})
    served = gate.DOCS / "dist/users/index.html"
    served.parent.mkdir(parents=True)
    served.write_text("served build")
    gate.write_report("source-manifest.json", gate.source_manifest())
    gate.write_report("distribution-manifest.json", gate.distribution_manifest())
    receipt = {"status": "passed", "source_manifest_sha256": gate.sha(gate.EVIDENCE / "source-manifest.json"),
               "evidence_sha256": {"distribution-manifest.json": gate.sha(gate.EVIDENCE / "distribution-manifest.json")},
               "reference_manifest_sha256": gate.sha(gate.DOCS / "coverage/reference.json"),
               "coverage_input_sha256": gate.coverage_inputs(),
               "content_manifest_sha256": {a: gate.sha(gate.DOCS / "coverage" / f"{a}.json") for a in ("users", "developers")}}
    gate.write_report("qualification.json", receipt)
    return source, page


@pytest.mark.parametrize("mutation, message", [
    ("distribution", "distribution changed"), ("reference_manifest", "Source reference evidence changed"),
    ("reference_page", "Generated source reference changed"), ("content_mapping", "content mapping changed"),
    ("referenced_source", "source.*changed"), ("new_catalog", "coverage catalog changed"),
])
def test_receipt_binds_served_bytes_pages_catalogs_and_each_referenced_source(repo, mutation, message):
    source, page = receipt_fixture(repo)
    gate.verify_receipt()
    if mutation == "distribution": (gate.DOCS / "dist/users/index.html").write_text("new served bytes")
    elif mutation == "reference_manifest": (gate.DOCS / "coverage/reference.json").write_text('{"files":[]}')
    elif mutation == "reference_page": page.write_text("different generated source")
    elif mutation == "content_mapping": (gate.DOCS / "coverage/users.json").write_text('{"pages":["new"]}')
    elif mutation == "referenced_source": source.write_text('workflow = "changed"\n')
    elif mutation == "new_catalog": write_json(gate.DOCS / "coverage/new.json", {"new": True})
    with pytest.raises(ValueError, match=message):
        gate.verify_receipt()


def test_subprocess_wrapper_uses_repo_cwd_timeout_and_propagates_failure(repo):
    gate.run([sys.executable, "-c", "from pathlib import Path; Path('ran').write_text('yes')"], timeout=10)
    assert (repo / "ran").read_text() == "yes"
    with pytest.raises(subprocess.CalledProcessError):
        gate.run([sys.executable, "-c", "raise SystemExit(7)"], timeout=10)


def stub_full(monkeypatch):
    calls = []
    monkeypatch.setattr(gate, "run", lambda args, **kwargs: calls.append([str(a) for a in args]))
    monkeypatch.setattr(gate, "contracts", lambda: {"status": "passed"})
    monkeypatch.setattr(gate, "recipes", lambda: {"status": "passed"})
    monkeypatch.setattr(gate, "documentation_coverage", lambda: {"status": "passed"})
    monkeypatch.setattr(gate, "content", lambda audience: {"audience": audience, "status": "passed"})
    monkeypatch.setattr(gate, "sites", lambda: [{"status": "passed"}])
    monkeypatch.setattr(gate, "examples", lambda: {"status": "passed"})
    return calls


def test_full_orchestration_publishes_receipt_only_after_the_complete_sequence(repo, monkeypatch):
    receipt_fixture(repo)
    calls = stub_full(monkeypatch)
    result = gate.full()
    assert result["status"] == "passed" and result == report("qualification.json")
    assert len(calls) == 5
    assert calls[0][:2] == ["env", "COVERAGE_FILE=.build/coverage-docs-full"]
    assert calls[0][3:5] == ["-m", "pytest"]
    assert "--cov=scripts" in calls[0] and "--cov-branch" in calls[0]
    assert calls[0][-1] == "--cov-report=json:" + str(gate.EVIDENCE / "coverage.json")
    assert calls[1] == ["node", "--experimental-strip-types", "--test", "tests/documentation/navigation.test.mjs"]
    assert calls[2][1:4] == ["-m", "scripts.docs_reference", "build"]
    assert calls[3][1:4] == ["-m", "scripts.docs_reference", "audit"]
    assert calls[4] == ["node", "scripts/docs_journey.mjs"]
    assert "qualification.json" not in result["evidence_sha256"]
    gate.verify_receipt()


@pytest.mark.parametrize("failure", ["subprocess", "source_drift", "catalog_drift", "site"])
def test_orchestration_failure_removes_prior_passing_receipt(repo, monkeypatch, failure):
    receipt_fixture(repo)
    stub_full(monkeypatch)
    if failure == "subprocess":
        def fail(*args, **kwargs): raise subprocess.CalledProcessError(8, ["fixture"])
        monkeypatch.setattr(gate, "run", fail)
        expected = subprocess.CalledProcessError
    elif failure == "site":
        def fail(): raise ValueError("site failed")
        monkeypatch.setattr(gate, "sites", fail)
        expected = ValueError
    else:
        snapshots = iter([{"before": "hash"}, {"after": "hash"}])
        monkeypatch.setattr(gate, "source_manifest" if failure == "source_drift" else "coverage_inputs", lambda: next(snapshots))
        expected = ValueError
    with pytest.raises(expected):
        gate.full()
    assert not (gate.EVIDENCE / "qualification.json").exists()


def test_sites_build_before_any_rendered_check(repo, monkeypatch):
    sequence = []
    monkeypatch.setattr(gate, "run", lambda args: sequence.append(args))
    monkeypatch.setattr(gate, "rendered_links", lambda audience: sequence.append(audience) or {"audience": audience})
    assert len(gate.sites()) == 2
    assert sequence == [["npm", "--prefix", "documentation", "run", "build"], "users", "developers"]


@pytest.mark.parametrize("mode, options, expected", [
    ("content", ["--audience", "users"], ["content:users"]), ("sites", [], ["sites"]),
    ("links", [], ["links:users", "links:developers"]), ("examples", [], ["examples"]),
    ("full", [], ["full"]), ("verify-receipt", [], ["verify"]), ("close", [], ["verify", "close"]),
])
def test_cli_dispatch_has_no_implicit_close_or_duplicate_qualification(repo, monkeypatch, mode, options, expected):
    calls = []
    monkeypatch.setattr(sys, "argv", ["docs_gate.py", mode, *options])
    monkeypatch.setattr(gate, "content", lambda audience: calls.append("content:" + audience))
    monkeypatch.setattr(gate, "sites", lambda: calls.append("sites"))
    monkeypatch.setattr(gate, "rendered_links", lambda audience: calls.append("links:" + audience))
    monkeypatch.setattr(gate, "examples", lambda: calls.append("examples"))
    monkeypatch.setattr(gate, "full", lambda: calls.append("full"))
    monkeypatch.setattr(gate, "verify_receipt", lambda: calls.append("verify"))
    def close(args):
        assert args == ["cg", "docs", "close"]
        calls.append("close")
    monkeypatch.setattr(gate, "run", close)
    gate.main()
    assert calls == expected


def test_cli_requires_audience_and_reexecs_pinned_python_before_examples(repo, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["docs_gate.py", "content"])
    with pytest.raises(SystemExit) as error:
        gate.main()
    assert error.value.code == 2
    pinned = repo / "pinned-python"
    pinned.write_text("fixture")
    monkeypatch.setattr(gate, "PYTHON", pinned)
    monkeypatch.setattr(sys, "argv", ["docs_gate.py", "examples"])
    def replace(executable, argv):
        assert executable == str(pinned) and argv[0] == str(pinned) and argv[-1] == "examples"
        raise SystemExit(23)
    monkeypatch.setattr("os.execv", replace)
    with pytest.raises(SystemExit) as error:
        gate.main()
    assert error.value.code == 23


def test_stale_review_stamps_and_unmapped_frontend_cannot_pass_manual_coverage(repo):
    manuals("developers")
    manifest = gate.DOCS / "coverage/developers.json"
    data = json.loads(manifest.read_text())
    data["source_sha256"]["consumer/api.py"] = "old-review"
    data["source_sha256"]["missing-review.py"] = "missing"
    write_json(manifest, data)
    view = repo / "frontend/src/new.css"
    view.parent.mkdir(parents=True)
    view.write_text("body { color: red; }")
    with pytest.raises(ValueError, match="content qualification"):
        gate.content("developers")
    errors = "\n".join(report("content-developers.json")["errors"])
    assert "needs source review: consumer/api.py" in errors
    assert "needs source review: missing-review.py" in errors
    assert "Frontend implementation missing" in errors and "new.css" in errors


def coverage_fixture(root, gate_lines=10, gate_hits=9, builder_lines=10, builder_hits=9):
    files = {}
    for name, count, hits in (("scripts/docs_gate.py", gate_lines, gate_hits),
                              ("scripts/docs_reference/core.py", builder_lines, builder_hits)):
        source = root / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("".join(f"value_{i} = {i}\n" for i in range(count)))
        files[name] = {"executed_lines": list(range(1, hits + 1)), "missing_lines": list(range(hits + 1, count + 1)),
                       "excluded_lines": [], "summary": {"covered_lines": hits, "num_statements": count,
                       "missing_lines": count - hits, "excluded_lines": 0, "num_branches": 0, "covered_branches": 0}}
    write_json(gate.EVIDENCE / "coverage.json", {"files": files})
    return files


def test_coverage_floors_accept_exact_ninety_and_hash_every_discovered_source(repo):
    files = coverage_fixture(repo)
    result = gate.documentation_coverage()
    assert result["status"] == "passed"
    assert set(result["source_sha256"]) == set(files)
    assert all(row["line_percent"] == 90 for row in result["groups"].values())
    assert result == report("coverage-floors.json")


def test_coverage_accepts_real_module_docstring_hits_without_counting_them(repo):
    sources = [repo / "scripts/docs_gate.py", repo / "scripts/docs_reference/core.py"]
    for source in sources:
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('\"\"\"A module docstring produces a raw coverage hit.\"\"\"\n\nvalue = 1\nresult = value + 1\n')
    runner = repo / "exercise.py"
    runner.write_text("import runpy\n" + "".join(f"runpy.run_path({str(source)!r})\n" for source in sources))
    data = repo / "coverage-data"
    gate.EVIDENCE.mkdir()
    subprocess.run([sys.executable, "-m", "coverage", "run", "--rcfile=/dev/null",
                    "--data-file=" + str(data), "--branch", "--source=" + str(repo / "scripts"),
                    str(runner)], cwd=repo, check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "coverage", "json", "--rcfile=/dev/null",
                    "--data-file=" + str(data), "-o", str(gate.EVIDENCE / "coverage.json")],
                   cwd=repo, check=True, capture_output=True)
    actual = report("coverage.json")
    row = actual["files"]["scripts/docs_gate.py"]
    assert row["executed_lines"] == [1, 3, 4]
    assert row["summary"]["covered_lines"] == row["summary"]["num_statements"] == 2
    result = gate.documentation_coverage()
    assert all(group["covered_lines"] == group["executable_lines"] == 2
               for group in result["groups"].values())
    row["executed_lines"].append(2)
    write_json(gate.EVIDENCE / "coverage.json", actual)
    with pytest.raises(ValueError, match="denominator or hits"):
        gate.documentation_coverage()


@pytest.mark.parametrize("group", ["gate", "reference_builder"])
def test_coverage_floors_use_unrounded_ratios_and_do_not_average_groups(repo, group):
    if group == "gate":
        coverage_fixture(repo, gate_lines=1000, gate_hits=899, builder_lines=10, builder_hits=10)
    else:
        coverage_fixture(repo, gate_lines=10, gate_hits=10, builder_lines=1000, builder_hits=899)
    with pytest.raises(ValueError, match=group + " executable line coverage"):
        gate.documentation_coverage()
    assert report("coverage-floors.json")["groups"][group]["line_percent"] == 89.9


@pytest.mark.parametrize("mutation, message", [
    ("missing", "Missing coverage source"), ("unimported", "Missing coverage source"),
    ("excluded", "Excluded executable"), ("denominator", "denominator or hits"),
    ("overlap", "denominator or hits"), ("duplicate_hit", "denominator or hits"),
    ("duplicate_file", "Duplicate coverage source"), ("wrong_hits", "denominator or hits"),
])
def test_coverage_cannot_hide_files_exclusions_or_executable_denominator(repo, mutation, message):
    files = coverage_fixture(repo)
    row = files["scripts/docs_reference/core.py"]
    if mutation == "missing": del files["scripts/docs_reference/core.py"]
    elif mutation == "unimported": (repo / "scripts/docs_reference/unused.py").write_text("def never_imported(): return 1\n")
    elif mutation == "excluded":
        row["excluded_lines"] = [10]; row["summary"]["excluded_lines"] = 1
    elif mutation == "denominator": row["summary"]["num_statements"] = 9
    elif mutation == "overlap": row["missing_lines"] = [9]
    elif mutation == "duplicate_hit": row["executed_lines"].append(9)
    elif mutation == "duplicate_file": files[str(repo / "scripts/docs_reference/core.py")] = row
    elif mutation == "wrong_hits": row["summary"]["covered_lines"] = 10
    write_json(gate.EVIDENCE / "coverage.json", {"files": files})
    with pytest.raises(ValueError, match=message):
        gate.documentation_coverage()
    assert report("coverage-floors.json")["status"] == "failed"


def test_empty_builder_inventory_cannot_claim_a_floor(repo):
    write_json(gate.EVIDENCE / "coverage.json", {"files": {}})
    with pytest.raises(ValueError, match="inventory is empty"):
        gate.documentation_coverage()
