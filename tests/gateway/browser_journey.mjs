/** Production browser -> actual standalone/embedded gateway -> HTTP provider -> agent. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import { nativeJourneys } from "./native_browser.mjs";

const rawPath = process.argv[2] || "evidence/sturgis-03/gateway-v8.json";
const out = path.dirname(rawPath);
await fs.mkdir(out, { recursive: true });
const proc = spawn(".venv/bin/python", ["-m", "tests.gateway.browser_server"], { stdio: ["ignore", "pipe", "inherit"] });
const info = await Promise.race([
  once(createInterface({ input: proc.stdout }), "line").then(([line]) => JSON.parse(line)),
  once(proc, "exit").then(([code]) => { throw new Error(`Gateway fixture exited ${code}`); }),
  new Promise((_, reject) => setTimeout(() => reject(new Error("Gateway fixture startup timed out")), 15000).unref()),
]);
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors = [], coverage = [], evidence = { status: "running", backend: "actual HTTP; controlled upstream provider", journeys: [], accessibility: [] };
page.on("pageerror", error => errors.push(error.message));
await page.coverage.startJSCoverage({ resetOnNavigation: false });
const click = name => page.getByRole("button", { name, exact: true }).click();
const fill = (name, value) => page.getByLabel(name, { exact: true }).fill(value);
const tab = name => page.getByRole("tab", { name, exact: true }).click();
const nav = name => page.getByRole("navigation", { name: name === "Model Gateway" ? "Installation navigation" : "Main navigation" }).getByRole("button", { name, exact: true }).click();
async function checkpoint() {
  coverage.push(...await page.coverage.stopJSCoverage());
  await page.coverage.startJSCoverage({ resetOnNavigation: false });
}
async function accessible(label) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  assert.deepEqual(result.violations.filter(v => ["serious", "critical"].includes(v.impact)), [], label);
  evidence.accessibility.push({ label, violations: result.violations.map(v => ({ id: v.id, impact: v.impact })) });
}
async function connect(name) {
  await fill("Search providers", "Custom OpenAI");
  await click("Connect Custom OpenAI-compatible");
  await fill("Provider name", name);
  await fill("Endpoint", info.endpoint);
  await page.locator('input[name="gateway-provider-secret"]').fill("fixture-upstream-private-key");
  await click("Load models");
  await page.getByRole("checkbox", { name: "model-b model-b" }).check();
  await accessible("provider drawer");
  await click("Connect provider");
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page.getByRole("heading", { name, exact: true }).waitFor();
}
async function api(url, key, model) {
  const response = await fetch(url + "/chat/completions", { method: "POST", headers: { Authorization: "Bearer " + key, "Content-Type": "application/json" },
    body: JSON.stringify({ model, messages: [{ role: "user", content: "Hello gateway" }] }) });
  assert.equal(response.status, 200, await response.clone().text());
  assert.equal((await response.json()).choices[0].message.content, "Gateway response");
}
try {
  await page.goto(info.url);
  await fill("Email", "gateway-browser@example.com");
  await fill("Password", "gateway-browser-private-password");
  await fill("Installation token", "gateway-browser-token");
  await click("Create owner account");
  await nav("Model Gateway");
  await connect("My gateway provider");
  evidence.journeys.push("Connect and discover provider before creating any agent");
  await tab("Models");
  await page.getByRole("heading", { name: "model-b", exact: true }).waitFor();
  await page.screenshot({ path: path.join(out, "gateway-models-desktop.png"), fullPage: true });
  await accessible("embedded models");
  await nav("Conversation");
  await fill("Agent name", "Gateway companion");
  await click("Create agent");
  await page.getByLabel("Message", { exact: true }).waitFor();
  await nav("Settings");
  await click("Use model");
  await page.getByText("Currently using", { exact: false }).waitFor();
  await nav("Conversation");
  await fill("Message", "Hello through the gateway");
  await click("Send message");
  await page.getByText("Gateway response", { exact: true }).waitFor();
  evidence.journeys.push("Select gateway model, probe automatically and receive an actual private-agent reply");
  await nav("Model Gateway");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(out, "gateway-mobile.png"), fullPage: true });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Gateway mobile overflow");
  await accessible("embedded mobile gateway");
  await page.setViewportSize({ width: 1440, height: 1000 });
  const consumerCsrf = (await context.cookies()).find(cookie => cookie.name === "sturgis_csrf")?.value;
  assert.ok(consumerCsrf);
  await checkpoint();
  await page.goto(info.standalone);
  await accessible("standalone sign-in");
  await fill("Administrator key", info.admin_key);
  await click("Open gateway");
  evidence.journeys.push(...await nativeJourneys(page, info));
  await connect("Standalone provider");
  await tab("Model routes");
  await click("Create route");
  await fill("Route name", "Daily route");
  await page.getByRole("checkbox", { name: "Standalone provider · model-b" }).check();
  await click("Save route");
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await tab("Access & usage");
  await click("Create API key");
  await fill("Key name", "Browser external client");
  await click("Generate key");
  const key = await page.getByLabel("Gateway API key", { exact: true }).inputValue();
  await click("Done");
  await api(info.standalone + "/v1", key, "daily-route");
  await page.getByText("Advanced: limits & configuration transfer", { exact: true }).click();
  await fill("Requests per minute", "80");
  await click("Save gateway limits");
  await page.getByText("Gateway limits saved.", { exact: true }).waitFor();
  const downloading = page.waitForEvent("download");
  await click("Download configuration");
  const download = await downloading;
  const archivePath = path.join(out, "gateway-config.fixture.json");
  await download.saveAs(archivePath);
  const archive = JSON.parse(await fs.readFile(archivePath, "utf8"));
  assert.equal(archive.credentials_included, false);
  assert.ok(!JSON.stringify(archive).includes("fixture-upstream-private-key"));
  await page.getByLabel("Import configuration file", { exact: true }).setInputFiles(archivePath);
  await click("Import configuration");
  await page.getByText(/Imported 1 connections and 1 routes/).waitFor();
  await tab("Providers");
  await page.getByText("Reconnect required", { exact: true }).waitFor();
  await tab("Access & usage");
  const appKeyRow = page.locator("article.record").filter({ has: page.getByRole("heading", { name: "Browser external client", exact: true }) });
  await appKeyRow.getByRole("button", { name: "Revoke", exact: true }).click();
  await click("Revoke key");
  await appKeyRow.getByText("Revoked", { exact: true }).waitFor();
  assert.equal((await fetch(info.standalone + "/v1/models", { headers: { Authorization: "Bearer " + key } })).status, 401);
  await accessible("standalone management");
  await page.screenshot({ path: path.join(out, "gateway-standalone-desktop.png"), fullPage: true });
  await click("Sign out");
  await page.getByLabel("Administrator key", { exact: true }).waitFor();
  assert.equal((await context.cookies()).find(cookie => cookie.name === "sturgis_csrf")?.value, consumerCsrf);
  await checkpoint();
  await page.goto(info.url);
  await nav("Model Gateway");
  await tab("Access & usage");
  await click("Create API key");
  await fill("Key name", "Consumer session still works");
  await click("Generate key");
  await page.getByLabel("Gateway API key", { exact: true }).waitFor();
  await click("Done");
  evidence.journeys.push("Independent sign-in, connection, route, API key, real external inference, settings, export/import, revoke and logout");
  evidence.journeys.push("Standalone login/logout preserves the consumer session on the same hostname");
  assert.deepEqual(errors, [], "Browser runtime errors");
  evidence.status = "passed";
  console.log(JSON.stringify(evidence));
} catch (error) {
  evidence.status = "failed";
  evidence.error = String(error);
  await page.screenshot({ path: path.join(out, "gateway-failure.png"), fullPage: true });
  throw error;
} finally {
  coverage.push(...await page.coverage.stopJSCoverage());
  await fs.writeFile(rawPath, JSON.stringify(coverage));
  await fs.writeFile(path.join(out, "gateway-browser-quality.json"), JSON.stringify(evidence, null, 2));
  await browser.close();
  proc.kill("SIGTERM");
  await Promise.race([once(proc, "exit"), new Promise(resolve => setTimeout(() => { proc.kill("SIGKILL"); resolve(); }, 15000).unref())]);
}
