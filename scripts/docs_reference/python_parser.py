"""Extract Python contracts from syntax without importing the documented code."""

import ast
import io
import platform
import tokenize


DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def parse_python(source: str) -> dict:
    """Return exact identities plus syntax-derived signatures and behavior facts."""
    tree = ast.parse(source, type_comments=True)
    raw = source.encode()
    offsets, total = [], 0
    for line in raw.splitlines(keepends=True):
        offsets.append(total)
        total += len(line)

    def text(node):
        return raw[offsets[node.lineno - 1] + node.col_offset:
                   offsets[node.end_lineno - 1] + node.end_col_offset].decode() if node is not None else None

    def header(node):
        fragment = text(node)
        depth = 0
        for tok in tokenize.generate_tokens(io.StringIO(fragment).readline):
            if tok.type == tokenize.OP:
                if tok.string in "([{": depth += 1
                elif tok.string in ")]}": depth -= 1
                elif tok.string == ":" and depth == 0:
                    parts = fragment.splitlines(keepends=True)
                    return "".join(parts[:tok.end[0] - 1]) + parts[tok.end[0] - 1][:tok.end[1]]
        return fragment

    def facts(node):
        result = []
        def add(child, kind, expression=None):
            result.append({"kind": kind, "line": child.lineno, "end_line": child.end_lineno,
                           "expression": expression, "source": text(child)})
        def visit(child):
            if child is not node and isinstance(child, DEFINITIONS + (ast.Lambda,)): return
            if isinstance(child, ast.Call):
                add(child, "call", text(child.func))
                name = text(child.func) or ""
                if any(part in name.lower() for part in ("write", "read", "open", "send", "request", "execute", "commit", "unlink", "replace", "mkdir", "spawn")):
                    add(child, "possible_io_or_mutation_call", name)
                for arg in child.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.lstrip().upper().startswith(("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER ", "PRAGMA ")):
                        add(arg, "sql_literal_argument", arg.value)
            elif isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete, ast.NamedExpr)):
                add(child, "assignment_or_deletion")
            elif isinstance(child, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.Match, ast.Try, ast.TryStar, ast.ExceptHandler, ast.Assert, ast.With, ast.AsyncWith)):
                add(child, "branch_or_control", text(getattr(child, "test", None)))
            elif isinstance(child, ast.Raise): add(child, "raise", text(child.exc))
            elif isinstance(child, (ast.Return, ast.Yield, ast.YieldFrom)): add(child, type(child).__name__.lower(), text(child.value))
            elif isinstance(child, ast.Await): add(child, "await", text(child.value))
            for nested in ast.iter_child_nodes(child): visit(nested)
        visit(node)
        return sorted(result, key=lambda row: (row["line"], row["kind"], row["source"] or ""))

    def arguments(node):
        if isinstance(node, ast.ClassDef): return []
        a = node.args; out = []
        positional = a.posonlyargs + a.args
        defaults = [None] * (len(positional) - len(a.defaults)) + a.defaults
        for i, (arg, default) in enumerate(zip(positional, defaults)):
            out.append({"name": arg.arg, "kind": "positional_only" if i < len(a.posonlyargs) else "positional_or_keyword",
                        "annotation": text(arg.annotation), "has_default": default is not None, "default": text(default)})
        if a.vararg: out.append({"name": a.vararg.arg, "kind": "variadic_positional", "annotation": text(a.vararg.annotation), "has_default": False, "default": None})
        for arg, default in zip(a.kwonlyargs, a.kw_defaults):
            out.append({"name": arg.arg, "kind": "keyword_only", "annotation": text(arg.annotation), "has_default": default is not None, "default": text(default)})
        if a.kwarg: out.append({"name": a.kwarg.arg, "kind": "variadic_keyword", "annotation": text(a.kwarg.annotation), "has_default": False, "default": None})
        return out

    symbols = []
    def walk(node, parents):
        for child in ast.iter_child_nodes(node):
            ancestry = parents
            if isinstance(child, ast.Lambda):
                target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else getattr(node, "target", None)
                name = target.id if isinstance(target, ast.Name) else f"<lambda@{child.lineno}:{child.col_offset}>"
                names = []
                for parent in parents:
                    names.append(parent["name"])
                    if parent["kind"] != "class": names.append("<locals>")
                names.append(name)
                observations = facts(child)
                observations.append({"kind": "implicit_return", "line": child.body.lineno, "end_line": child.body.end_lineno,
                                     "source": text(child.body), "expression": text(child.body)})
                symbols.append({"name": name, "qualified_name": ".".join(names), "kind": "lambda",
                    "line": child.lineno, "column": child.col_offset, "end_line": child.end_lineno,
                    "start_line": child.lineno, "signature": text(child), "parameters": arguments(child),
                    "decorators": [], "returns_annotation": None, "bases": [], "fields": [],
                    "docstring": None, "facts": observations, "parents": parents})
                ancestry = parents + [{"name": name, "kind": "lambda", "line": child.lineno}]
            if isinstance(child, ast.TypeAlias):
                names = []
                for parent in parents:
                    names.append(parent["name"])
                    if parent["kind"] != "class": names.append("<locals>")
                names.append(child.name.id)
                symbols.append({"name": child.name.id, "qualified_name": ".".join(names), "kind": "type_alias",
                    "line": child.lineno, "column": child.col_offset, "end_line": child.end_lineno,
                    "start_line": child.lineno, "signature": text(child),
                    "parameters": [{"name": p.name, "kind": "type_parameter", "annotation": text(getattr(p, "bound", None)),
                                    "has_default": False, "default": None} for p in child.type_params],
                    "decorators": [], "returns_annotation": text(child.value), "bases": [], "fields": [],
                    "docstring": None, "facts": facts(child), "parents": parents})
            if isinstance(child, DEFINITIONS):
                kind = "class" if isinstance(child, ast.ClassDef) else "async_function" if isinstance(child, ast.AsyncFunctionDef) else "function"
                names = []
                for parent in parents:
                    names.append(parent["name"])
                    if parent["kind"] != "class": names.append("<locals>")
                names.append(child.name)
                decorators = [{"line": d.lineno, "source": text(d)} for d in child.decorator_list]
                symbols.append({"name": child.name, "qualified_name": ".".join(names), "kind": kind,
                    "line": child.lineno, "column": child.col_offset, "end_line": child.end_lineno,
                    "start_line": min([child.lineno] + [d["line"] for d in decorators]),
                    "signature": header(child), "parameters": arguments(child), "decorators": decorators,
                    "returns_annotation": text(getattr(child, "returns", None)),
                    "bases": [text(b) for b in getattr(child, "bases", [])],
                    "fields": [{"name": text(item.target), "annotation": text(item.annotation),
                                "has_default": item.value is not None, "default": text(item.value),
                                "line": item.lineno, "source": text(item)}
                               for item in child.body if isinstance(item, ast.AnnAssign)] if isinstance(child, ast.ClassDef) else [],
                    "docstring": ast.get_docstring(child, clean=False), "facts": facts(child),
                    "parents": parents})
                ancestry = parents + [{"name": child.name, "kind": kind, "line": child.lineno}]
            walk(child, ancestry)
    walk(tree, [])
    callable_count = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) for n in ast.walk(tree))
    if callable_count != sum(s["kind"] in {"function", "async_function", "lambda"} for s in symbols):
        raise ValueError("Unaccounted Python callable")
    return {"parser": "python-ast", "parser_version": platform.python_version(), "parser_quality": "complete_syntax_tree", "limitations": [
        "Syntax evidence does not resolve dynamic dispatch, prove branches execute, infer return types, or establish I/O authority.",
        "Possible I/O/mutation calls are name-based candidates, not semantic effect classification."],
        "module_docstring": ast.get_docstring(tree, clean=False), "symbols": symbols, "facts": facts(tree),
        "parser_callable_count": callable_count}
