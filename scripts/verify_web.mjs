/** Complete authored-source frontend gate: component tests AND the real service/browser. */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { parseArgs } from "node:util";
import {
  snapshot,
  assertSnapshot,
  baseline,
  browserCoverage,
  mergeCoverage,
} from "../tests/consumer_web/coverage.mjs";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { values } = parseArgs({
  options: {
    output: { type: "string" },
    "gateway-browser": { type: "boolean", default: false },
  },
});
const out = path.resolve(values.output || path.join(root, "evidence/sturgis-02"));
await fs.mkdir(out, { recursive: true });
for (const file of [
  "web-coverage.json",
  "web-istanbul.json",
  "web-source-manifest.json",
  "browser-v8.json",
  "consumer-browser-v8.json",
  "gateway-browser-v8.json",
  "browser-quality.json",
  "gateway-browser-quality.json",
])
  await fs.rm(path.join(out, file), { force: true });
const before = await snapshot(root);
function run(args, timeout) {
  const result = spawnSync(process.execPath, args, {
    cwd: root,
    stdio: "inherit",
    timeout,
  });
  if (result.status !== 0)
    throw new Error(
      `Frontend qualification command failed: ${args.join(" ")} (${result.status ?? result.error})`,
    );
}
run(["--test", "tests/consumer_web/coverage.test.mjs"], 60_000);
run(
  [
    "node_modules/vitest/vitest.mjs",
    "run",
    "--config",
    "frontend/vitest.config.ts",
    "--coverage",
  ],
  180_000,
);
const raw = path.join(out, "browser-v8.json");
const consumerRaw = path.join(out, "consumer-browser-v8.json");
run(["tests/consumer_web/journey.mjs", consumerRaw], 300_000);
const batches = JSON.parse(await fs.readFile(consumerRaw, "utf8"));
if (values["gateway-browser"]) {
  const gatewayRaw = path.join(out, "gateway-browser-v8.json");
  run(["tests/gateway/browser_journey.mjs", gatewayRaw], 300_000);
  batches.push(...JSON.parse(await fs.readFile(gatewayRaw, "utf8")));
}
await fs.writeFile(raw, JSON.stringify(batches));
assertSnapshot(before, await snapshot(root));
const base = await baseline(root);
const browser = await browserCoverage(
  root,
  JSON.parse(await fs.readFile(raw, "utf8")),
  base.authored,
);
const result = mergeCoverage(
  root,
  base,
  JSON.parse(
    await fs.readFile(
      path.join(root, ".build/web-vitest/coverage-final.json"),
      "utf8",
    ),
  ),
  browser,
);
assertSnapshot(before, await snapshot(root));
result.summary.source_manifest_sha256 = createHash("sha256")
  .update(JSON.stringify(before, null, 2))
  .digest("hex");
result.summary.evidence_sha256 = {};
for (const file of [raw, path.join(root, ".build/web-vitest/coverage-final.json")])
  result.summary.evidence_sha256[path.relative(root, file)] = createHash("sha256")
    .update(await fs.readFile(file))
    .digest("hex");
await fs.writeFile(
  path.join(out, "web-istanbul.json"),
  JSON.stringify(result.istanbul, null, 2),
);
await fs.writeFile(
  path.join(out, "web-coverage.json"),
  JSON.stringify(result.summary, null, 2),
);
await fs.writeFile(
  path.join(out, "web-source-manifest.json"),
  JSON.stringify(before, null, 2),
);
const lines = result.summary.totals.lines;
const percent = (100 * lines.covered) / lines.total;
if (!lines.total || percent < 90)
  throw new Error(
    `Authored frontend executable line coverage ${percent.toFixed(4)}% is below 90%; see web-coverage.json uncovered_lines.`,
  );
console.log(
  `Component tests and actual browser journeys passed; authored frontend executable lines ${lines.covered}/${lines.total} (${percent.toFixed(4)}%). Branches ${result.summary.totals.branches.covered}/${result.summary.totals.branches.total}.`,
);
