"""Extract own-file C++ definitions from a real compiler translation unit."""

from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
import re
import subprocess

from .inventory import ReferenceError


@lru_cache(maxsize=1)
def system_includes() -> tuple[str, ...]:
    """Read the installed C++ compiler's actual header search paths."""
    probe = subprocess.run(["g++", "-E", "-x", "c++", "-", "-v"], input="", text=True,
                           capture_output=True, timeout=30, check=False)
    if probe.returncode:
        raise ReferenceError("Cannot discover the installed C++ header search paths")
    match = re.search(r"#include <\.\.\.> search starts here:\n(.*?)End of search list\.", probe.stderr, re.S)
    if not match:
        raise ReferenceError("C++ compiler did not report its header search paths")
    return tuple(line.strip() for line in match.group(1).splitlines() if line.strip())


def compile_flags(filename: Path) -> list[str]:
    """Use the actual project's CMake standard/includes plus system headers."""
    args = ["-x", "c++"]
    cmake = next((p / "CMakeLists.txt" for p in filename.parents if (p / "CMakeLists.txt").is_file()), None)
    standard = "20"
    if cmake:
        content = cmake.read_text()
        setting = re.search(r"set\s*\(\s*CMAKE_CXX_STANDARD\s+(\d+)\s*\)", content, re.I)
        if not setting: raise ReferenceError("CMake C++ standard cannot be resolved for documentation")
        standard = setting.group(1)
        for block in re.findall(r"target_include_directories\s*\((.*?)\)", content, re.S):
            for include in block.split()[1:]:
                if include in {"PRIVATE", "PUBLIC", "INTERFACE", "SYSTEM", "BEFORE", "AFTER"}: continue
                if "$" in include: raise ReferenceError("Dynamic CMake include expressions need an explicit compiler configuration")
                args.append("-I" + str((cmake.parent / include).resolve()))
    args.append("-std=c++" + standard)
    args.extend("-I" + directory for directory in system_includes())
    return args


def parse_translation_unit(filename: Path, source: str) -> dict:
    """Parse exact source without executing it; reject compiler error diagnostics."""
    try:
        from clang import cindex
    except ImportError as exc:
        raise ReferenceError("Compiler references require the pinned libclang tooling") from exc
    filename = filename.resolve()
    args = compile_flags(filename)
    try:
        unit = cindex.Index.create().parse(str(filename), args=args, unsaved_files=[(str(filename), source)])
    except cindex.TranslationUnitLoadError as exc:
        raise ReferenceError(f"Compiler could not parse {filename.name}") from exc
    errors = [str(d) for d in unit.diagnostics if d.severity >= cindex.Diagnostic.Error]
    if errors: raise ReferenceError("C++ compiler rejected source: " + "; ".join(errors))
    raw = source.encode()

    def own(node):
        return node.location.file is not None and Path(node.location.file.name).resolve() == filename

    def text(node):
        return raw[node.extent.start.offset:node.extent.end.offset].decode()

    def qualified(node):
        names = [node.spelling] if node.spelling else []
        parent = node.semantic_parent
        while parent is not None and parent.kind.name != "TRANSLATION_UNIT":
            names.insert(0, parent.spelling or f"<anonymous@{parent.location.line}:{parent.location.column}>")
            parent = parent.semantic_parent
        return "::".join(names)

    callable_kinds = {"FUNCTION_DECL", "CXX_METHOD", "CONSTRUCTOR", "DESTRUCTOR", "FUNCTION_TEMPLATE", "CONVERSION_FUNCTION", "LAMBDA_EXPR"}
    kinds = callable_kinds | {"NAMESPACE", "CLASS_DECL", "STRUCT_DECL", "UNION_DECL", "CLASS_TEMPLATE", "CLASS_TEMPLATE_PARTIAL_SPECIALIZATION", "ENUM_DECL", "TYPEDEF_DECL", "TYPE_ALIAS_DECL", "TYPE_ALIAS_TEMPLATE_DECL"}

    def facts(owner):
        rows = []
        def walk(node):
            if node != owner and node.kind.name in kinds: return
            if not own(node): return
            kind = expression = None
            name = node.kind.name
            if name == "CALL_EXPR": kind, expression = "call", node.spelling or node.displayname or text(node)
            elif name in {"VAR_DECL", "CXX_DELETE_EXPR"}: kind = "assignment_or_mutation"
            elif name in {"BINARY_OPERATOR", "COMPOUND_ASSIGNMENT_OPERATOR", "UNARY_OPERATOR"}:
                if any(t.spelling in {"=", "+=", "-=", "*=", "/=", "++", "--"} for t in node.get_tokens()): kind = "assignment_or_mutation"
            elif name in {"IF_STMT", "SWITCH_STMT", "CASE_STMT", "FOR_STMT", "CXX_FOR_RANGE_STMT", "WHILE_STMT", "DO_STMT", "CXX_TRY_STMT", "CXX_CATCH_STMT", "CONDITIONAL_OPERATOR"}: kind = "branch_or_control"
            elif name == "CXX_THROW_EXPR": kind, expression = "raise", text(node)
            elif name == "RETURN_STMT": kind, expression = "return", text(node)
            elif name == "STRING_LITERAL" and re.search(r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|PRAGMA)\s", text(node)): kind, expression = "sql_literal_candidate", text(node)
            if kind:
                row = {"kind": kind, "line": node.extent.start.line, "end_line": node.extent.end.line,
                       "source": text(node), "expression": expression}
                if kind == "call" and node.referenced is not None: row["resolved_callee"] = qualified(node.referenced)
                rows.append(row)
                if kind == "call" and re.search(r"sqlite|read|write|send|open|close|rename|remove|sync", expression or "", re.I):
                    rows.append({**row, "kind": "possible_io_or_mutation_call"})
            for child in node.get_children(): walk(child)
        walk(owner)
        return sorted(rows, key=lambda r: (r["line"], r["kind"], r["source"]))

    symbols, seen = [], set()
    def walk(node, parents):
        ancestry = parents
        if node.kind.name != "TRANSLATION_UNIT" and not own(node): return
        if node.kind.name in kinds:
            name = node.spelling or f"<anonymous@{node.location.line}:{node.location.column}>"
            qname = qualified(node) or "::".join([p["name"] for p in parents] + [name])
            children = list(node.get_children())
            body = next((c for c in children if c.kind.name == "COMPOUND_STMT"), None)
            parameters = []
            for param in (c for c in children if c.kind.name == "PARM_DECL"):
                tokens = list(param.get_tokens())
                equal = next((t for t in tokens if t.spelling == "="), None)
                parameters.append({"name": param.spelling, "kind": "parameter", "annotation": param.type.spelling,
                    "has_default": equal is not None, "default": raw[equal.extent.end.offset:param.extent.end.offset].decode().strip() if equal else None, "source": text(param)})
            signature = raw[node.extent.start.offset:body.extent.start.offset].decode().strip() if body else text(node)
            identity = (qname, node.kind.name, node.extent.start.line, node.extent.start.column)
            if identity in seen: raise ReferenceError(f"Compiler emitted duplicate own-file symbol: {qname}")
            seen.add(identity)
            row = {"name": name, "qualified_name": qname, "kind": node.kind.name,
                "line": node.extent.start.line, "start_line": node.extent.start.line,
                "column": node.extent.start.column - 1, "end_line": node.extent.end.line,
                "signature": signature, "parameters": parameters, "decorators": [],
                "returns_annotation": node.result_type.spelling if node.kind.name in callable_kinds - {"LAMBDA_EXPR"} else None,
                "bases": [text(c) for c in children if c.kind.name == "CXX_BASE_SPECIFIER"],
                "fields": [{"name": c.spelling, "annotation": c.type.spelling, "line": c.extent.start.line, "source": text(c)}
                           for c in children if c.kind.name == "FIELD_DECL"],
                "docstring": node.raw_comment, "facts": facts(node), "parents": parents,
                "is_definition": node.is_definition()}
            symbols.append(row)
            ancestry = parents + [{"name": name, "kind": node.kind.name, "line": row["line"]}]
        for child in node.get_children(): walk(child, ancestry)
    walk(unit.cursor, [])
    return {"parser": "libclang-translation-unit", "parser_version": version("libclang"), "parser_quality": "complete_syntax_tree",
        "compiler_arguments": args, "compiler_diagnostics": [str(d) for d in unit.diagnostics],
        "parser_callable_count": sum(s["kind"] in callable_kinds for s in symbols),
        "symbols": symbols, "facts": [], "module_docstring": None,
        "limitations": ["Compiler declarations and call targets are extracted only for this exact file; included dependencies are not represented as authored definitions.",
            "Overload/declaration source identities are retained. Compiler syntax/types do not prove that branches execute or that external calls succeed.",
            "Possible I/O and SQL candidates remain lexical classifications; inspect exact source and runtime evidence for effects."]}
