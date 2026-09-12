"""Render inert source and syntax evidence as Astro-compatible Markdown pages."""

import hashlib
import html
import json


def anchor(symbol: dict) -> str:
    """Stable unique symbol anchor including overload coordinates."""
    key = json.dumps([symbol["qualified_name"], symbol["kind"], symbol["line"], symbol["column"]])
    return "symbol-" + hashlib.sha256(key.encode()).hexdigest()[:20]


def code(value: str) -> str:
    """Escape code before placing it inside raw HTML; never render source HTML."""
    return "<pre><code>" + html.escape(value, quote=True) + "</code></pre>"


def facts(rows: list[dict]) -> list[str]:
    """Render concrete evidence, with source links for every observation."""
    result = []
    for row in rows:
        result.extend([f'<p><strong>{html.escape(row["kind"])}</strong> '
            f'<a href="#L{row["line"]}">lines {row["line"]}–{row["end_line"]}</a></p>',
            code(row["source"].splitlines()[0]), ""])
    return result


def render(record: dict, source: str | None) -> str:
    """One page per file with all symbol contracts and every numbered source line."""
    path = record["path"]
    parsed = record["structure"]
    output = ["---", "title: " + json.dumps(path),
        "description: " + json.dumps("Source-grounded implementation reference for " + path),
        "slug: " + json.dumps("reference/files/" + path), "---", "",
        "This generated reference records **syntax-derived evidence**, not inferred intent,",
        "runtime proof, authority, or a guarantee that a branch executes. The explanatory",
        "developer guides describe the surrounding architecture and supported contracts.", "",
        "Observation excerpts show the first source line; their links retain the complete",
        "statement span. The machine-readable manifest retains the complete extracted statements.", "",
        f'- Repository path: `{path}`', f'- SHA-256: `{record["sha256"]}`',
        f'- Bytes: {record["bytes"]}; source lines: {record["line_count"]}',
        f'- Parser: `{parsed["parser"]}` ({parsed["parser_quality"]})', ""]
    for limitation in parsed["limitations"]:
        output.append("- " + limitation)
    if record.get("guides"):
        output.extend(["", "## Architecture and rationale", ""])
        for guide in record["guides"]:
            output.append(f'<p><a href="{html.escape(guide["url"], quote=True)}">{html.escape(guide["title"])}</a></p>')
    if parsed.get("module_docstring"):
        output.extend(["", "## Source documentation", "", code(parsed["module_docstring"])])
    output.extend(["", "## Definitions", ""])
    if not parsed["symbols"]:
        output.extend(["No named definitions are present in the supported syntax inventory for this file.", ""])
    for symbol in parsed["symbols"]:
        label = html.escape(symbol["qualified_name"])
        output.append(f'<p><a href="#{anchor(symbol)}">{label}</a> — {html.escape(symbol["kind"])}; '
                      f'<a href="#L{symbol["line"]}">line {symbol["line"]}</a></p>')
    output.append("")
    for symbol in parsed["symbols"]:
        label = html.escape(symbol["qualified_name"])
        output.extend([f'<details id="{anchor(symbol)}">',
            f'<summary>{label} — {html.escape(symbol["kind"])}</summary>', "",
            f'<p>Exact source span: <a href="#L{symbol["start_line"]}">{symbol["start_line"]}–{symbol["end_line"]}</a>.</p>',
            code(symbol["signature"]), "", "<h3>Arguments and declared contract</h3>", "",
            code(json.dumps({k: symbol[k] for k in ("parameters", "decorators", "returns_annotation", "bases", "fields")}, indent=2, ensure_ascii=False)), ""])
        if symbol.get("docstring"):
            output.extend(["<h3>Source docstring or attached comment</h3>", "", code(symbol["docstring"]), ""])
        output.extend(["<h3>Observed implementation syntax</h3>", ""])
        if symbol["facts"]: output.extend(facts(symbol["facts"]))
        else: output.extend(["No direct body observations of the recorded kinds; inspect the complete source below.", ""])
        output.extend(["</details>", ""])
    if parsed["facts"]:
        output.extend(["<details>", "<summary>File-level implementation observations</summary>", ""])
        output.extend(facts(parsed["facts"]))
        output.extend(["</details>", ""])
    output.extend(["## Complete source", ""])
    if source is None:
        output.extend(["This input is retained as dependency-lock or binary-asset provenance,", "not authored executable source. Its exact bytes are identified by the hash above.", "", code(json.dumps(record.get("metadata", {}), indent=2))])
    else:
        output.extend(["<details open>", "<summary>Every source line, escaped as inert text</summary>", "", '<pre class="numbered-source"><code>'])
        for number, line in enumerate(source.splitlines(), 1):
            output.append(f'<span class="source-line" id="L{number}"><a href="#L{number}" aria-label="Line {number}">{number:5}</a> {html.escape(line, quote=True)}</span>')
        output.extend(["</code></pre>", "", "</details>"])
    return "\n".join(output) + "\n"
