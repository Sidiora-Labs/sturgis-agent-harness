#!/usr/bin/env python3
"""Read-only CommonMark local-link/anchor audit; all output stays in .build."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit

import markdown_it
from markdown_it import MarkdownIt

PATTERNS = ["README.md", "docs/**/*.md", "spec/**/*.md", "agent/**/README.md",
            "native/memory/PROTOCOL.md", "packaging/**/*.md",
            "frontend/licenses/**/*.md", ".research/**/*.md"]
EXCLUDED_PARTS = {"vendor", "vendored", "third_party", "third-party", "node_modules",
                  "dist", "build", ".build", "generated", ".git", ".codegraph",
                  ".codex", ".agents", "__pycache__"}
MD = MarkdownIt("commonmark", {"html": True})


class HTMLReferences(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids, self.links = set(), []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        for key in ("id", "name" if tag == "a" else "id"):
            if values.get(key):
                self.ids.add(values[key])
        for key in ("href", "src"):
            if values.get(key) is not None:
                self.links.append((values[key], self.getpos()[0], "html_" + key))

    handle_startendtag = handle_starttag


def plain(children):
    result = []
    for child in children or []:
        if child.type in {"text", "code_inline"}:
            result.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            result.append(" ")
        elif child.type == "image":
            result.append(plain(child.children) if child.children else child.content)
    return "".join(result)


def slug(value):
    # GitHub-style Unicode heading IDs: retain letters/numbers/marks, _ and -;
    # remove punctuation/symbols, lowercase, and replace each space with a dash.
    return "".join(c for c in value.lower() if c in " _-" or
                   unicodedata.category(c)[0] not in {"P", "S", "C"}).replace(" ", "-")


def parse_markdown(body):
    tokens = MD.parse(body)
    lines = body.splitlines()
    headings, seen, links = set(), set(), []
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            base = slug(plain(tokens[index + 1].children))
            candidate, number = base, 0
            while candidate in seen:
                number += 1
                candidate = f"{base}-{number}"
            headings.add(candidate)
            seen.add(candidate)
        if token.type == "html_block":
            parser = HTMLReferences()
            parser.feed(token.content)
            headings.update(parser.ids)
            for href, row, kind in parser.links:
                links.append({"target": href, "line": token.map[0] + row, "kind": kind})
        if token.type != "inline":
            continue
        start, end = token.map or (0, len(lines))
        for child in token.children or []:
            if child.type == "html_inline":
                parser = HTMLReferences()
                parser.feed(child.content)
                headings.update(parser.ids)
                child_links = [(href, kind) for href, _, kind in parser.links]
            elif child.type == "link_open":
                child_links = [(child.attrGet("href"), "link")]
            elif child.type == "image":
                child_links = [(child.attrGet("src"), "image")]
            else:
                continue
            for href, kind in child_links:
                # Reference-style links fall back to the source block's first line.
                row = next((i + 1 for i in range(start, end)
                            if href in lines[i] or unquote(href) in lines[i]), start + 1)
                links.append({"target": href, "line": row, "kind": kind})
    return headings, links


def target_parts(value):
    # Absolute /root/...:line citations are filesystem links, not URI schemes.
    if value.startswith("//"):
        return None
    colon = re.search(r":([0-9]+)(?:-([0-9]+))?$", value.split("#", 1)[0])
    scheme = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
    if scheme and not colon and not value.startswith("file:"):
        return None
    parsed = urlsplit(value if not colon or value.startswith("/") else "./" + value)
    if parsed.scheme and parsed.scheme != "file":
        return None
    if parsed.scheme == "file" and parsed.netloc not in {"", "localhost"}:
        return None
    path, fragment = unquote(parsed.path), unquote(parsed.fragment)
    match = re.search(r":([0-9]+)(?:-([0-9]+))?$", path)
    span = None
    if match:
        path = path[:match.start()]
        span = (int(match[1]), int(match[2] or match[1]))
    return path, fragment, span


class Checker:
    def __init__(self, root):
        self.root, self.cache = root, {}

    def read(self, path):
        if path not in self.cache:
            self.cache[path] = path.read_bytes()
        return self.cache[path]

    def validate(self, document, link):
        parsed = target_parts(link["target"])
        if parsed is None:
            return {"classification": "external"}
        location, fragment, span = parsed
        target = ((document.parent / location) if location else document).resolve()
        result = {"classification": "local", "resolved_path": str(target),
                  "outside_repository": not target.is_relative_to(self.root)}
        if not target.exists():
            return {**result, "error": "missing_path"}
        if span and fragment:
            return {**result, "error": "ambiguous_line_and_fragment"}
        if fragment and re.fullmatch(r"L[0-9]+(?:-L?[0-9]+)?", fragment):
            numbers = re.findall(r"[0-9]+", fragment)
            span = (int(numbers[0]), int(numbers[-1]))
        if span:
            result["anchor_kind"] = "line"
            if not target.is_file():
                return {**result, "error": "line_target_not_file"}
            try:
                source = self.read(target).decode("utf-8")
            except (OSError, UnicodeError):
                return {**result, "error": "line_target_not_readable_text"}
            count = len(source.splitlines())
            result.update(line_start=span[0], line_end=span[1], target_line_count=count)
            if not 1 <= span[0] <= span[1] <= count:
                return {**result, "error": "line_anchor_out_of_range"}
        elif fragment:
            result["anchor_kind"] = "heading_or_html_id"
            if target.is_dir():
                target = next((target / name for name in ("README.md", "readme.md", "index.html")
                               if (target / name).is_file()), target)
            if not target.is_file():
                return {**result, "error": "fragment_target_not_file"}
            try:
                body = self.read(target).decode("utf-8")
            except (OSError, UnicodeError):
                return {**result, "error": "fragment_target_not_readable_text"}
            if target.suffix.lower() in {".md", ".markdown"}:
                ids, _ = parse_markdown(body)
            elif target.suffix.lower() in {".html", ".htm", ".svg", ".xml"}:
                parser = HTMLReferences()
                parser.feed(body)
                ids = parser.ids
            else:
                return {**result, "error": "unsupported_fragment_target"}
            if fragment not in ids:
                return {**result, "error": "missing_heading_or_html_id", "fragment": fragment}
        return result


def self_test():
    cases = []
    with tempfile.TemporaryDirectory(prefix="sturgis-doc-links-") as directory:
        root = Path(directory)
        document = root / "README.md"
        document.write_text("# Intro\n## Repeated\n## Repeated\n## API `ID` & Café\n"
                            "<a id=\"manual\"></a>\n\n[reference][good]\n\n"
                            "[good]: target.md#target-heading\n\n"
                            "```md\n[ignored](absent-in-code.md)\n```\n"
                            "`[ignored](absent-inline.md)`\n"
                            "![picture](image.svg#symbol)\n<a href=\"target.md\">html</a>\n")
        (root / "target.md").write_text("# Target heading\n\nText.\n")
        (root / "source.py").write_text("first\nsecond\nthird\n")
        (root / "image.svg").write_text('<svg><symbol id="symbol"/></svg>')
        checker = Checker(root)
        checks = [
            ("real_path", "target.md", None),
            ("missing_path", "absent.md", "missing_path"),
            ("real_heading", "target.md#target-heading", None),
            ("missing_heading", "target.md#false-heading", "missing_heading_or_html_id"),
            ("same_document", "#intro", None),
            ("duplicate_heading", "#repeated-1", None),
            ("missing_duplicate", "#repeated-2", "missing_heading_or_html_id"),
            ("unicode_inline_heading", "#api-id--caf%C3%A9", None),
            ("html_id", "#manual", None),
            ("svg_id", "image.svg#symbol", None),
            ("line", "source.py#L2", None),
            ("line_range", "source.py#L1-L3", None),
            ("colon_line", "source.py:2", None),
            ("absolute_colon", str(root / "source.py") + ":2", None),
            ("line_zero", "source.py#L0", "line_anchor_out_of_range"),
            ("line_excess", "source.py#L4", "line_anchor_out_of_range"),
            ("colon_excess", "source.py:4", "line_anchor_out_of_range"),
            ("reversed_range", "source.py#L3-L1", "line_anchor_out_of_range"),
            ("bad_fragment_type", "source.py#invented", "unsupported_fragment_target"),
            ("missing_svg_id", "image.svg#absent", "missing_heading_or_html_id"),
        ]
        for name, href, error in checks:
            actual = checker.validate(document, {"target": href}).get("error")
            if actual != error:
                raise RuntimeError(f"Self-test {name}: expected {error!r}, got {actual!r}")
            cases.append(name)
        _, links = parse_markdown(document.read_text())
        if [entry["target"] for entry in links] != ["target.md#target-heading", "image.svg#symbol", "target.md"]:
            raise RuntimeError("CommonMark reference/image/HTML/code exclusion self-test failed")
        cases.append("reference_image_html_links_and_code_exclusion")
        if checker.validate(document, {"target": "https://example.invalid/#absent"}) != {"classification": "external"}:
            raise RuntimeError("External link classification self-test failed")
        cases.append("external_classification_without_network")
    return {"status": "passed", "count": len(cases), "cases": cases}


def audit(root):
    validation = self_test()
    candidates = sorted({p for pattern in PATTERNS for p in root.glob(pattern) if p.is_file()})
    excluded = [p for p in candidates if set(p.relative_to(root).parts) & EXCLUDED_PARTS]
    documents = [p for p in candidates if p not in excluded]
    checker, errors, links, counts = Checker(root), [], [], Counter()
    for document in documents:
        _, entries = parse_markdown(checker.read(document).decode("utf-8"))
        for entry in entries:
            result = checker.validate(document, entry)
            row = {"document": str(document.relative_to(root)), **entry, **result}
            links.append(row)
            counts[result["classification"]] += 1
            counts[entry["kind"]] += 1
            if result.get("anchor_kind"):
                counts[result["anchor_kind"]] += 1
            if result.get("outside_repository"):
                counts["outside_repository_local"] += 1
            if result.get("error"):
                errors.append(row)
    changed = [str(p) for p, content in checker.cache.items() if not p.exists() or p.read_bytes() != content]
    errors.extend({"error": "input_changed_during_scan", "document": path} for path in changed)
    return {
        "schema": "sturgis-doc-links/1", "created_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root), "status": "passed" if not errors else "failed",
        "parser": {"name": "markdown-it-py", "version": markdown_it.__version__, "mode": "commonmark + HTML"},
        "scope": {"patterns": PATTERNS, "excluded_path_components": sorted(EXCLUDED_PARTS),
                  "excluded_files": [str(p.relative_to(root)) for p in excluded],
                  "documents": [str(p.relative_to(root)) for p in documents],
                  "public_spec_markdown_mirrors_included": True},
        "limits": ["External URLs, mail/app/data URIs and protocol-relative links are counted but never fetched.",
                   "Heading anchors use GitHub-style Unicode slugs and duplicate suffixes; HTML id/name values are exact.",
                   "Line anchors validate file readability and bounds, not whether a cited symbol or historical claim still occupies that line.",
                   "Absolute donor citations are checked on this host; this does not make them portable checkout links.",
                   "Code spans/fences and plain unlinked paths are not links; reference-style links use the containing block's source line."],
        "counts": {"documents": len(documents), "links": len(links), **dict(counts), "errors": len(errors)},
        "self_validation": validation,
        "document_sha256": {str(p.relative_to(root)): hashlib.sha256(checker.read(p)).hexdigest() for p in documents},
        "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "errors": errors, "links": links,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path(".build/docs-links-preflight.json"))
    args = parser.parse_args()
    report = audit(args.root.resolve())
    output = args.output if args.output.is_absolute() else args.root.resolve() / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": report["status"], "counts": report["counts"],
                      "self_validation": report["self_validation"]["count"], "report": str(output)}, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
