"""Build and independently verify complete authored-file reference artifacts."""

from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatchcase
import hashlib
import json
from collections import Counter
from pathlib import Path
import re

from .cpp_parser import parse_cpp
from .inventory import ReferenceError, inventory
from .javascript import parse_javascript
from .python_parser import parse_python
from .render import anchor, render


VERSION = "sturgis-source-reference/1"


def guide_input(root: Path, path: Path | None) -> tuple[dict | None, list[dict]]:
    """Read a separately hashed narrative map; it never controls inventory."""
    path = path or root / "documentation/coverage/developers.json"
    if not path.exists():
        return None, []
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
        rows = value if isinstance(value, list) else value["pages"]
        if not isinstance(rows, list): raise ValueError("pages must be a list")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str) or not isinstance(row.get("sources"), list) or any(not isinstance(s, str) for s in row["sources"]):
                raise ValueError("each guide needs a path and source strings")
            target = row["path"]
            if target.startswith("/") or ".." in Path(target).parts or "\\" in target or ":" in target:
                raise ValueError("guide path must be local and relative")
    except (ValueError, KeyError, TypeError) as exc:
        raise ReferenceError(f"Invalid guide manifest: {exc}") from exc
    resolved = path.resolve()
    name = resolved.relative_to(root).as_posix() if resolved.is_relative_to(root) else str(resolved)
    return {"path": name, "sha256": digest(raw)}, rows


def attach_guides(record: dict, rows: list[dict]) -> None:
    """Connect concrete source files to matching explanatory guide links."""
    matches = {}
    for row in rows:
        if not any(record["path"] == pattern or record["path"].startswith(pattern.rstrip("/") + "/") or fnmatchcase(record["path"], pattern) for pattern in row["sources"]):
            continue
        slug = row["path"].removesuffix(".md").removesuffix(".mdx")
        if not slug.startswith("guides/"): slug = "guides/" + slug
        slug = slug.removesuffix("/index")
        url = "/" + slug.rstrip("/") + "/"
        matches[url] = {"url": url, "title": str(row.get("title") or Path(slug).name.replace("-", " "))}
    record["guides"] = [matches[key] for key in sorted(matches)]


def digest(data: bytes) -> str:
    """Content identity for source, generated pages and parser contracts."""
    return hashlib.sha256(data).hexdigest()


def plain_structure(kind: str, limitation: str) -> dict:
    """Declare source-only formats explicitly instead of claiming semantic parsing."""
    return {"parser": kind, "parser_quality": "complete_source_only", "limitations": [limitation],
            "symbols": [], "facts": [], "module_docstring": None}


def read_record(root: Path, path: str) -> tuple[dict, str | None]:
    """Read one fixed inventory input and derive its accountable source contract."""
    raw = (root / path).read_bytes()
    record = {"path": path, "sha256": digest(raw), "bytes": len(raw), "page": path + ".md"}
    if Path(path).name in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "uv.lock"}:
        record.update(line_count=len(raw.splitlines()), source_view=False,
            structure=plain_structure("dependency-lock-metadata", "Generated dependency lock retained as provenance; vendor resolution lines are not represented as authored implementation."),
            metadata={"role": "dependency lock", "source_path": path, "hash": record["sha256"]})
        if path.endswith("package-lock.json"):
            lock = json.loads(raw)
            record["metadata"].update(lockfile_version=lock.get("lockfileVersion"), package_entries=len(lock.get("packages", {})))
        return record, None
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError:
        record.update(line_count=0, source_view=False,
            structure=plain_structure("binary-asset-metadata", "Binary input retained by exact hash/size; it has no text lines or callable syntax inventory."),
            metadata={"role": "binary asset", "source_path": path, "hash": record["sha256"]})
        return record, None
    record.update(line_count=len(source.splitlines()), source_view=True)
    suffix = Path(path).suffix.lower()
    try:
        if suffix == ".py" or source.splitlines()[:1] and source.startswith("#!") and "python" in source.splitlines()[0]: structure = parse_python(source)
        elif suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}: structure = parse_javascript(path, source)
        elif suffix in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}: structure = parse_cpp(source, filename=root / path)
        elif suffix == ".astro":
            match = re.match(r"\A---[^\S\n]*\n(.*?)\n---[^\S\n]*(?:\n|$)", source, re.S)
            fragment = "\n" + match.group(1) if match else ""
            structure = parse_javascript(path + ".ts", fragment)
            structure["parser"] = "astro-frontmatter-babel"
            structure["parser_quality"] = "frontmatter_syntax_and_complete_template_source"
            structure["limitations"].append("Astro frontmatter is parsed as TypeScript. Template expressions and embedded script tags require the Astro compiler; full source remains visible and this parser does not claim their callable coverage.")
            if re.search(r"<script\b", source, re.I):
                raise ReferenceError(f"Astro embedded scripts need a complete parser before claiming coverage: {path}")
        else: structure = plain_structure("text-source", "This declarative/configuration/document format has a complete escaped line view; no executable-language semantics are inferred.")
    except (SyntaxError, ValueError) as exc:
        raise ReferenceError(f"Cannot account for {path}: {exc}") from exc
    symbols = structure["symbols"]
    identities = [(s["qualified_name"], s["kind"], s["line"], s["column"]) for s in symbols]
    if len(identities) != len(set(identities)):
        raise ReferenceError(f"Duplicate symbol identity in {path}")
    for s in symbols:
        if not 1 <= s["start_line"] <= s["line"] <= s["end_line"] <= record["line_count"]:
            raise ReferenceError(f"Invalid symbol span in {path}: {s['qualified_name']}")
        s["anchor"] = anchor(s)
    record["structure"] = structure
    return record, source


def inspect(root: Path, files: list[str]) -> list[tuple[dict, str | None]]:
    """Parse independently enumerated inputs in stable file order."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(lambda path: read_record(root, path), files))


def summary(records: list[dict]) -> dict:
    """Report observed inventory coverage without turning provenance into proof."""
    return {
        "files": len(records), "pages": len(records),
        "source_view_files": sum(r["source_view"] for r in records),
        "metadata_only_files": sum(not r["source_view"] for r in records),
        "source_lines": sum(r["line_count"] for r in records if r["source_view"]),
        "symbols": sum(len(r["structure"]["symbols"]) for r in records),
        "syntax_tree_files": sum(r["structure"]["parser_quality"] == "complete_syntax_tree" for r in records),
        "rendered_browser_coverage_claimed": False,
        "behavioral_execution_claimed": False,
    }


def safe_outputs(root: Path, output: Path, manifest: Path, discovered: dict) -> None:
    """Refuse to overwrite authored inputs or introduce inventory recursion."""
    sources = {(root / p).resolve() for p in discovered["files"]}
    if manifest in sources:
        raise ReferenceError("Manifest path would overwrite an authored input")
    if any((output / (p + ".md")).resolve() in sources for p in discovered["files"]):
        raise ReferenceError("Generated page would overwrite an authored input")
    from .inventory import exclusion, ROOTS
    for destination in (output, manifest):
        if destination.is_relative_to(root):
            relative = destination.relative_to(root).as_posix()
            if relative.split("/", 1)[0] in ROOTS and not exclusion(relative + "/"):
                raise ReferenceError("Output must be outside inventoried authored trees or under excluded generated documentation content")


def build(root: Path, output: Path, manifest: Path, guide_manifest: Path | None = None) -> dict:
    """Generate every file reference, then re-read inputs to reject source drift."""
    root, output, manifest = root.resolve(), output.resolve(), manifest.resolve()
    discovered = inventory(root)
    if not discovered["files"]: raise ReferenceError("No authored files found")
    safe_outputs(root, output, manifest, discovered)
    guide_metadata, guide_rows = guide_input(root, guide_manifest)
    parsed = inspect(root, discovered["files"])
    records = []
    expected_pages = {r["page"] for r, _ in parsed}
    if output.exists():
        present = {p.relative_to(output).as_posix() for p in output.rglob("*.md")}
        if present - expected_pages: raise ReferenceError("Output contains stale or unrelated pages; use a fresh output directory")
    for record, source in parsed:
        attach_guides(record, guide_rows)
        page = render(record, source).encode()
        record["page_sha256"] = digest(page)
        destination = output / record["page"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(page)
        records.append(record)
    current = inventory(root)
    if discovered["files"] != current["files"] or any(digest((root / r["path"]).read_bytes()) != r["sha256"] for r in records):
        raise ReferenceError("Source changed during reference generation")
    if guide_input(root, guide_manifest)[0] != guide_metadata: raise ReferenceError("Guide manifest changed during generation")
    result = {"version": VERSION, "inventory": discovered, "guide_manifest": guide_metadata, "files": records, "summary": summary(records),
        "derivation": "Definitions and facts come from language syntax trees; source-only formats and metadata-only inputs are explicitly classified. Browser rendering and runtime behavior are separate qualification tasks."}
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result["summary"]


def audit(root: Path, output: Path, manifest: Path, guide_manifest: Path | None = None) -> dict:
    """Independently rediscover/reparse all source and reject omissions/tampering."""
    root, output = root.resolve(), output.resolve()
    try:
        claimed = json.loads(manifest.read_text())
    except (ValueError, OSError) as exc:
        raise ReferenceError("Reference manifest cannot be read") from exc
    if claimed.get("version") != VERSION: raise ReferenceError("Unsupported reference manifest version")
    discovered = inventory(root)
    guide_metadata, guide_rows = guide_input(root, guide_manifest)
    if claimed.get("guide_manifest") != guide_metadata: raise ReferenceError("Guide manifest drift")
    if any(claimed.get("inventory", {}).get(k) != discovered[k] for k in ("files", "policy")): raise ReferenceError("Source inventory drift or missing/extra input")
    rows = claimed.get("files", [])
    if [r.get("path") for r in rows] != discovered["files"]: raise ReferenceError("Manifest has missing, duplicate, unordered or extra files")
    actual = inspect(root, discovered["files"])
    pages = {p.relative_to(output).as_posix() for p in output.rglob("*.md")}
    if pages != {r["page"] for r, _ in actual}: raise ReferenceError("Generated page inventory has missing or extra files")
    for recorded, (current, source) in zip(rows, actual):
        attach_guides(current, guide_rows)
        expected = render(current, source).encode()
        current["page_sha256"] = digest(expected)
        if current != recorded: raise ReferenceError(f"Source/contract/hash drift or symbol omission: {current['path']}")
        data = (output / current["page"]).read_bytes()
        if data != expected: raise ReferenceError(f"Generated reference content changed: {current['path']}")
        page = data.decode()
        anchors = Counter(re.findall(r'\bid="([^"]+)"', page))
        for number in range(1, current["line_count"] + 1 if current["source_view"] else 1):
            if anchors[f"L{number}"] != 1: raise ReferenceError(f"Missing/duplicate source line anchor: {current['path']}:{number}")
        for symbol in current["structure"]["symbols"]:
            if anchors[symbol["anchor"]] != 1: raise ReferenceError("Missing/duplicate symbol coverage")
    verified = summary([r for r, _ in actual])
    if claimed.get("summary") != verified: raise ReferenceError("Reference summary differs from independently observed inventory")
    return {**verified, "verified": True, "unresolved": []}
