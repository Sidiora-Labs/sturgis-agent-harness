/** Exact-source, AST-aware union of component and production-browser V8 evidence. */
import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath, pathToFileURL } from "node:url";
import { convert } from "ast-v8-to-istanbul";
import { parseAstAsync, transformWithOxc } from "vite";
import istanbul from "istanbul-lib-coverage";
const extension = /\.(?:ts|tsx|js|jsx)$/;
const slash = (value) => value.replaceAll("\\", "/");
const sha256 = (value) => createHash("sha256").update(value).digest("hex");
export async function walk(directory) {
  const result = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) result.push(...(await walk(file)));
    else if (entry.isFile()) result.push(file);
    else throw new Error(`Unexpected non-regular qualification input: ${file}`);
  }
  return result.sort();
}
export async function snapshot(root) {
  const sources = await walk(path.join(root, "frontend/src"));
  const licenses = await walk(path.join(root, "frontend/licenses"));
  const assets = await walk(path.join(root, "consumer/web"));
  const configuration = [
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "CMakeLists.txt",
    ".build/native/sturgis-memory",
    ".build/web-modules.json",
    "frontend/index.html",
    "frontend/tsconfig.json",
    "frontend/vite.config.ts",
    "frontend/vitest.config.ts",
    "scripts/verify_web.mjs",
    "tests/consumer_web/journey.mjs",
    "tests/consumer_web/server.py",
  ].map((file) => path.join(root, file));
  configuration.push(
    ...(await walk(path.join(root, "tests/consumer_web"))).filter((file) =>
      /^coverage.*\.mjs$/.test(path.basename(file)),
    ),
  );
  const tests = await walk(path.join(root, "frontend/tests"));
  const runtime = [];
  for (const directory of ["agent", "consumer"])
    runtime.push(
      ...(await walk(path.join(root, directory))).filter((file) =>
        file.endsWith(".py"),
      ),
    );
  runtime.push(
    ...(await walk(path.join(root, "native"))).filter((file) =>
      /\.(?:c|cc|cpp|cxx|h|hpp|hxx)$/.test(file),
    ),
  );
  const files = {};
  for (const file of [
    ...new Set([
      ...sources,
      ...licenses,
      ...assets,
      ...configuration,
      ...tests,
      ...runtime,
    ]),
  ].sort()) {
    const bytes = await fs.readFile(file);
    files[slash(path.relative(root, file))] = {
      sha256: sha256(bytes),
      bytes: bytes.length,
    };
  }
  return {
    version: "sturgis-web-evidence/1",
    authored_sources: sources
      .filter((file) => extension.test(file))
      .map((file) => slash(path.relative(root, file))),
    files,
  };
}
export function assertSnapshot(before, after) {
  if (JSON.stringify(before) !== JSON.stringify(after))
    throw new Error(
      "Qualification inputs changed while tests were running; rebuild and rerun.",
    );
}
function filePath(value) {
  return value.startsWith("file:") ? fileURLToPath(value) : path.resolve(value);
}
function onlyAuthored(map, authored) {
  const result = istanbul.createCoverageMap({});
  for (const [name, entry] of Object.entries(map)) {
    const file = filePath(name);
    if (authored.has(file))
      result.addFileCoverage({
        ...(typeof entry.toJSON === "function" ? entry.toJSON() : entry),
        path: file,
      });
  }
  return result;
}
function zero(file) {
  return {
    path: file,
    statementMap: {},
    fnMap: {},
    branchMap: {},
    s: {},
    f: {},
    b: {},
  };
}
export async function baseline(root) {
  const files = (await walk(path.join(root, "frontend/src"))).filter((file) =>
    extension.test(file),
  );
  if (!files.length) throw new Error("No authored frontend source files.");
  const authored = new Set(files);
  const coverage = istanbul.createCoverageMap({});
  for (const file of files) {
    const source = await fs.readFile(file, "utf8");
    if (/(?:istanbul|v8|c8|node:coverage)\s+ignore\b/.test(source))
      throw new Error(`Coverage exclusion in authored source: ${file}`);
    // Independently transpile every file, including unimported and tree-shaken code.
    const transformed = await transformWithOxc(source, file, {
      sourcemap: true,
      jsx: { runtime: "automatic", development: false },
    });
    const map =
      typeof transformed.map === "string"
        ? JSON.parse(transformed.map)
        : transformed.map;
    map.sources = [file];
    map.sourcesContent = [source];
    const converted = await convert({
      code: transformed.code,
      sourceMap: map,
      ast: parseAstAsync(transformed.code),
      coverage: { url: pathToFileURL(file).href, functions: [] },
    });
    const current = onlyAuthored(converted, authored);
    if (!current.files().includes(file)) current.addFileCoverage(zero(file));
    coverage.merge(current);
  }
  return { authored, coverage };
}
export async function browserCoverage(root, entries, authored) {
  if (!Array.isArray(entries) || !entries.length)
    throw new Error("Browser supplied no V8 script evidence.");
  const web = path.join(root, "consumer/web");
  const files = (await walk(web)).filter((file) => file.endsWith(".js"));
  if (!files.length) throw new Error("No production JavaScript bundles.");
  const coverage = istanbul.createCoverageMap({});
  const bundles = [];
  const modules = JSON.parse(
    await fs.readFile(path.join(root, ".build/web-modules.json"), "utf8"),
  );
  if (!modules || Array.isArray(modules) || typeof modules !== "object")
    throw new Error("Invalid build module manifest.");
  let observedAuthored = false;
  for (const file of files) {
    const code = await fs.readFile(file, "utf8");
    const relative = slash(path.relative(web, file));
    const metadata = modules[relative];
    if (
      !metadata ||
      !Array.isArray(metadata.moduleIds) ||
      metadata.moduleIds.some((id) => typeof id !== "string") ||
      typeof metadata.isEntry !== "boolean" ||
      typeof metadata.isDynamicEntry !== "boolean"
    )
      throw new Error(`Missing or invalid build module metadata: ${file}`);
    let mapBytes;
    try {
      mapBytes = await fs.readFile(file + ".map");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    const generatedRuntime =
      !mapBytes &&
      metadata.moduleIds.length === 1 &&
      metadata.moduleIds[0] === "\0rolldown/runtime.js" &&
      !metadata.isEntry &&
      !metadata.isDynamicEntry;
    const generatedCssBridge =
      !mapBytes &&
      !code.trim() &&
      metadata.moduleIds.length > 0 &&
      metadata.moduleIds.every((id) => id.endsWith(".css")) &&
      !metadata.isEntry &&
      !metadata.isDynamicEntry;
    if (!mapBytes && !generatedRuntime && !generatedCssBridge)
      throw new Error(`Missing source map for nongenerated bundle: ${file}`);
    const sourceMap = mapBytes
      ? JSON.parse(mapBytes)
      : { version: 3, sources: [], sourcesContent: [], mappings: "" };
    if (
      sourceMap.version !== 3 ||
      !Array.isArray(sourceMap.sources) ||
      !Array.isArray(sourceMap.sourcesContent) ||
      sourceMap.sources.length !== sourceMap.sourcesContent.length ||
      typeof sourceMap.mappings !== "string"
    )
      throw new Error(`Invalid source map: ${file}`);
    const mapped = [];
    sourceMap.sources = sourceMap.sources.map((source, index) => {
      if (
        typeof source !== "string" ||
        typeof sourceMap.sourcesContent[index] !== "string"
      )
        throw new Error(`Missing map sourcesContent: ${file}`);
      const resolved = source.startsWith("file:")
        ? fileURLToPath(source)
        : path.resolve(path.dirname(file), sourceMap.sourceRoot || "", source);
      if (
        resolved.startsWith(path.join(root, "frontend/src") + path.sep) &&
        !authored.has(resolved)
      )
        throw new Error(
          `Source map references absent authored source: ${resolved}`,
        );
      if (authored.has(resolved)) mapped.push({ file: resolved, index });
      return resolved;
    });
    delete sourceMap.sourceRoot;
    for (const item of mapped)
      if (
        (await fs.readFile(item.file, "utf8")) !==
        sourceMap.sourcesContent[item.index]
      )
        throw new Error(
          `Source map differs from authored source: ${item.file}`,
        );
    const urlPath = "/" + slash(path.relative(web, file));
    const observed = entries.filter(
      (entry) => new URL(entry.url, "http://invalid").pathname === urlPath,
    );
    const ast = await parseAstAsync(code);
    for (const entry of observed) {
      if (entry.source !== code)
        throw new Error(`Browser script differs from built asset: ${file}`);
      if (!Array.isArray(entry.functions) || !entry.functions.length)
        throw new Error(`Missing V8 function ranges: ${file}`);
      for (const fn of entry.functions) {
        if (
          !Array.isArray(fn.ranges) ||
          !fn.ranges.length ||
          typeof fn.isBlockCoverage !== "boolean"
        )
          throw new Error(`Invalid V8 function: ${file}`);
        for (const range of fn.ranges)
          if (
            !Number.isSafeInteger(range.startOffset) ||
            !Number.isSafeInteger(range.endOffset) ||
            !Number.isSafeInteger(range.count) ||
            range.startOffset < 0 ||
            range.endOffset <= range.startOffset ||
            range.endOffset > code.length ||
            range.count < 0
          )
            throw new Error(`Invalid V8 offset/count: ${file}`);
      }
      if (!generatedRuntime && !generatedCssBridge) {
        const converted = await convert({
          code,
          sourceMap,
          ast,
          coverage: {
            url: pathToFileURL(file).href,
            functions: entry.functions,
          },
        });
        coverage.merge(onlyAuthored(converted, authored));
        if (mapped.length) observedAuthored = true;
      }
    }
    bundles.push({
      path: slash(path.relative(root, file)),
      sha256: sha256(code),
      observations: observed.length,
      source_map_sha256: mapBytes ? sha256(mapBytes) : null,
      generated_runtime: generatedRuntime,
      generated_css_bridge: generatedCssBridge,
      module_ids: metadata.moduleIds,
      authored_sources: mapped.map((item) =>
        slash(path.relative(root, item.file)),
      ),
    });
  }
  if (!observedAuthored)
    throw new Error(
      "Browser did not execute any mapped authored frontend code.",
    );
  return { coverage, bundles };
}
export function mergeCoverage(root, base, unitData, browser) {
  const unit = onlyAuthored(unitData, base.authored);
  for (const file of base.authored)
    if (
      !unit.files().includes(file) &&
      base.coverage.fileCoverageFor(file).toSummary().lines.total
    )
      throw new Error(`Vitest omitted an executable authored source: ${file}`);
  // Istanbul's toJSON() retains FileCoverage instances; clone their serialized
  // values so a merge cannot contaminate a later comparison's zero baseline.
  const coverage = istanbul.createCoverageMap(
    JSON.parse(JSON.stringify(base.coverage)),
  );
  coverage.merge(unit);
  coverage.merge(browser.coverage);
  const files = {};
  const relative = istanbul.createCoverageMap({});
  for (const file of [...base.authored].sort()) {
    const name = slash(path.relative(root, file));
    const entry = coverage.fileCoverageFor(file);
    relative.addFileCoverage({ ...entry.toJSON(), path: name });
    files[name] = {
      ...entry.toSummary().toJSON(),
      uncovered_lines: Object.entries(entry.getLineCoverage())
        .filter(([, count]) => !count)
        .map(([line]) => Number(line)),
    };
  }
  return {
    istanbul: relative.toJSON(),
    summary: {
      version: "sturgis-web-coverage/1",
      measurement:
        "AST-aware V8 coverage of every authored frontend module; union of Vitest and production Chromium journeys",
      files,
      totals: relative.getCoverageSummary().toJSON(),
      component_totals: unit.getCoverageSummary().toJSON(),
      browser_totals: browser.coverage.getCoverageSummary().toJSON(),
      browser_bundles: browser.bundles,
    },
  };
}
