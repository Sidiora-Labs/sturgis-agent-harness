"""Build and qualify both Sturgis documentation sites against independent source inventories."""
from __future__ import annotations

import argparse
import ast
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import unquote, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'documentation'
EVIDENCE = ROOT / 'evidence/sturgis-docs'
PYTHON = ROOT / '.venv/bin/python'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, *, timeout=900):
    print('+ ' + ' '.join(str(x) for x in args), flush=True)
    subprocess.run([str(x) for x in args], cwd=ROOT, check=True, timeout=timeout)


def write_report(name, data):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def command_inventory():
    """Read the production HTTP allowlists without importing or executing the service."""
    tree = ast.parse((ROOT / 'consumer/api.py').read_text())
    result = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {'READ_COMMANDS', 'WRITE_COMMANDS'}:
                    call = node.value
                    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'frozenset' and len(call.args) == 1 and not call.keywords):
                        raise ValueError('Unrecognized consumer command declaration; update the independent extractor')
                    split = call.args[0]
                    if not (isinstance(split, ast.Call) and not split.args and not split.keywords and isinstance(split.func, ast.Attribute) and split.func.attr == 'split' and isinstance(split.func.value, ast.Constant) and isinstance(split.func.value.value, str)):
                        raise ValueError('Unrecognized command split declaration')
                    result[target.id] = set(split.func.value.value.split())
    if set(result) != {'READ_COMMANDS', 'WRITE_COMMANDS'}:
        raise ValueError('Cannot independently enumerate HTTP commands')
    if result['READ_COMMANDS'] & result['WRITE_COMMANDS']:
        raise ValueError('Ambiguous command classification')
    return result


def command_contracts():
    """Independently derive registered request fields, including bound domain signatures."""
    application = ast.parse((ROOT/'consumer/application.py').read_text())
    result = {}
    def add(name, allowed, required):
        result[name] = {'required': sorted(required), 'optional': sorted(set(allowed)-set(required))}
    for node in ast.walk(application):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'add' and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            allowed = ast.literal_eval(node.args[2]).split() if len(node.args)>2 else []
            required = ast.literal_eval(node.args[3]).split() if len(node.args)>3 else []
            add(node.args[0].value, allowed, required)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id=='domain' for t in node.targets):
            if not isinstance(node.value, ast.Dict):
                raise ValueError('Unknown domain registration form')
            for name_node, handler in zip(node.value.keys, node.value.values):
                name = ast.literal_eval(name_node)
                parts = ast.unparse(handler).split('.')
                if parts[:2] == ['self','work']:
                    source, owner = 'consumer/work.py', 'WorkViews'
                elif parts[:2] == ['self','permissions']:
                    source, owner = 'consumer/permissions.py', 'Permissions'
                elif parts[:2] == ['self','devices']:
                    source, owner = 'consumer/device.py', 'DeviceService'
                elif len(parts)==2 and parts[0]=='self':
                    source, owner = 'consumer/application.py', 'AgentApplication'
                else:
                    raise ValueError(f'Unknown bound command owner: {parts}')
                parsed = ast.parse((ROOT/source).read_text())
                cls = next(n for n in parsed.body if isinstance(n,ast.ClassDef) and n.name==owner)
                fn = next(n for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==parts[-1])
                if fn.args.vararg or fn.args.kwarg:
                    raise ValueError(f'Unbounded domain signature: {name}')
                positional = [*fn.args.posonlyargs,*fn.args.args]
                default_start = len(positional)-len(fn.args.defaults)
                required = [a.arg for i,a in enumerate(positional) if a.arg!='self' and i<default_start]
                required += [a.arg for a,d in zip(fn.args.kwonlyargs,fn.args.kw_defaults) if d is None]
                allowed = [a.arg for a in [*positional,*fn.args.kwonlyargs] if a.arg not in {'self','reviewer'}]
                add(name,allowed,required)
        if isinstance(node,ast.For) and isinstance(node.target,ast.Name) and node.target.id=='action':
            for call in ast.walk(node):
                if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='add' and call.args and isinstance(call.args[0],ast.BinOp) and isinstance(call.args[0].left,ast.Constant) and call.args[0].left.value=='plugins.':
                    for action in ast.literal_eval(node.iter):
                        add('plugins.'+action,ast.literal_eval(call.args[2]).split(),ast.literal_eval(call.args[3]).split())
    inventory = command_inventory()
    public = inventory['READ_COMMANDS']|inventory['WRITE_COMMANDS']
    if public-set(result):
        raise ValueError('Unresolved registered commands: '+', '.join(sorted(public-set(result))))
    return {name:{**result[name],'classification':'read' if name in inventory['READ_COMMANDS'] else 'write'} for name in sorted(public)}


def contracts():
    """Reject stale field tables even when command names have not changed."""
    expected = command_contracts()
    stored = json.loads((DOCS/'coverage/commands.json').read_text())['commands']
    errors = []
    if set(stored)!=set(expected):
        errors.append('Command catalog does not match the current public command set')
    for name, row in expected.items():
        actual = stored.get(name,{})
        if any(sorted(actual.get(key,[]))!=row[key] for key in ('required','optional')) or actual.get('classification')!=row['classification']:
            errors.append(f'Stale required/optional/access contract: {name}')
    guide = DOCS/'developers/src/content/docs/guides/consumer/http-commands.md'
    text = guide.read_text()
    for name,row in expected.items():
        table = next((line for line in text.splitlines() if line.startswith('| `'+name+'` |')),None)
        if table is None:
            errors.append(f'Missing published schema row: {name}')
            continue
        cells = [c.strip() for c in table.split('|')[1:-1]]
        if len(cells)!=4 or cells[1]!=row['classification'] or sorted(re.findall(r'`([^`]+)`',cells[2]))!=row['required'] or sorted(re.findall(r'`([^`]+)`',cells[3]))!=row['optional']:
            errors.append(f'Published command schema differs from source: {name}')
    report={'status':'failed' if errors else 'passed','commands':len(expected),'fields':sum(len(r['required'])+len(r['optional']) for r in expected.values()),'errors':errors}
    write_report('command-contracts.json',report)
    if errors:
        raise ValueError('Command contract documentation failed: '+ '; '.join(errors))
    print(json.dumps(report,indent=2),flush=True)
    return report


def prose_pages(audience):
    root = DOCS / audience / 'src/content/docs'
    return {p.relative_to(root).as_posix(): p for p in root.rglob('*.md') if 'reference/files' not in p.relative_to(root).as_posix()}


def recipes():
    """Require an individually explained, field-correct recipe for every live command."""
    text = (DOCS/'users/src/content/docs/reference/command-recipes.md').read_text()
    sections = re.findall(r'^### ([\w.]+)\n([\s\S]*?)(?=^### |\Z)', text, re.M)
    expected = command_contracts()
    errors = []
    if len(sections) != len(expected) or {name for name,_ in sections} != set(expected):
        errors.append('Cookbook recipes do not exactly cover the current public command set')
    field_examples = 0
    for name, body in sections:
        if name not in expected:
            continue
        if '**Expected result.**' not in body or '**Recovery.**' not in body:
            errors.append(f'Missing expected outcome or recovery: {name}')
        if name == 'providers.credentials.put':
            if 'sturgis credential AGENT_ID model_key' not in body or '--params-file' in body:
                errors.append('Credential recipe must use the dedicated private prompt')
            continue
        if not re.search(r'sturgis (?:--output-file \S+ )?command AGENT_ID '+re.escape(name)+r'(?:\s|$)',body):
            errors.append(f'Missing executable CLI invocation: {name}')
        encoded = re.findall(r"<<'JSON'\n([\s\S]*?)\nJSON(?:\n|$)",body)
        params = json.loads(encoded[0]) if len(encoded)==1 else {}
        if len(encoded)>1 or not isinstance(params,dict):
            errors.append(f'Ambiguous recipe parameters: {name}')
            continue
        fields = set(params)
        required = set(expected[name]['required'])
        allowed = required | set(expected[name]['optional'])
        if required-fields or fields-allowed:
            errors.append(f'Recipe fields differ from the current contract: {name}')
        field_examples += len(fields)
        if expected[name]['classification']=='write' and '--request-id ' not in body:
            errors.append(f'Mutation recipe lacks a retained request identity: {name}')
    report = {'status':'failed' if errors else 'passed','recipes':len(sections),'parameter_examples':field_examples,
              'private_credential_prompt':True,'errors':errors}
    write_report('command-recipes.json',report)
    if errors:
        raise ValueError('Command cookbook failed: '+ '; '.join(errors))
    return report


def gateway_surface():
    """Compare independently parsed gateway resources and CLI flags to manuals."""
    source = ROOT / 'model_gateway/api.py'
    if not source.parent.exists():
        return {'status': 'absent', 'resources': []}
    tree = ast.parse(source.read_text())
    resources = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == 'reads' for t in node.targets) and isinstance(node.value, ast.Dict):
                resources.update(ast.literal_eval(k) for k in node.value.keys)
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == 'reads':
                    resources.add(ast.literal_eval(target.slice))
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == 'resource':
            resources.update(n.value for n in node.comparators if isinstance(n, ast.Constant) and isinstance(n.value, str))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'Route' and node.args:
            path = node.args[0]
            if isinstance(path, ast.BinOp) and isinstance(path.right, ast.Constant):
                resources.add(path.right.value.lstrip('/'))
    cli = ast.parse((ROOT / 'model_gateway/__main__.py').read_text())
    flags = {n.args[0].value for n in ast.walk(cli) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == 'add_argument' and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value.startswith('--')}
    manuals = '\n'.join(p.read_text() for audience in ('users', 'developers') for p in prose_pages(audience).values())
    missing = sorted({'/' + name for name in resources if '/' + name not in manuals} | {flag for flag in flags if flag not in manuals})
    report = {'status': 'failed' if missing else 'passed', 'resources': sorted(resources), 'cli_flags': sorted(flags), 'missing': missing}
    write_report('gateway-surface.json', report)
    if missing:
        raise ValueError('Gateway surface lacks manuals: ' + ', '.join(missing))
    return report


def content(audience):
    """Require real explanatory pages and mapped examples for the complete live surface."""
    manifest_path = DOCS / 'coverage' / f'{audience}.json'
    manifest = json.loads(manifest_path.read_text())
    pages = prose_pages(audience)
    entries = manifest['pages']
    errors = []
    mapped = set()
    covered_commands = set()
    sources = set()
    example_count = 0
    word_count = 0
    for row in entries:
        rel = row['path']
        if audience == 'developers' and not rel.startswith('guides/'):
            rel = 'guides/' + rel
        if rel in mapped:
            errors.append(f'Duplicate page mapping: {rel}')
        mapped.add(rel)
        if rel not in pages:
            errors.append(f'Missing manual: {rel}')
            continue
        text = pages[rel].read_text()
        words = len(re.findall(r'\b[\w-]+\b', re.sub(r'```[\s\S]*?```', '', text)))
        word_count += words
        if words < 170:
            errors.append(f'Manual has insufficient explanatory prose ({words} words): {rel}')
        if not (text.startswith('---\n') and re.search(r'^description:', text, re.M)):
            errors.append(f'Missing title/description metadata: {rel}')
        examples = row.get('examples', [])
        if not examples:
            errors.append(f'No worked example mapped: {rel}')
        example_count += len(examples)
        for source in row.get('sources', []):
            if audience == 'developers' and source not in text:
                errors.append(f'Source mapping lacks an authored reference: {source} in {rel}')
            if not (ROOT / source).is_file():
                errors.append(f'Missing source {source} in {rel}')
            sources.add(source)
        for command in row.get('commands', []):
            if command not in text:
                errors.append(f'Command mapping lacks corresponding prose: {command} in {rel}')
            covered_commands.add(command)
        if re.search(r'\b(?:TODO|TBD|lorem ipsum|coming soon|placeholder page)\b', text, re.I):
            errors.append(f'Unfinished manual marker: {rel}')
    reviewed = manifest.get('source_sha256', {})
    implementation = {s for s in sources if Path(s).suffix in {'.py','.cpp','.hpp','.h','.ts','.tsx','.css','.js','.mjs'}}
    if implementation - set(reviewed):
        errors.append('Implementation lacks a documentation review hash: '+', '.join(sorted(implementation-set(reviewed))))
    for source,digest in reviewed.items():
        if not (ROOT/source).is_file() or sha(ROOT/source)!=digest:
            errors.append(f'Authored feature guidance needs source review: {source}')
    required_count = 30 if audience == 'users' else 35
    if len(entries) < required_count:
        errors.append(f'{audience} has {len(entries)} mapped guides; expected at least {required_count}')
    if audience == 'users':
        inventory = command_inventory()
        required = inventory['READ_COMMANDS'] | inventory['WRITE_COMMANDS']
        missing = required - covered_commands
        if missing:
            errors.append('Commands missing feature guidance: ' + ', '.join(sorted(missing)))
    else:
        modules = [p for p in (ROOT / 'agent').iterdir() if p.is_dir() and (p / '__init__.py').is_file()]
        missing = [p.name for p in modules if not any(s.startswith(f'agent/{p.name}/') for s in sources)]
        if missing:
            errors.append('Core modules missing authored explanations: ' + ', '.join(missing))
        consumers = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'consumer').glob('*.py') if p.name not in {'__init__.py', '__main__.py'}}
        if consumers - sources:
            errors.append('Consumer implementation missing authored explanations: ' + ', '.join(sorted(consumers - sources)))
        gateway = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'model_gateway').glob('*.py')}
        if gateway - sources:
            errors.append('Gateway implementation missing authored explanations: ' + ', '.join(sorted(gateway - sources)))
        frontend = {p.relative_to(ROOT).as_posix() for p in (ROOT/'frontend/src').rglob('*') if p.is_file() and p.suffix in {'.ts','.tsx','.css'}}
        if frontend - sources:
            errors.append('Frontend implementation missing authored explanations: '+', '.join(sorted(frontend-sources)))
    report = {'audience': audience, 'status': 'failed' if errors else 'passed', 'manuals': len(entries), 'words': word_count,
              'examples': example_count, 'mapped_commands': len(covered_commands), 'mapped_sources': len(sources),
              'manifest_sha256': sha(manifest_path), 'errors': errors}
    write_report(f'content-{audience}.json', report)
    print(json.dumps(report, indent=2), flush=True)
    if errors:
        raise ValueError(f'{audience} content qualification failed with {len(errors)} errors')
    return report


class RenderedPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.links = []
        self.canonical = None
        self.title = ''
        self._in_title = False
        self.has_main = False
        self.has_h1 = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        self._in_title |= tag == 'title'
        self.has_main |= tag == 'main'
        self.has_h1 |= tag == 'h1'
        if tag == 'link' and attrs.get('rel') == 'canonical':
            self.canonical = attrs.get('href')
            return
        for key in ('href', 'src'):
            if attrs.get(key):
                self.links.append(attrs[key])

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def rendered_links(audience):
    """Audit actual generated HTML; source Markdown links alone cannot qualify a site."""
    root = DOCS / 'dist' / audience
    pages = {}
    errors = []
    links = 0
    external = 0
    for file in root.rglob('*.html'):
        if not file.is_file():
            continue
        parser = RenderedPage()
        parser.feed(file.read_text())
        pages[file.resolve()] = parser
        if not parser.title or not parser.has_main or not parser.has_h1:
            errors.append(f'Missing semantic page/title: {file.relative_to(root)}')
    if not pages:
        raise ValueError(f'No rendered {audience} pages')
    # A source generator can succeed while the site loader silently drops pages
    # such as Python __init__ modules. Require every authored/generated page route.
    expected_routes = 0
    content_root = DOCS / audience / 'src/content/docs'
    for source in content_root.rglob('*.md'):
        text = source.read_text()
        slug = re.search(r'^slug: "([^"]+)"$', text, re.M)
        route = slug.group(1) if slug else source.relative_to(content_root).with_suffix('').as_posix()
        if not slug and route == 'index':
            route = ''
        elif not slug and route.endswith('/index'):
            route = route[:-6]
        target = (root / route / 'index.html').resolve()
        expected_routes += 1
        if target not in pages:
            errors.append(f'Source page was not published: {source.relative_to(content_root)}')
    reference_lines = 0
    reference_symbols = 0
    reference_manifest = DOCS / 'coverage/reference.json'
    if audience == 'developers' and reference_manifest.is_file():
        for record in json.loads(reference_manifest.read_text())['files']:
            target = (root / 'reference/files' / record['path'] / 'index.html').resolve()
            rendered = pages.get(target)
            if rendered is None:
                errors.append(f'Source reference is unreachable: {record["path"]}')
                continue
            if record['source_view']:
                for line in range(1, record['line_count'] + 1):
                    reference_lines += 1
                    if f'L{line}' not in rendered.ids:
                        errors.append(f'Rendered source line is missing: {record["path"]}:L{line}')
            for symbol in record['structure']['symbols']:
                reference_symbols += 1
                if symbol['anchor'] not in rendered.ids:
                    errors.append(f'Rendered definition is missing: {record["path"]}:{symbol["anchor"]}')
    origin = f'http://localhost:{4321 if audience == "users" else 4322}'
    for file, parser in pages.items():
        rel = file.relative_to(root).as_posix()
        route = '/' + (rel[:-10] if rel.endswith('index.html') else rel)
        for link in parser.links:
            if link.startswith(('data:', 'mailto:', 'tel:', 'javascript:')):
                continue
            absolute = urlsplit(urljoin(origin + route, link))
            if absolute.netloc and absolute.netloc != urlsplit(origin).netloc:
                external += 1
                continue
            links += 1
            target = (root / unquote(absolute.path).lstrip('/')).resolve()
            if not target.is_relative_to(root.resolve()):
                errors.append(f'{rel}: escaping link {link}')
                continue
            if target.is_dir():
                target /= 'index.html'
            if not target.is_file():
                errors.append(f'{rel}: missing target {link}')
            elif absolute.fragment and target.suffix == '.html':
                if target not in pages or unquote(absolute.fragment) not in pages[target].ids:
                    errors.append(f'{rel}: missing anchor {link}')
    report = {'audience': audience, 'status': 'failed' if errors else 'passed', 'pages': len(pages), 'expected_routes': expected_routes,
              'source_lines': reference_lines, 'source_symbols': reference_symbols, 'local_links': links,
              'external_links_not_fetched': external, 'canonical_metadata_not_navigation': sum(p.canonical is not None for p in pages.values()), 'errors': errors}
    write_report(f'links-{audience}.json', report)
    print(json.dumps({**report, 'errors': errors[:20]}, indent=2), flush=True)
    if errors:
        raise ValueError(f'{audience} rendered links failed: {len(errors)} errors')
    return report


def examples():
    """Check copyable snippets, then execute the documented scratch-workspace tutorial."""
    from markdown_it import MarkdownIt
    parser = MarkdownIt()
    counts = {'json': 0, 'python_compiled': 0, 'shell_syntax': 0}
    errors = []
    for audience in ('users', 'developers'):
        for rel, path in prose_pages(audience).items():
            for token in parser.parse(path.read_text()):
                if token.type != 'fence':
                    continue
                language = token.info.split()[0] if token.info.split() else ''
                body = token.content
                try:
                    if language == 'json':
                        json.loads(body); counts['json'] += 1
                    elif language in ('python', 'py'):
                        compile(body, str(path), 'exec'); counts['python_compiled'] += 1
                    elif language in ('bash', 'sh', 'shell'):
                        check = subprocess.run(['bash', '-n'], input=body, text=True, capture_output=True)
                        if check.returncode:
                            raise ValueError(check.stderr)
                        counts['shell_syntax'] += 1
                except (ValueError, SyntaxError) as exc:
                    errors.append(f'{audience}/{rel}:{token.map[0]+1}: {exc}')
    if errors:
        write_report('examples.json', {'status': 'failed', 'counts': counts, 'errors': errors})
        raise ValueError('Invalid copyable examples:\n' + '\n'.join(errors))
    run([PYTHON, 'documentation/examples/first_mechanisms.py'])
    report = {'status': 'passed', 'counts': counts, 'executed_tutorial': 'documentation/examples/first_mechanisms.py',
              'tutorial_sha256': sha(DOCS/'examples/first_mechanisms.py'),
              'boundary': 'Syntax checks do not prove remote endpoint behavior. The local tutorial executes real journal, revisions, goals, framing, and canonical error handling in a temporary workspace.'}
    write_report('examples.json', report)
    return report


def source_manifest():
    paths = []
    for directory in ('agent', 'consumer', 'model_gateway', 'native/memory', 'frontend', 'scripts', 'tests', 'packaging', 'documentation'):
        for p in (ROOT / directory).rglob('*'):
            rel = p.relative_to(ROOT)
            if not p.is_file() or p.is_symlink():
                continue
            if any(x in rel.parts for x in ('node_modules', '.astro', 'dist', '__pycache__', 'vendor')):
                continue
            if rel.as_posix().startswith(('documentation/coverage/', 'documentation/developers/src/content/docs/reference/files/')):
                continue
            paths.append(p)
    for name in ('pyproject.toml', 'CMakeLists.txt', 'package.json', 'package-lock.json'):
        paths.append(ROOT / name)
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(set(paths))}



def distribution_manifest():
    """Bind served HTML, scripts, search shards, fonts and assets to qualification."""
    base = DOCS / 'dist'
    return {p.relative_to(base).as_posix(): sha(p) for p in sorted(base.rglob('*')) if p.is_file()}


def coverage_inputs():
    """Hash authored coverage catalogs separately from the generated source inventory."""
    return {p.name:sha(p) for p in sorted((DOCS/'coverage').glob('*.json')) if p.name!='reference.json'}


def documentation_coverage():
    """Independently require every docs-tool source and two exact line floors."""
    from coverage.parser import PythonParser
    raw = json.loads((EVIDENCE/'coverage.json').read_text())
    expected = {'scripts/docs_gate.py': 'gate'}
    expected.update({p.relative_to(ROOT).as_posix(): 'reference_builder'
                     for p in (ROOT/'scripts/docs_reference').rglob('*.py')})
    if len(expected) < 2:
        raise ValueError('Reference builder source inventory is empty')
    measured = {}
    errors = []
    for name, row in raw.get('files', {}).items():
        path = Path(name)
        canonical = path.resolve().relative_to(ROOT.resolve()).as_posix() if path.is_absolute() and path.resolve().is_relative_to(ROOT.resolve()) else path.as_posix()
        if canonical in measured:
            errors.append(f'Duplicate coverage source: {canonical}')
        measured[canonical] = row
    groups = {name: {'covered_lines': 0, 'executable_lines': 0, 'covered_branches': 0, 'branches': 0}
              for name in ('gate', 'reference_builder')}
    hashes = {}
    for name, group in sorted(expected.items()):
        hashes[name] = sha(ROOT/name)
        row = measured.get(name)
        if row is None:
            errors.append(f'Missing coverage source: {name}')
            continue
        parser = PythonParser(filename=str(ROOT/name))
        parser.parse_source()
        statements = set(parser.statements)
        executed, missing = row.get('executed_lines', []), row.get('missing_lines', [])
        summary = row.get('summary', {})
        if row.get('excluded_lines') or summary.get('excluded_lines', 0):
            errors.append(f'Excluded executable lines are forbidden: {name}')
        valid_lines = all(type(line) is int for line in executed + missing)
        executable_hits = set(executed) & statements
        nonstatement_hits = set(executed) - statements
        if (not valid_lines or len(executed) != len(set(executed)) or len(missing) != len(set(missing))
                or nonstatement_hits - parser.raw_docstrings
                or executable_hits & set(missing) or executable_hits | set(missing) != statements
                or summary.get('num_statements') != len(statements)
                or summary.get('covered_lines') != len(executable_hits)
                or summary.get('missing_lines') != len(missing)):
            errors.append(f'Coverage executable denominator or hits differ from source: {name}')
            continue
        totals = groups[group]
        totals['covered_lines'] += len(executable_hits)
        totals['executable_lines'] += len(statements)
        totals['covered_branches'] += summary.get('covered_branches', 0)
        totals['branches'] += summary.get('num_branches', 0)
    for name, totals in groups.items():
        count = totals['executable_lines']
        totals['line_percent'] = totals['covered_lines'] * 100 / count if count else 0
        if not count or totals['covered_lines'] * 100 < count * 90:
            errors.append(f'{name} executable line coverage is below 90 percent')
    result = {'status': 'failed' if errors else 'passed', 'groups': groups, 'source_sha256': hashes,
              'coverage_sha256': sha(EVIDENCE/'coverage.json'), 'excluded_lines': 0, 'errors': errors}
    write_report('coverage-floors.json', result)
    if errors:
        raise ValueError('Documentation coverage failed: ' + '; '.join(errors))
    return result


def sites():
    run(['npm', '--prefix', 'documentation', 'run', 'build'])
    return [rendered_links(audience) for audience in ('users', 'developers')]


def full():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    receipt = EVIDENCE / 'qualification.json'
    receipt.unlink(missing_ok=True)
    before = source_manifest()
    before_coverage = coverage_inputs()
    (ROOT/'.build').mkdir(parents=True, exist_ok=True)
    run(['env', 'COVERAGE_FILE=.build/coverage-docs-full', PYTHON, '-m', 'pytest',
         'tests/documentation', 'tests/docs_site_gate.py', '-q', '--cov=scripts', '--cov-branch',
         '--cov-report=json:'+str(EVIDENCE/'coverage.json')])
    documentation_coverage()
    run(['node', '--experimental-strip-types', '--test', 'tests/documentation/navigation.test.mjs'])
    contracts()
    recipes()
    gateway_surface()
    manuals = [content(audience) for audience in ('users', 'developers')]
    run([PYTHON, '-m', 'scripts.docs_reference', 'build', '--root', ROOT, '--output', DOCS/'developers/src/content/docs/reference/files', '--manifest', DOCS/'coverage/reference.json'])
    run([PYTHON, '-m', 'scripts.docs_reference', 'audit', '--root', ROOT, '--output', DOCS/'developers/src/content/docs/reference/files', '--manifest', DOCS/'coverage/reference.json'])
    site_reports = sites()
    example_report = examples()
    run(['node', 'scripts/docs_journey.mjs'])
    after = source_manifest()
    if before != after or before_coverage != coverage_inputs():
        raise ValueError('Authored source/content changed during documentation qualification')
    write_report('source-manifest.json', after)
    write_report('distribution-manifest.json', distribution_manifest())
    report = {'status': 'passed', 'completed_at': time.time(), 'manuals': manuals, 'sites': site_reports, 'examples': example_report,
              'source_manifest_sha256': sha(EVIDENCE/'source-manifest.json'),
              'evidence_sha256': {p.name: sha(p) for p in sorted(EVIDENCE.glob('*.json')) if p.name != 'qualification.json'},
              'reference_manifest_sha256': sha(DOCS/'coverage/reference.json'),
              'coverage_input_sha256': coverage_inputs(),
              'content_manifest_sha256': {a: sha(DOCS/'coverage'/f'{a}.json') for a in ('users', 'developers')}}
    write_report('qualification.json', report)
    print('Both complete documentation sites qualified.', flush=True)
    return report


def verify_receipt():
    report = json.loads((EVIDENCE / 'qualification.json').read_text())
    manifest = EVIDENCE/'source-manifest.json'
    if report['status'] != 'passed' or sha(manifest) != report['source_manifest_sha256'] or source_manifest() != json.loads(manifest.read_text()):
        raise ValueError('Documentation qualification is absent or stale')
    for name, digest in report['evidence_sha256'].items():
        if sha(EVIDENCE/name) != digest:
            raise ValueError(f'Documentation evidence changed: {name}')
    if (EVIDENCE/'distribution-manifest.json').is_file() and distribution_manifest() != json.loads((EVIDENCE/'distribution-manifest.json').read_text()):
        raise ValueError('Built documentation distribution changed')
    if sha(DOCS/'coverage/reference.json') != report['reference_manifest_sha256']:
        raise ValueError('Source reference evidence changed')
    reference = json.loads((DOCS/'coverage/reference.json').read_text())
    for record in reference.get('files', []):
        if sha(ROOT/record['path']) != record['sha256']:
            raise ValueError(f'Referenced source changed: {record["path"]}')
        if sha(DOCS/'developers/src/content/docs/reference/files'/record['page']) != record['page_sha256']:
            raise ValueError(f'Generated source reference changed: {record["path"]}')
    for audience, digest in report.get('content_manifest_sha256', {}).items():
        if sha(DOCS/'coverage'/f'{audience}.json') != digest:
            raise ValueError(f'Documentation content mapping changed: {audience}')
    if 'coverage_input_sha256' in report and coverage_inputs() != report['coverage_input_sha256']:
        raise ValueError('Documentation coverage catalog changed')
    print('Documentation qualification matches current authored source and manuals.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['content', 'sites', 'links', 'examples', 'full', 'verify-receipt', 'close'])
    parser.add_argument('--audience', choices=['users', 'developers'])
    args = parser.parse_args()
    if args.mode in {'examples', 'full'} and sys.executable != str(PYTHON) and PYTHON.is_file():
        # Use the pinned documentation parser dependencies in the project environment.
        import os
        os.execv(str(PYTHON), [str(PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    if args.mode == 'content':
        if not args.audience:
            parser.error('--audience is required for content')
        content(args.audience)
    elif args.mode == 'sites':
        sites()
    elif args.mode == 'links':
        for audience in ('users', 'developers'):
            rendered_links(audience)
    elif args.mode == 'examples':
        examples()
    elif args.mode == 'full':
        full()
    elif args.mode == 'verify-receipt':
        verify_receipt()
    else:
        verify_receipt()
        run(['cg', 'docs', 'close'])


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'Documentation gate failed: {error}', file=sys.stderr)
        raise SystemExit(1)
