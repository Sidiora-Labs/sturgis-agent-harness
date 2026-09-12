/** Parse authored JS/TS/JSX using Babel, without evaluating the input module. */
import { parse } from "@babel/parser";
import fs from "node:fs";
import { createRequire } from "node:module";
const parserVersion = createRequire(import.meta.url)("@babel/parser/package.json").version;

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const source = input.source;
const tree = parse(source, {
  sourceType: "unambiguous",
  plugins: ["jsx", ...(input.path.endsWith(".ts") || input.path.endsWith(".tsx") ? ["typescript"] : [])],
  errorRecovery: false,
});
const text = (node) => node ? source.slice(node.start, node.end) : null;
const functions = new Set(["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression", "ObjectMethod", "ClassMethod", "ClassPrivateMethod", "TSDeclareFunction", "TSMethodSignature", "TSCallSignatureDeclaration", "TSConstructSignatureDeclaration", "TSFunctionType", "TSConstructorType"]);
const named = new Set([...functions, "ClassDeclaration", "ClassExpression", "TSInterfaceDeclaration", "TSTypeAliasDeclaration", "TSEnumDeclaration"]);
const children = (node) => Object.entries(node).flatMap(([key, value]) => {
  if (["loc", "comments", "leadingComments", "trailingComments", "innerComments", "tokens"].includes(key)) return [];
  return (Array.isArray(value) ? value : [value]).filter((x) => x && typeof x === "object" && typeof x.type === "string");
});
function facts(root) {
  const rows = [];
  function add(node, kind, expression = null) {
    rows.push({ kind, line: node.loc.start.line, end_line: node.loc.end.line, source: text(node), expression });
  }
  function walk(node) {
    if (node !== root && (functions.has(node.type) || node.type === "ClassDeclaration" || node.type === "ClassExpression")) return;
    if (["CallExpression", "OptionalCallExpression", "NewExpression"].includes(node.type)) {
      const target = text(node.callee);
      add(node, "call", target);
      if (/fetch|request|send|write|read|setItem|removeItem|execute|dispatch|mutate|invalidate|setState/i.test(target)) add(node, "possible_io_or_mutation_call", target);
      for (const arg of node.arguments || []) if (arg.type === "StringLiteral" && /^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|PRAGMA)\s/i.test(arg.value)) add(arg, "sql_literal_argument", arg.value);
    } else if (["AssignmentExpression", "UpdateExpression", "VariableDeclarator"].includes(node.type)) add(node, "assignment_or_declaration");
    else if (["IfStatement", "ConditionalExpression", "SwitchStatement", "SwitchCase", "TryStatement", "CatchClause", "ForStatement", "ForOfStatement", "ForInStatement", "WhileStatement", "DoWhileStatement", "LogicalExpression"].includes(node.type)) add(node, "branch_or_control", text(node.test || node.discriminant));
    else if (node.type === "ThrowStatement") add(node, "raise", text(node.argument));
    else if (node.type === "ReturnStatement") add(node, "return", text(node.argument));
    else if (node.type === "YieldExpression") add(node, "yield", text(node.argument));
    else if (node.type === "AwaitExpression") add(node, "await", text(node.argument));
    for (const child of children(node)) walk(child);
  }
  walk(root);
  return rows.sort((a, b) => a.line - b.line || a.kind.localeCompare(b.kind) || a.source.localeCompare(b.source));
}
function parameters(node) {
  return (node.params || node.parameters || []).map((param) => {
    const p = param.type === "TSParameterProperty" ? param.parameter : param;
    const binding = p.type === "AssignmentPattern" ? p.left : p.type === "RestElement" ? p.argument : p;
    return { name: text(binding), kind: p.type === "RestElement" ? "rest" : "parameter", annotation: text(binding.typeAnnotation || p.typeAnnotation), has_default: p.type === "AssignmentPattern", default: p.type === "AssignmentPattern" ? text(p.right) : null, optional: !!binding.optional, source: text(param) };
  });
}
const symbols = [];
const accounted = new Set();
function add(node, name, parents, kind = node.type, signatureNode = node) {
  const body = signatureNode.body;
  const end = body ? body.start : signatureNode.end;
  const signature = source.slice(node.start, Math.max(node.start, end)).trim();
  const decorators = (node.decorators || []).map((d) => ({ line: d.loc.start.line, source: text(d) }));
  const result = { name, qualified_name: [...parents.map((p) => p.name), name].join("."), kind,
    line: node.loc.start.line, start_line: node.loc.start.line, column: node.loc.start.column, end_line: node.loc.end.line,
    signature: signature || text(node), parameters: parameters(signatureNode), decorators,
    returns_annotation: text(signatureNode.returnType), bases: [text(node.superClass)].filter(Boolean),
    fields: (Array.isArray(node.body?.body) ? node.body.body : node.typeAnnotation?.members || []).filter((n) => ["ClassProperty", "ClassPrivateProperty", "TSPropertySignature"].includes(n.type)).map((n) => ({
      name: text(n.key), annotation: text(n.typeAnnotation), optional: !!n.optional, readonly: !!n.readonly,
      has_default: n.value != null, default: text(n.value), line: n.loc.start.line, source: text(n),
    })),
    docstring: (node.leadingComments || []).map((c) => c.value).join("\n") || null,
    facts: facts(signatureNode), parents };
  symbols.push(result); accounted.add(node);
  return [...parents, { name, kind, line: node.loc.start.line }];
}
function walk(node, parents) {
  let next = parents;
  if (node.type === "VariableDeclarator" && node.id.type === "Identifier") {
    const initial = node.init;
    if (initial && functions.has(initial.type)) {
      next = add(node, node.id.name, parents, initial.type, initial);
      accounted.add(initial);
      walk(node.id, next);
      for (const child of children(initial)) walk(child, next);
      return;
    }
    // Bindings with callback initializers (e.g. forwardRef) preserve their name
    // as a lexical parent; the real callback is separately documented below.
    next = [...parents, { name: node.id.name, kind: "binding", line: node.loc.start.line }];
  } else if (named.has(node.type)) {
    const id = node.id || node.key;
    const name = id ? text(id) : `<anonymous@${node.loc.start.line}:${node.loc.start.column}>`;
    next = add(node, name, parents);
  } else if (node.type === "ObjectProperty" && node.key) {
    next = [...parents, { name: text(node.key), kind: "property", line: node.loc.start.line }];
  }
  for (const child of children(node)) walk(child, next);
}
walk(tree, []);
let callableCount = 0;
function count(node) {
  if (functions.has(node.type)) {
    callableCount++;
    if (!accounted.has(node)) throw new Error(`Unaccounted callable at ${node.loc.start.line}`);
  }
  for (const child of children(node)) count(child);
}
count(tree);
process.stdout.write(JSON.stringify({ parser: "babel-parser", parser_version: parserVersion, parser_quality: "complete_syntax_tree", parser_callable_count: callableCount,
  limitations: ["Syntax extraction does not resolve runtime types, dynamic dispatch, React effects, or prove a call executes.", "Possible I/O/mutation calls are name-based candidates; inferred names for anonymous callbacks preserve source coordinates."],
  symbols, facts: facts(tree), module_docstring: null }));
