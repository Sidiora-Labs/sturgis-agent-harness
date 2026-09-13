"""Negative qualification cases for generated-site links and source-bound receipts."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('docs_gate', Path(__file__).parents[1] / 'scripts/docs_gate.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def page(body):
    return '<!doctype html><html><head><title>Sturgis</title></head><body><main><h1>Guide</h1>' + body + '</main></body></html>'


@pytest.fixture
def sites(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'DOCS', tmp_path)
    monkeypatch.setattr(gate, 'EVIDENCE', tmp_path/'evidence')
    root = tmp_path/'dist/users'
    root.mkdir(parents=True)
    return root


def test_rendered_links_resolve_directories_fragments_queries_and_assets(sites):
    (sites/'index.html').write_text(page('<a href="guide/?q=1#same">Guide</a><a href="#same">Home</a><p id="same">A</p><img src="image.svg"><a href="https://external.example/">External</a>'))
    (sites/'guide').mkdir()
    (sites/'guide/index.html').write_text(page('<p id="same">B</p><a href="../#same">Return</a>'))
    (sites/'image.svg').write_text('<svg/>')
    result = gate.rendered_links('users')
    assert result['pages'] == 2
    assert result['local_links'] == 4
    assert result['external_links_not_fetched'] == 1


@pytest.mark.parametrize('link', ['missing/', '#absent', 'guide/#absent', '/%2e%2e/outside'])
def test_rendered_missing_target_or_anchor_cannot_pass(sites, link):
    (sites/'index.html').write_text(page(f'<a href="{link}">Bad reference</a>'))
    (sites/'guide').mkdir()
    (sites/'guide/index.html').write_text(page('<p id="exists">Value</p>'))
    with pytest.raises(ValueError, match='rendered links failed'):
        gate.rendered_links('users')


def test_empty_or_semantically_blank_site_cannot_pass(sites):
    with pytest.raises(ValueError, match='No rendered'):
        gate.rendered_links('users')
    (sites/'index.html').write_text('<html><p>Blank</p></html>')
    with pytest.raises(ValueError, match='rendered links failed'):
        gate.rendered_links('users')


def test_site_cannot_silently_omit_underscored_source_pages(sites):
    docs = gate.DOCS/'users/src/content/docs/reference/files/agent'
    docs.mkdir(parents=True)
    (docs/'__init__.py.md').write_text('---\nslug: "reference/files/agent/__init__.py"\n---\nSource\n')
    (sites/'index.html').write_text(page('Home'))
    with pytest.raises(ValueError, match='rendered links failed'):
        gate.rendered_links('users')
    target = sites/'reference/files/agent/__init__.py'
    target.mkdir(parents=True)
    (target/'index.html').write_text(page('Source'))
    assert gate.rendered_links('users')['expected_routes'] == 1


def test_html_named_source_directory_and_complete_line_symbol_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'DOCS', tmp_path)
    monkeypatch.setattr(gate, 'EVIDENCE', tmp_path/'evidence')
    root = tmp_path/'dist/developers/reference/files/frontend/index.html'
    root.mkdir(parents=True)
    output = root/'index.html'
    output.write_text(page('<p id="L1">source</p><details id="symbol-main">Definition</details>'))
    coverage = tmp_path/'coverage'; coverage.mkdir()
    manifest = {'files':[{'path':'frontend/index.html','source_view':True,'line_count':2,
        'structure':{'symbols':[{'anchor':'symbol-main'}]}}]}
    (coverage/'reference.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='rendered links failed'):
        gate.rendered_links('developers')
    output.write_text(page('<p id="L1">source</p><p id="L2">line</p>'))
    with pytest.raises(ValueError, match='rendered links failed'):
        gate.rendered_links('developers')
    output.write_text(page('<p id="L1">source</p><p id="L2">line</p><details id="symbol-main">Definition</details>'))
    report = gate.rendered_links('developers')
    assert (report['pages'],report['source_lines'],report['source_symbols']) == (1,2,1)


def test_command_inventory_is_derived_from_actual_allowlist_and_rejects_unrecognized_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    (tmp_path/'consumer').mkdir()
    source = tmp_path/'consumer/api.py'
    source.write_text('READ_COMMANDS = frozenset("read.a read.b".split())\nWRITE_COMMANDS = frozenset("write.a".split())\n')
    assert gate.command_inventory() == {'READ_COMMANDS': {'read.a','read.b'}, 'WRITE_COMMANDS': {'write.a'}}
    source.write_text('READ_COMMANDS = frozenset("read.a new.feature".split())\nWRITE_COMMANDS = frozenset("write.a".split())\n')
    assert 'new.feature' in gate.command_inventory()['READ_COMMANDS']
    source.write_text('READ_COMMANDS = create_allowlist()\nWRITE_COMMANDS = frozenset("write.a".split())\n')
    with pytest.raises(ValueError, match='Unrecognized'):
        gate.command_inventory()


def test_reference_report_must_match_current_source_and_all_evidence(tmp_path, monkeypatch):
    evidence = tmp_path/'evidence'; evidence.mkdir()
    docs = tmp_path/'documentation'; (docs/'coverage').mkdir(parents=True)
    monkeypatch.setattr(gate, 'EVIDENCE', evidence)
    monkeypatch.setattr(gate, 'DOCS', docs)
    monkeypatch.setattr(gate, 'source_manifest', lambda: {'source.py': 'actual'})
    (evidence/'source-manifest.json').write_text(json.dumps({'source.py':'actual'}))
    (evidence/'browser.json').write_text('{"status":"passed"}')
    (docs/'coverage/reference.json').write_text('{"files":[]}')
    receipt = {'status':'passed', 'source_manifest_sha256':gate.sha(evidence/'source-manifest.json'),
        'evidence_sha256':{'browser.json':gate.sha(evidence/'browser.json')},
        'reference_manifest_sha256':gate.sha(docs/'coverage/reference.json')}
    (evidence/'qualification.json').write_text(json.dumps(receipt))
    gate.verify_receipt()
    (evidence/'browser.json').write_text('{"status":"failed"}')
    with pytest.raises(ValueError, match='evidence changed'):
        gate.verify_receipt()
    (evidence/'browser.json').write_text('{"status":"passed"}')
    monkeypatch.setattr(gate, 'source_manifest', lambda: {'source.py':'edited'})
    with pytest.raises(ValueError, match='absent or stale'):
        gate.verify_receipt()


def test_changed_optional_parameter_invalidates_documented_command_contract(tmp_path, monkeypatch):
    import ast
    import shutil
    project = Path(__file__).parents[1]
    (tmp_path/'consumer').mkdir()
    for name in ('application.py','api.py','work.py','permissions.py','device.py'):
        shutil.copyfile(project/'consumer'/name,tmp_path/'consumer'/name)
    docs = tmp_path/'documentation'
    (docs/'coverage').mkdir(parents=True)
    guide = docs/'developers/src/content/docs/guides/consumer/http-commands.md'
    guide.parent.mkdir(parents=True)
    shutil.copyfile(project/'documentation/coverage/commands.json',docs/'coverage/commands.json')
    shutil.copyfile(project/'documentation/developers/src/content/docs/guides/consumer/http-commands.md',guide)
    monkeypatch.setattr(gate,'ROOT',tmp_path)
    monkeypatch.setattr(gate,'DOCS',docs)
    monkeypatch.setattr(gate,'EVIDENCE',tmp_path/'evidence')
    assert gate.contracts()['commands'] == 95
    source = tmp_path/'consumer/work.py'
    tree = ast.parse(source.read_text())
    owner = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='WorkViews')
    fn = next(n for n in owner.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='cancel_work')
    fn.args.kwonlyargs.append(ast.arg(arg='new_optional'))
    fn.args.kw_defaults.append(ast.Constant(value=False))
    source.write_text(ast.unparse(ast.fix_missing_locations(tree)))
    with pytest.raises(ValueError,match='work.cancel'):
        gate.contracts()


def test_gateway_surface_new_resource_or_cli_option_requires_a_manual(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    monkeypatch.setattr(gate, 'DOCS', tmp_path/'documentation')
    monkeypatch.setattr(gate, 'EVIDENCE', tmp_path/'evidence')
    assert gate.gateway_surface()['status'] == 'absent'
    source = tmp_path/'model_gateway'
    source.mkdir()
    (source/'api.py').write_text('''
def routes():
    reads = {"models": handler}
    reads["settings"] = handler
    if resource == "discover": pass
    return Route(prefix + "/chat/completions", handler)
''')
    (source/'__main__.py').write_text('parser.add_argument("--port")\nparser.add_argument("serve")\n')
    guide = tmp_path/'documentation/users/src/content/docs/gateway.md'
    guide.parent.mkdir(parents=True)
    guide.write_text('/models /settings /discover /chat/completions --port')
    assert gate.gateway_surface()['status'] == 'passed'
    (source/'api.py').write_text((source/'api.py').read_text() + '\nreads["new-operation"] = handler\n')
    with pytest.raises(ValueError, match='new-operation'):
        gate.gateway_surface()
    guide.write_text(guide.read_text() + ' /new-operation')
    (source/'__main__.py').write_text('parser.add_argument("--new-limit")\n')
    with pytest.raises(ValueError, match='--new-limit'):
        gate.gateway_surface()
