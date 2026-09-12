"""Real C++ syntax inventory; no compiler/type-resolution claims are inferred."""

import re
from importlib.metadata import version

from .inventory import ReferenceError


def parse_cpp(source: str, filename=None) -> dict:
    """Record all definitions, function-shaped declarations, aliases and lambdas."""
    if filename is not None:
        from .clang_parser import parse_translation_unit
        return parse_translation_unit(filename, source)
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_cpp
    except ImportError as exc:
        raise ReferenceError("C++ references require the pinned tree-sitter and tree-sitter-cpp tooling") from exc
    data = source.encode()
    tree = Parser(Language(tree_sitter_cpp.language())).parse(data)
    if tree.root_node.has_error:
        raise ReferenceError("C++ syntax contains parser errors or missing nodes; reference generation is incomplete")

    def text(node):
        return data[node.start_byte:node.end_byte].decode() if node is not None else None

    def field(node, name):
        return node.child_by_field_name(name) if node is not None else None

    def descendants(node):
        yield node
        for child in node.named_children:
            yield from descendants(child)

    def function_declarator(node):
        return next((n for n in descendants(node) if n.type in {"function_declarator", "abstract_function_declarator"}), None)

    scoped = {"namespace_definition", "class_specifier", "struct_specifier", "union_specifier", "enum_specifier", "function_definition", "lambda_expression"}

    def facts(owner):
        rows = []
        def walk(node):
            if node != owner and node.type in scoped: return
            kind = expression = None
            if node.type in {"call_expression", "new_expression"}:
                kind, expression = "call", text(field(node, "function") or field(node, "type"))
            elif node.type in {"assignment_expression", "update_expression", "init_declarator", "delete_expression"}: kind = "assignment_or_mutation"
            elif node.type in {"if_statement", "switch_statement", "case_statement", "for_statement", "for_range_loop", "while_statement", "do_statement", "try_statement", "catch_clause", "conditional_expression"}:
                kind, expression = "branch_or_control", text(field(node, "condition"))
            elif node.type == "throw_statement": kind, expression = "raise", text(node)
            elif node.type in {"return_statement", "co_return_statement", "co_yield_statement"}: kind, expression = "return" if "return" in node.type else "yield", text(node)
            elif node.type in {"string_literal", "raw_string_literal"} and re.search(r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|PRAGMA)\s", text(node)):
                kind, expression = "sql_literal_candidate", text(node)
            if kind:
                rows.append({"kind": kind, "line": node.start_point.row + 1, "end_line": node.end_point.row + 1, "source": text(node), "expression": expression})
                if kind == "call" and re.search(r"sqlite|read|write|send|open|close|rename|remove|sync", expression or "", re.I):
                    rows.append({**rows[-1], "kind": "possible_io_or_mutation_call"})
            for child in node.named_children: walk(child)
        walk(owner)
        return sorted(rows, key=lambda row: (row["line"], row["kind"], row["source"]))

    symbols, accounted = [], set()
    def add(node, name, parents, kind, declarator=None):
        body = field(node, "body")
        params = field(declarator, "parameters")
        parameters = []
        if params:
            for p in params.named_children:
                default = field(p, "default_value")
                parameters.append({"name": text(field(p, "declarator")), "kind": p.type,
                    "annotation": text(field(p, "type")), "has_default": default is not None,
                    "default": text(default), "source": text(p)})
        qualified = "::".join([p["name"] for p in parents] + [name])
        row = {"name": name, "qualified_name": qualified, "kind": kind,
            "line": node.start_point.row + 1, "start_line": node.start_point.row + 1,
            "column": node.start_point.column, "end_line": node.end_point.row + 1,
            "signature": data[node.start_byte:body.start_byte].decode().strip() if body else text(node),
            "parameters": parameters, "decorators": [], "returns_annotation": text(field(node, "type")),
            "bases": [text(c) for c in node.named_children if c.type == "base_class_clause"],
            "fields": [{"name": text(field(c, "declarator")), "annotation": text(field(c, "type")),
                        "line": c.start_point.row + 1, "source": text(c)}
                       for c in body.named_children if c.type == "field_declaration"] if body else [],
            "docstring": None, "facts": facts(node), "parents": parents}
        symbols.append(row)
        if declarator: accounted.add(declarator.id)
        return parents + [{"name": name, "kind": kind, "line": row["line"]}]

    def walk(node, parents):
        ancestry = parents
        if node.type in scoped:
            declarator = function_declarator(node) if node.type in {"function_definition", "lambda_expression"} else None
            name_node = field(node, "name") or field(declarator, "declarator")
            name = text(name_node) or f"<anonymous@{node.start_point.row + 1}:{node.start_point.column}>"
            ancestry = add(node, name, parents, node.type, declarator)
        elif node.type in {"alias_declaration", "type_definition"}:
            name = text(field(node, "name") or field(node, "declarator"))
            if not name: raise ReferenceError("C++ alias has no accountable name")
            add(node, name, parents, node.type)
        elif node.type == "function_declarator" and node.id not in accounted:
            owner = node.parent
            # A function pointer/type declarator is recorded as such, without
            # inventing a callable runtime entity or inferred semantic return.
            name = text(field(node, "declarator")) or f"<declarator@{node.start_point.row + 1}>"
            add(owner, name, parents, "function_declaration", node)
        for child in node.named_children: walk(child, ancestry)
    walk(tree.root_node, [])
    declarators = [n for n in descendants(tree.root_node) if n.type == "function_declarator"]
    if any(n.id not in accounted for n in declarators):
        raise ReferenceError("Unaccounted C++ function declarator")
    return {"parser": "tree-sitter-cpp", "parser_version": version("tree-sitter-cpp"), "parser_quality": "complete_syntax_tree",
        "parser_callable_count": len(declarators), "symbols": symbols,
        "facts": facts(tree.root_node), "module_docstring": None,
        "limitations": ["This is a syntax tree, not compiler semantic analysis: includes/macros, overload resolution, templates and types are not evaluated.",
            "Function-shaped declarations may denote function pointers; source signatures preserve that distinction without claiming execution.",
            "Possible I/O and SQL candidates are lexical evidence; no external effect or reachable branch is proved."]}
