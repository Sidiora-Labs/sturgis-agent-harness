/** The production Material 3 client drives actual Python, private workers and native memory. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
const proc = spawn(".venv/bin/python", ["tests/consumer_web/server.py"], {
  stdio: ["ignore", "pipe", "inherit"],
});
const lines = createInterface({ input: proc.stdout });
const info = await Promise.race([
  once(lines, "line").then(([line]) => JSON.parse(line)),
  once(proc, "exit").then(([code]) => {
    throw new Error("Journey server exited " + code);
  }),
  new Promise((_, reject) =>
    setTimeout(
      () => reject(new Error("Server startup timeout")),
      15000,
    ).unref(),
  ),
]);
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const responses = [];
page.on("response", (response) => {
  if (response.status() >= 400 && !response.url().endsWith("/auth/me"))
    responses.push([response.status(), response.url()]);
});
await page.coverage.startJSCoverage({ resetOnNavigation: false });
const v8Batches = [];
let collecting = true;
async function checkpointCoverage() {
  v8Batches.push(...(await page.coverage.stopJSCoverage()));
  collecting = false;
  await page.coverage.startJSCoverage({ resetOnNavigation: false });
  collecting = true;
}
const click = (name) => page.getByRole("button", { name, exact: true }).click();
const fill = (name, value) =>
  page.getByLabel(name, { exact: true }).fill(value);
const card = (name) =>
  page
    .locator("section.st-card")
    .filter({ has: page.getByRole("heading", { name, exact: true }) });
const dialog = () => page.getByRole("dialog");
const nav = async (name) => {
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name, exact: true })
    .click();
  await page
    .getByRole("heading", {
      name:
        {
          Work: "Work, with continuity.",
          Connections: "Your agent, connected.",
        }[name] || name,
      exact: true,
    })
    .waitFor();
};
const tab = async (name) =>
  page.getByRole("tab", { name, exact: true }).click();
const select = async (label, option) => {
  await page.getByRole("combobox", { name: label, exact: true }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
};
const notices = () => page.locator(".notice-region").textContent();
async function noErrors() {
  assert.equal(await page.locator(".toast.error").count(), 0, await notices());
}
async function dismiss() {
  const buttons = page.getByRole("button", { name: "Dismiss notification" });
  while (await buttons.count()) {
    try {
      await buttons.first().click({ timeout: 1000 });
    } catch (error) {
      if (await buttons.count()) throw error;
    }
  }
}
async function axe(label) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  const violations = result.violations.filter((item) =>
    ["critical", "serious"].includes(item.impact),
  );
  assert.deepEqual(
    violations.map((v) => ({
      id: v.id,
      nodes: v.nodes.map((n) => ({
        target: n.target,
        html: n.html,
        reason: n.failureSummary,
      })),
    })),
    [],
    label + " accessibility",
  );
  return {
    label,
    violations: result.violations.map((v) => ({ id: v.id, impact: v.impact })),
  };
}
const evidence = { accessibility: [], performance: {}, requests: responses };
try {
  await page.addInitScript(() => {
    window.__longTasks = [];
    new PerformanceObserver((list) =>
      window.__longTasks.push(...list.getEntries().map((e) => e.duration)),
    ).observe({ type: "longtask", buffered: true });
  });
  await page.goto(info.url);
  await page.getByLabel("Email", { exact: true }).waitFor();
  evidence.accessibility.push(await axe("bootstrap"));
  await page.screenshot({
    path: "evidence/sturgis-02/auth-material3.png",
    fullPage: true,
  });
  await fill("Email", "browser@example.com");
  await fill("Password", "browser-private-password");
  await fill("Installation token", "browser-installation-token");
  await click("Create owner account");
  await fill("Agent name", "Research companion");
  await click("Create agent");
  await page.getByLabel("Message", { exact: true }).waitFor();
  await page.evaluate(() => document.fonts.ready);
  evidence.performance.coldAssets = await page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .filter((resource) => resource.name.includes("/assets/"))
      .map((resource) => ({
        url: new URL(resource.name).pathname,
        transferSize: resource.transferSize,
        encodedBodySize: resource.encodedBodySize,
        duration: resource.duration,
      })),
  );
  evidence.performance.initialJavaScriptBytes = evidence.performance.coldAssets
    .filter((resource) => resource.url.endsWith(".js"))
    .reduce((total, resource) => total + resource.encodedBodySize, 0);
  assert.ok(
    evidence.performance.initialJavaScriptBytes > 0 &&
      evidence.performance.initialJavaScriptBytes <= 250 * 1024,
    "Initial conversation JavaScript exceeds the 250 KiB compressed transfer budget",
  );
  await page.waitForFunction(() => {
    const welcome = document.querySelector(".conversation-welcome");
    return welcome && getComputedStyle(welcome).opacity === "1";
  });
  await dismiss();
  evidence.accessibility.push(await axe("empty conversation"));
  await page.screenshot({
    path: "evidence/sturgis-02/conversation-material3-empty.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: /Shape an idea/ }).click();
  assert.equal(
    await page.getByLabel("Message", { exact: true }).inputValue(),
    "Help me shape an idea",
  );
  await fill("Message", "Draft survives navigation");
  await nav("Settings");
  await click("Connect your first model");
  await fill("Endpoint", info.model);
  await fill("Model", "wire-browser");
  await fill("Output limit", "512");
  await click("Save provider");
  await card("Your models").getByText("Needs probe", { exact: true }).waitFor();
  await click("Probe model");
  await card("Your models").getByText("Ready", { exact: true }).waitFor();
  await click("Use model");
  await card("Your models").getByText("Selected", { exact: true }).waitFor();
  await click("Add credential");
  await fill("Credential name", "reference");
  await fill("Secret", "private-web-secret");
  await click("Store credential");
  await card("Private credentials")
    .getByText("reference", { exact: true })
    .waitFor();
  await click("Rotate secret");
  await fill("Secret", "rotated-private-web-secret");
  await click("Store credential");
  await tab("Procedures");
  await click("Add procedure");
  await fill("Procedure ID", "reading");
  await fill("Description", "Review supplied sources");
  await fill("Trigger words", "source");
  await fill("Procedure steps", "Read each source\nExplain uncertainty");
  await click("Install procedure");
  await click("Disable procedure");
  await click("Enable procedure");
  await tab("Permissions");
  await select("Policy for workspace.write", "Deny");
  await select("Policy for workspace.write", "Ask first");
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name: "Conversation", exact: true })
    .click();
  await page.getByLabel("Message", { exact: true }).waitFor();
  assert.equal(
    await page.getByLabel("Message", { exact: true }).inputValue(),
    "Draft survives navigation",
  );
  await fill(
    "Message",
    "slow browser request <script>window.injected=true</script>",
  );
  await page.getByLabel("Message", { exact: true }).press("Enter");
  await page
    .locator(".message-bubble.assistant")
    .filter({ hasText: "A real streamed reply." })
    .waitFor();
  assert.equal(await page.evaluate(() => window.injected), undefined);
  assert.equal(
    await page.locator(".message-body img,.message-body script").count(),
    0,
  );
  await select("Message mode", "Steer active work");
  await fill("Message", "Please change direction");
  await click("Send message");
  await page
    .locator(".message-bubble.user")
    .filter({ hasText: "Please change direction" })
    .waitFor();
  await click("Stop");
  await page.getByLabel("Attachment file", { exact: true }).setInputFiles({
    name: "attached.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Exact source supplied through the composer."),
  });
  await page
    .locator(".attachment-chips")
    .filter({ hasText: "attached.txt" })
    .waitFor();
  await fill("Message", "Use this source");
  await click("Send message");
  await page
    .locator(".message-bubble.user")
    .filter({ hasText: "Use this source" })
    .waitFor();
  await click("Conversation options");
  await page.getByRole("menuitem", { name: "Rename conversation" }).click();
  await fill("Conversation title", "Research thread");
  await click("Save");
  await page
    .getByRole("button", { name: "Research thread", exact: true })
    .waitFor();
  await click("New conversation");
  await page
    .locator(".thread-header-label")
    .filter({ hasText: /^New conversation$/ })
    .waitFor();
  await fill("Message", "Second conversation draft");
  await page.getByLabel("Message", { exact: true }).press("Shift+Enter");
  assert.match(
    await page.getByLabel("Message", { exact: true }).inputValue(),
    /\n/,
  );
  await click("Research thread");
  await page
    .locator(".message-bubble.user")
    .filter({ hasText: "Use this source" })
    .waitFor();
  await dismiss();
  evidence.accessibility.push(await axe("conversation"));
  await page.screenshot({
    path: "evidence/sturgis-02/conversation-material3-desktop.png",
    fullPage: true,
  });
  const input = page.getByLabel("Message", { exact: true });
  await input.fill("");
  const durations = [];
  for (let i = 0; i < 24; i++) {
    const start = performance.now();
    await input.press("a");
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => resolve())),
    );
    durations.push(performance.now() - start);
  }
  evidence.performance.inputFrameP95Ms = durations.sort((a, b) => a - b)[
    Math.floor(durations.length * 0.95)
  ];
  assert.ok(
    evidence.performance.inputFrameP95Ms < 180,
    "Input-to-frame p95 exceeded 180ms on local runner",
  );
  await input.fill("");
  await nav("Knowledge");
  await click("Add a source");
  await page.getByLabel("Source file", { exact: true }).setInputFiles({
    name: "research.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(
      "Cedar is the project concept. Source evidence remains available.",
    ),
  });
  await click("Upload source");
  const source = card("Your sources")
    .locator(".record")
    .filter({
      has: page.getByRole("heading", { name: "research.txt", exact: true }),
    });
  await source.getByRole("button", { name: "Add to knowledge" }).click();
  await card("Connected knowledge")
    .getByRole("heading", { name: "research.txt", exact: true })
    .waitFor();
  await click("Read source");
  await dialog()
    .getByText(
      "Cedar is the project concept. Source evidence remains available.",
      { exact: true },
    )
    .waitFor();
  await page.keyboard.press("Escape");
  const downloading = page.waitForEvent("download");
  await source.getByRole("link", { name: "Download" }).click();
  assert.equal((await downloading).suggestedFilename(), "research.txt");
  await tab("Agent memory");
  await fill("Search memory", "Cedar");
  await click("Search memory");
  await click("Correct memory");
  await fill(
    "Corrected memory",
    "Cedar is the current source-backed project concept.",
  );
  await click("Save correction");
  await page
    .locator(".record-title")
    .filter({ hasText: /User correction:/ })
    .waitFor();
  await click("Retract");
  await fill("Reason", "Superseded after review");
  await click("Retract memory");
  await tab("Source library");
  await page
    .getByRole("button", { name: "Remove research.txt from knowledge" })
    .click();
  await click("Remove from knowledge");
  await card("Connected knowledge")
    .getByRole("heading", { name: "A library your agent can use" })
    .waitFor();
  await nav("Files");
  await click("New file");
  await fill("Initial content", "A real workspace file.");
  await click("Create file");
  await page.getByLabel("File content", { exact: true }).waitFor();
  await fill("File content", "Updated with a checked preimage.");
  await click("Save file");
  await page.getByRole("button", { name: "Open file notes.txt" }).click();
  assert.equal(
    await page.getByLabel("File content", { exact: true }).inputValue(),
    "Updated with a checked preimage.",
  );
  await click("Delete file");
  await dialog()
    .getByRole("button", { name: "Delete file", exact: true })
    .click();
  await page.getByRole("heading", { name: "A clean workspace" }).waitFor();
  await nav("Connections");
  await page
    .locator(".page-heading")
    .getByRole("button", { name: "Add connection", exact: true })
    .click();
  await fill("Connection ID", "reference");
  await fill("Endpoint", info.model);
  await click("Configure connection");
  await click("Manage grants");
  await dialog().getByRole("checkbox", { name: /fetch/i }).check();
  await click("Save grants");
  await click("Test connection");
  await page.getByText("Ready", { exact: true }).waitFor();
  await nav("Work");
  await click("New goal");
  await fill("Desired outcome", "Make the research reviewable");
  await click("Create goal");
  await page
    .getByRole("heading", { name: "Make the research reviewable" })
    .waitFor();
  await dismiss();
  evidence.accessibility.push(await axe("work"));
  await page.screenshot({
    path: "evidence/sturgis-02/work-material3-desktop.png",
    fullPage: true,
  });
  await nav("Settings");
  await tab("Agent & data");
  await click("Runtime diagnostics");
  await page
    .getByRole("dialog")
    .getByText(/native_memory_available/)
    .waitFor();
  await page.keyboard.press("Escape");
  await click("Rename");
  await fill("Display name", "Research space");
  await click("Rename agent");
  await page.keyboard.press("Control+k");
  await page.getByRole("dialog", { name: "Go anywhere" }).waitFor();
  await fill("Search your space", "Knowledge");
  await page
    .getByRole("button", { name: "Knowledge", exact: true })
    .last()
    .click();
  await page.getByRole("heading", { name: "Knowledge", exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Conversation", exact: true })
    .click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page.getByLabel("Message", { exact: true }).waitFor();
  await page
    .locator(".message-bubble.user")
    .filter({ hasText: "Use this source" })
    .waitFor();
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
    "narrow overflow",
  );
  await page.screenshot({
    path: "evidence/sturgis-02/conversation-material3-mobile.png",
    fullPage: true,
  });
  evidence.accessibility.push(await axe("mobile conversation"));
  await page.emulateMedia({ reducedMotion: "reduce" });
  assert.equal(
    await page.evaluate(
      () => matchMedia("(prefers-reduced-motion: reduce)").matches,
    ),
    true,
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: /Account menu/ }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await fill("Email", "browser@example.com");
  await fill("Password", "wrong-private-password");
  await click("Sign in");
  await page.getByRole("alert").waitFor();
  await fill("Password", "browser-private-password");
  await click("Sign in");
  await page.getByLabel("Message", { exact: true }).waitFor();
  // Preserve counters before Chromium destroys this document's execution context.
  await checkpointCoverage();
  await page.reload();
  await page.getByLabel("Message", { exact: true }).waitFor();
  evidence.performance.longTasks = await page.evaluate(
    () => window.__longTasks,
  );
  evidence.performance.resources = await page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .filter((r) => r.name.includes("/assets/"))
      .map((r) => ({
        url: new URL(r.name).pathname,
        transferSize: r.transferSize,
        encodedBodySize: r.encodedBodySize,
        duration: r.duration,
      })),
  );
  assert.deepEqual(errors, []);
  await noErrors();
  await fs.writeFile(
    "evidence/sturgis-02/browser-quality.json",
    JSON.stringify(evidence, null, 2),
  );
  console.log(
    "Material 3 production browser journey passed: actual identity, worker/model streaming, source/native memory, file effects, connections, work, keyboard/mobile and safe rendering.",
  );
} catch (error) {
  await page.screenshot({
    path: "evidence/sturgis-02/browser-failure.png",
    fullPage: true,
  });
  console.error(
    "Browser errors:",
    errors,
    "Current notices:",
    await notices(),
    "HTTP:",
    responses,
  );
  throw error;
} finally {
  if (collecting) v8Batches.push(...(await page.coverage.stopJSCoverage()));
  await fs.writeFile(process.argv[2], JSON.stringify(v8Batches));
  await browser.close();
  proc.kill("SIGTERM");
  await Promise.race([
    once(proc, "exit"),
    new Promise((resolve) =>
      setTimeout(() => {
        proc.kill("SIGKILL");
        resolve();
      }, 10000).unref(),
    ),
  ]);
  lines.close();
}
