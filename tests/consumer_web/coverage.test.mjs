import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Session } from "node:inspector/promises";
import vm from "node:vm";
import { createServer } from "node:http";
import { chromium } from "playwright";
import { transformWithOxc } from "vite";
import {
  baseline,
  browserCoverage,
  mergeCoverage,
  assertSnapshot,
  snapshot,
  walk,
} from "./coverage.mjs";

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "sturgis-coverage-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, "frontend/src"), { recursive: true });
  await fs.mkdir(path.join(root, "consumer/web/assets"), { recursive: true });
  const source =
    'function select(flag:boolean) {\n  if(flag) return "accepted";\n  return "rejected";\n}\n';
  const file = path.join(root, "frontend/src/live.ts");
  await fs.writeFile(file, source);
  await fs.writeFile(
    path.join(root, "frontend/src/unused.ts"),
    'export const dormant = () => {\n  return "never imported";\n};\n',
  );
  await fs.writeFile(
    path.join(root, "frontend/src/types.ts"),
    "export interface Evidence { value: string }\n",
  );
  const output = await transformWithOxc(source, file, { sourcemap: true });
  const code = output.code;
  const map =
    typeof output.map === "string" ? JSON.parse(output.map) : output.map;
  map.sources = [file];
  map.sourcesContent = [source];
  const bundle = path.join(root, "consumer/web/assets/app.js");
  await fs.writeFile(bundle, code);
  await fs.writeFile(bundle + ".map", JSON.stringify(map));
  await fs.mkdir(path.join(root, ".build"), { recursive: true });
  await fs.writeFile(
    path.join(root, ".build/web-modules.json"),
    JSON.stringify({
      "assets/app.js": {
        moduleIds: ["frontend/src/live.ts"],
        isEntry: true,
        isDynamicEntry: false,
      },
    }),
  );
  const session = new Session();
  session.connect();
  await session.post("Profiler.enable");
  await session.post("Profiler.startPreciseCoverage", {
    callCount: true,
    detailed: true,
  });
  vm.runInNewContext(
    code + "\nselect(true);",
    {},
    { filename: "sturgis-coverage-fixture.js" },
  );
  const { result } = await session.post("Profiler.takePreciseCoverage");
  await session.post("Profiler.stopPreciseCoverage");
  session.disconnect();
  const measured = result.find(
    (entry) => entry.url === "sturgis-coverage-fixture.js",
  );
  // The invocation is a harness suffix and not part of the production bundle.
  for (const fn of measured.functions)
    fn.ranges = fn.ranges
      .filter((range) => range.startOffset < code.length)
      .map((range) => ({
        ...range,
        endOffset: Math.min(range.endOffset, code.length),
      }));
  const entry = {
    ...measured,
    url: "http://127.0.0.1/assets/app.js",
    source: code,
  };
  return { root, file, bundle, entry };
}

test("real V8 execution maps to authored lines, includes unused/type-only sources and reports branches", async (t) => {
  const f = await fixture(t);
  const base = await baseline(f.root);
  const browser = await browserCoverage(f.root, [f.entry], base.authored);
  const result = mergeCoverage(f.root, base, base.coverage.toJSON(), browser);
  assert.deepEqual(Object.keys(result.summary.files), [
    "frontend/src/live.ts",
    "frontend/src/types.ts",
    "frontend/src/unused.ts",
  ]);
  assert.equal(result.summary.files["frontend/src/types.ts"].lines.total, 0);
  assert.equal(result.summary.files["frontend/src/unused.ts"].lines.covered, 0);
  assert.ok(result.summary.files["frontend/src/unused.ts"].lines.total >= 2);
  assert.ok(result.summary.files["frontend/src/live.ts"].lines.covered > 0);
  assert.ok(
    result.summary.files["frontend/src/live.ts"].uncovered_lines.includes(3),
  );
  assert.ok(result.summary.totals.branches.total > 0);
  assert.equal(result.summary.browser_bundles[0].observations, 1);
  assert.throws(
    () => mergeCoverage(f.root, base, {}, browser),
    /Vitest omitted/,
  );
});

test("coverage refuses stale source, changed scripts, invalid offsets and absent browser proof", async (t) => {
  const f = await fixture(t);
  const { authored } = await baseline(f.root);
  await assert.rejects(browserCoverage(f.root, [], authored), /no V8/);
  await assert.rejects(
    browserCoverage(
      f.root,
      [{ ...f.entry, url: "http://invalid/other.js" }],
      authored,
    ),
    /did not execute/,
  );
  await assert.rejects(
    browserCoverage(f.root, [{ ...f.entry, source: "changed" }], authored),
    /differs from built/,
  );
  await assert.rejects(
    browserCoverage(f.root, [{ ...f.entry, functions: [] }], authored),
    /Missing V8/,
  );
  const invalid = structuredClone(f.entry);
  invalid.functions[0].ranges[0].count = -1;
  await assert.rejects(
    browserCoverage(f.root, [invalid], authored),
    /Invalid V8/,
  );
  await fs.appendFile(f.file, "\nexport const changed=true;");
  await assert.rejects(
    browserCoverage(f.root, [f.entry], authored),
    /differs from authored/,
  );
});

test("coverage cannot silently exclude source or follow symlink inputs", async (t) => {
  const f = await fixture(t);
  await fs.appendFile(f.file, "\n/* v8 ignore next */\n");
  await assert.rejects(baseline(f.root), /Coverage exclusion/);
  await fs.symlink(f.file, path.join(f.root, "frontend/src/linked.ts"));
  await assert.rejects(walk(path.join(f.root, "frontend/src")), /non-regular/);
  assertSnapshot({ hash: "same" }, { hash: "same" });
  assert.throws(
    () => assertSnapshot({ hash: "before" }, { hash: "after" }),
    /changed while tests/,
  );
});

test("source maps require content and the full manifest detects built asset changes", async (t) => {
  const f = await fixture(t);
  const { authored } = await baseline(f.root);
  const map = JSON.parse(await fs.readFile(f.bundle + ".map", "utf8"));
  await fs.writeFile(
    f.bundle + ".map",
    JSON.stringify({ ...map, sourcesContent: [null] }),
  );
  await assert.rejects(
    browserCoverage(f.root, [f.entry], authored),
    /Missing map sourcesContent/,
  );
  await fs.writeFile(f.bundle + ".map", JSON.stringify({ ...map, version: 2 }));
  await assert.rejects(
    browserCoverage(f.root, [f.entry], authored),
    /Invalid source map/,
  );
  for (const name of [
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "CMakeLists.txt",
    ".build/native/sturgis-memory",
    "agent/runtime.py",
    "consumer/api.py",
    "model_gateway/service.py",
    "native/memory/main.cpp",
    "frontend/index.html",
    "frontend/tsconfig.json",
    "frontend/vite.config.ts",
    "frontend/vitest.config.ts",
    "frontend/licenses/dependency.LICENSE",
    "scripts/verify_web.mjs",
    "tests/consumer_web/journey.mjs",
    "tests/consumer_web/server.py",
    "tests/consumer_web/coverage.mjs",
    "tests/gateway/browser_journey.mjs",
    "frontend/tests/example.test.ts",
  ]) {
    const file = path.join(f.root, name);
    await fs.mkdir(path.dirname(file), { recursive: true });
    await fs.writeFile(file, "qualification input");
  }
  const before = await snapshot(f.root);
  assert.equal(
    before.files["consumer/web/assets/app.js"].bytes,
    Buffer.byteLength(f.entry.source),
  );
  assert.equal(before.files["package-lock.json"].sha256.length, 64);
  assert.equal(before.files["consumer/api.py"].sha256.length, 64);
  assert.equal(before.files["model_gateway/service.py"].sha256.length, 64);
  assert.equal(before.files["tests/gateway/browser_journey.mjs"].sha256.length, 64);
  assert.equal(
    before.files["frontend/licenses/dependency.LICENSE"].sha256.length,
    64,
  );
  await fs.appendFile(
    path.join(f.root, "consumer/api.py"),
    "\n# runtime changed",
  );
  const runtimeChanged = await snapshot(f.root);
  assert.throws(
    () => assertSnapshot(before, runtimeChanged),
    /changed while tests/,
  );
  await fs.appendFile(f.bundle, "\n// changed after execution");
  assert.throws(
    () => assertSnapshot(before, { ...before, files: {} }),
    /changed while tests/,
  );
  const after = await snapshot(f.root);
  assert.throws(() => assertSnapshot(before, after), /changed while tests/);
});

test("mapless assets require exact generated-only module evidence and never hide authored source", async (t) => {
  const f = await fixture(t),
    base = await baseline(f.root);
  const manifest = path.join(f.root, ".build/web-modules.json");
  const modules = JSON.parse(await fs.readFile(manifest, "utf8"));
  const name = "assets/generated.js",
    generated = path.join(f.root, "consumer/web", name);
  await fs.writeFile(generated, "export const runtime=true;\n");
  modules[name] = {
    moduleIds: ["\0rolldown/runtime.js"],
    isEntry: false,
    isDynamicEntry: false,
  };
  await fs.writeFile(manifest, JSON.stringify(modules));
  let result = await browserCoverage(f.root, [f.entry], base.authored);
  assert.equal(
    result.bundles.find((item) => item.generated_runtime).source_map_sha256,
    null,
  );
  modules[name].moduleIds.push("frontend/src/live.ts");
  await fs.writeFile(manifest, JSON.stringify(modules));
  await assert.rejects(
    browserCoverage(f.root, [f.entry], base.authored),
    /Missing source map/,
  );
  modules[name].moduleIds = ["frontend/src/theme.css"];
  await fs.writeFile(manifest, JSON.stringify(modules));
  await assert.rejects(
    browserCoverage(f.root, [f.entry], base.authored),
    /Missing source map/,
  );
  await fs.writeFile(generated, "\n");
  result = await browserCoverage(f.root, [f.entry], base.authored);
  assert.ok(result.bundles.some((item) => item.generated_css_bridge));
  delete modules[name];
  await fs.writeFile(manifest, JSON.stringify(modules));
  await assert.rejects(
    browserCoverage(f.root, [f.entry], base.authored),
    /module metadata/,
  );
});

test("real Chromium navigation checkpoints preserve execution from an unloaded lazy page", async (t) => {
  const f = await fixture(t);
  const sources = {
    "live.ts":
      'export function select(flag:boolean) {\n  if(flag) return "accepted";\n  return "rejected";\n}\ndocument.body.dataset.result=select(true);\n',
    "later.ts":
      'export function resume() {\n  return "resumed";\n}\ndocument.body.dataset.result=resume();\n',
  };
  const modules = {};
  for (const [name, source] of Object.entries(sources)) {
    const file = path.join(f.root, "frontend/src", name);
    await fs.writeFile(file, source);
    const transformed = await transformWithOxc(source, file, {
      sourcemap: true,
    });
    const map =
      typeof transformed.map === "string"
        ? JSON.parse(transformed.map)
        : transformed.map;
    map.sources = [file];
    map.sourcesContent = [source];
    const bundle = name === "live.ts" ? "assets/app.js" : "assets/later.js";
    await fs.writeFile(
      path.join(f.root, "consumer/web", bundle),
      transformed.code,
    );
    await fs.writeFile(
      path.join(f.root, "consumer/web", bundle + ".map"),
      JSON.stringify(map),
    );
    modules[bundle] = {
      moduleIds: ["frontend/src/" + name],
      isEntry: true,
      isDynamicEntry: false,
    };
  }
  await fs.writeFile(
    path.join(f.root, ".build/web-modules.json"),
    JSON.stringify(modules),
  );
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, "http://fixture");
    if (["/first", "/second"].includes(url.pathname)) {
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end(
        `<body><script type="module" src="/assets/${url.pathname === "/first" ? "app" : "later"}.js"></script></body>`,
      );
    } else if (["/assets/app.js", "/assets/later.js"].includes(url.pathname)) {
      response.writeHead(200, { "Content-Type": "application/javascript" });
      response.end(
        await fs.readFile(path.join(f.root, "consumer/web", url.pathname)),
      );
    } else {
      response.writeHead(404);
      response.end();
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.coverage.startJSCoverage({ resetOnNavigation: false });
  await page.goto(origin + "/first");
  await page.waitForFunction(() => document.body.dataset.result === "accepted");
  const first = await page.coverage.stopJSCoverage();
  await page.coverage.startJSCoverage({ resetOnNavigation: false });
  await page.goto(origin + "/second");
  await page.waitForFunction(() => document.body.dataset.result === "resumed");
  const last = await page.coverage.stopJSCoverage();
  assert.ok(first.some((entry) => entry.url.endsWith("/assets/app.js")));
  assert.ok(!last.some((entry) => entry.url.endsWith("/assets/app.js")));
  const base = await baseline(f.root);
  const checkpointed = mergeCoverage(
    f.root,
    base,
    base.coverage.toJSON(),
    await browserCoverage(f.root, [...first, ...last], base.authored),
  );
  const finalOnly = mergeCoverage(
    f.root,
    base,
    base.coverage.toJSON(),
    await browserCoverage(f.root, last, base.authored),
  );
  assert.ok(
    checkpointed.summary.files["frontend/src/live.ts"].lines.covered > 0,
  );
  assert.ok(
    checkpointed.summary.files["frontend/src/later.ts"].lines.covered > 0,
  );
  assert.equal(
    finalOnly.summary.files["frontend/src/live.ts"].lines.covered,
    0,
  );
  assert.ok(
    checkpointed.summary.files["frontend/src/live.ts"].uncovered_lines.includes(
      3,
    ),
  );
  assert.equal(
    checkpointed.summary.files["frontend/src/unused.ts"].lines.covered,
    0,
  );
});
