/** Actual dashboard -> native sign-in -> live catalog -> gateway inference journeys. */
import assert from "node:assert/strict";
import AxeBuilder from "@axe-core/playwright";

const names = {
  "github-copilot": "GitHub Copilot",
  codex: "ChatGPT / Codex",
  "gemini-cli": "Gemini CLI",
  kiro: "Kiro",
};

/** Exercise provider UI against the fixture's real HTTP token/catalog/wire servers. */
export async function nativeJourneys(page, info) {
  const journeys = [];
  let key;
  const keyName = "Native account browser client";
  const click = (name) => page.getByRole("button", { name, exact: true }).click();
  const tab = (name) => page.getByRole("tab", { name, exact: true }).click();
  const readConnections = () => page.evaluate(async () => {
    const response = await fetch("/api/v1/gateway/connections");
    if (!response.ok) throw new Error("Could not read public gateway connections.");
    return (await response.json()).data;
  });
  assert.ok(Array.isArray(info.native), "The real browser fixture must advertise its native account providers");
  assert.equal(info.native.length, 4, "All four account providers must be configured");
  for (const provider of info.native) {
    const name = names[provider.id];
    assert.ok(name, "The fixture must use a supported native provider");
    const connectionName = `Native browser ${name}`;
    await tab("Providers");
    await page.getByLabel("Search providers", { exact: true }).fill("");
    await click(`Sign in to ${name}`);
    await page.getByLabel("Connection name", { exact: true }).fill(connectionName);
    await click(`Continue with ${name}`);

    const providerLink = page.getByRole("link", { name: `Open ${name} sign-in`, exact: true });
    await providerLink.waitFor();
    if (provider.id === "codex" || provider.id === "gemini-cli") {
      const authorization = new URL(await providerLink.getAttribute("href"));
      const redirect = authorization.searchParams.get("redirect_uri");
      const state = authorization.searchParams.get("state");
      assert.equal(redirect, provider.redirect_uri, "Use the registered fixture callback");
      assert.ok(state, "The provider authorization URL must bind callback state");
      const callback = new URL(redirect);
      callback.searchParams.set("state", state);
      callback.searchParams.set("code", "controlled-browser-authorization-code");
      // The controlled provider has issued a code. Reach the actual listener as
      // a provider redirect would; do not shortcut the gateway completion API.
      const returned = await fetch(callback);
      assert.equal(returned.status, 200, `${name} callback failed`);
      assert.match(await returned.text(), /Return to Model Gateway/);
      // Normal UI polling completes sign-in and removes this dialog. A manual
      // poll click races that success transition, so observe the connected
      // drawer below instead of interacting with a disappearing button.
    } else {
      assert.ok((await page.locator(".gateway-device-code strong").textContent()).trim(), `${name} device code missing`);
      // Respect the live session poll interval. The fixture token endpoint
      // grants the device authorization when the UI's next allowed poll runs.
    }

    await page.getByText(`Signed in as ${connectionName}`, { exact: true }).waitFor({ timeout: 30000 });
    assert.equal(await page.locator('input[name="gateway-provider-secret"]').count(), 0, "Native tokens must not become an API-key field");
    await click("Load models");
    await page.getByRole("checkbox").first().check();
    const drawer = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    assert.deepEqual(drawer.violations.filter((violation) => ["serious", "critical"].includes(violation.impact)), [], `${name} native import accessibility`);
    await click("Connect provider");
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    const connection = (await readConnections()).find((row) => row.name === connectionName);
    assert.ok(connection?.auth_account_id, `${name} import must bind its authenticated account`);
    assert.ok(connection.models.length, `${name} catalog must return an actual model`);
    const model = `${connection.id}/${connection.models[0].id}`;

    if (!key) {
      await tab("Access & usage");
      await click("Create API key");
      await page.getByLabel("Key name", { exact: true }).fill(keyName);
      await click("Generate key");
      key = await page.getByLabel("Gateway API key", { exact: true }).inputValue();
      assert.ok(key, "A gateway app key must be issued");
      await click("Done");
    }
    const response = await fetch(info.standalone + "/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ model, messages: [{ role: "user", content: "Reply with a short native account greeting." }], max_tokens: 64 }),
    });
    assert.equal(response.status, 200, `${name} native inference failed: ${await response.clone().text()}`);
    const completion = await response.json();
    const expectedReply = ["github-copilot", "codex"].includes(provider.id) ? "Hi there" : "Native account answer";
    assert.equal(completion.choices[0].message.content, expectedReply, `${name} must use the native provider wire`);
    assert.equal(completion.choices[0].finish_reason, "stop");

    await tab("Providers");
    await click(`Disconnect account ${connectionName}`);
    await click("Disconnect account");
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    assert.equal(await page.getByRole("button", { name: `Use ${connectionName}`, exact: true }).count(), 0);
    const disconnected = await fetch(info.standalone + "/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify({ model, messages: [{ role: "user", content: "Account access must be revoked." }] }),
    });
    assert.ok(disconnected.status >= 400, `${name} must not infer after account disconnect`);

    // Leave the installation clean for the general standalone/export journey.
    await click(`Disconnect ${connectionName}`);
    await click("Disconnect provider");
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    assert.ok(!(await readConnections()).some((row) => row.id === connection.id));
    journeys.push(`${name}: native sign-in, live model import, real gateway inference, account disconnect and connection cleanup`);
  }
  await tab("Access & usage");
  await page.locator("article.record").filter({ hasText: keyName }).getByRole("button", { name: "Revoke", exact: true }).click();
  await click("Revoke key");
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  assert.equal((await fetch(info.standalone + "/v1/models", { headers: { Authorization: "Bearer " + key } })).status, 401);
  await tab("Providers");
  return journeys;
}
