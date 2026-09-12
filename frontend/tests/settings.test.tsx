import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Blob as NodeBlob } from "node:buffer";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import SettingsPage from "../src/pages/SettingsPage";
import { mountPage } from "./harness";
const agent = {
  id: "agent",
  name: "Cedar research",
  revision: 7,
  archived: false,
};
function settings(
  extra: Parameters<typeof mountPage>[1] = {},
  override: Record<string, unknown> = {},
) {
  const overview = {
    agent_id: "agent",
    mode: "self_hosted",
    provider_settings: [],
    usage: {
      actual: { tokens: 12 },
      receipt_count: 1,
      unresolved_resources: {},
      unresolved_claims: [],
      overruns: [],
    },
    memory: { available: true },
    accepting: true,
    tools: [],
    ...override,
  };
  const onChanged = vi.fn().mockResolvedValue(undefined),
    onCreate = vi.fn(),
    onArchived = vi.fn().mockResolvedValue(undefined);
  return {
    onChanged,
    onCreate,
    onArchived,
    ...mountPage(
      <SettingsPage
        agent={agent}
        onChanged={onChanged}
        onCreate={onCreate}
        onArchived={onArchived}
      />,
      {
        "GET /agents/agent": () => ({
          ...agent,
          runtime: {
            desired: "running",
            observed: "running",
            next_wake_at: null,
            error: null,
            generation: 1,
          },
        }),
        overview: () => overview,
        "GET /gateway/models": () => [],
        "providers.credentials.list": () => [],
        "permissions.list": () => [],
        "permissions.pending": () => [],
        "skills.list": () => [],
        "plugins.list": () => [],
        ...extra,
      },
    ),
  };
}
const tab = async (name: string) => {
  await userEvent.click(screen.getByRole("tab", { name, exact: true }));
};

it("model configuration and edits carry declared capabilities and CAS revisions; failed readiness cannot activate", async () => {
  const models: Record<string, unknown>[] = [];
  let revision = 0,
    ready = false,
    probeFails = true;
  const page = settings({
    overview: () => ({
      mode: "self_hosted",
      provider_settings: models,
      usage: { actual: {}, unresolved_claims: [] },
      memory: { available: false },
    }),
    "providers.configure": (params) => {
      expect(params.expected_revision).toBe(revision);
      revision++;
      const model = {
        ...params,
        id: params.provider_id,
        revision,
        ready: false,
        active: false,
      };
      models.splice(0, models.length, model);
      return model;
    },
    "providers.probe": (params) => {
      expect(params.expected_revision).toBe(revision);
      if (probeFails)
        throw new Error("The model did not provide a valid stream");
      ready = true;
      models[0] = { ...models[0], ready, revision: ++revision };
      return models[0];
    },
    "providers.activate": (params) => {
      expect(params.expected_revision).toBe(revision);
      models[0] = { ...models[0], active: true, revision: ++revision };
      return models[0];
    },
  });
  await screen.findByText("Choose a mind for your agent");
  await userEvent.click(
    screen.getByText("Advanced: legacy model connections, credentials & usage"),
  );
  expect(screen.getByText("Unavailable")).toBeTruthy();
  await userEvent.click(
    screen.getByRole("button", { name: "Connect your first model" }),
  );
  fireEvent.change(screen.getByLabelText("Endpoint"), {
    target: { value: "https://model.example/chat/completions" },
  });
  fireEvent.change(screen.getByLabelText("Model"), {
    target: { value: "text-model" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Save provider" }));
  await screen.findByRole("heading", { name: "text-model" });
  expect(
    page.calls.find((call) => call.method === "providers.configure")?.params,
  ).toMatchObject({
    provider_id: "primary",
    tools: false,
    expected_revision: 0,
    context_limit: 64000,
    output_limit: 2048,
    credential_ref: null,
  });
  expect(
    screen.getByRole("button", { name: "Use model" }).hasAttribute("disabled"),
  ).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Output limit"), {
    target: { value: "1024" },
  });
  await userEvent.click(
    screen.getByRole("checkbox", {
      name: "Model supports canonical tool calls",
    }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Save provider" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.filter((call) => call.method === "providers.configure")[1]
      .params,
  ).toMatchObject({ tools: true, expected_revision: 1, output_limit: 1024 });
  await userEvent.click(screen.getByRole("button", { name: "Probe model" }));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "The model did not provide a valid stream",
      "error",
    ),
  );
  expect(
    screen.getByRole("button", { name: "Use model" }).hasAttribute("disabled"),
  ).toBe(true);
  probeFails = false;
  await userEvent.click(screen.getByRole("button", { name: "Probe model" }));
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Use model" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  await userEvent.click(screen.getByRole("button", { name: "Use model" }));
  await screen.findByRole("button", { name: "In use" });
});

it("credential rotation uses the current reference revision, clears the secret, and revocation fences it", async () => {
  const credential = {
    name: "model-key",
    revision: 3,
    reference: "opaque-old",
    active: true,
  };
  const page = settings({
    "providers.credentials.list": () => [credential],
    "providers.credentials.put": (params) => {
      expect(params).toEqual({
        name: "model-key",
        secret: "new-private-secret",
        expected_revision: 3,
      });
      credential.revision++;
      credential.reference = "opaque-current";
      return credential;
    },
    "providers.credentials.revoke": (params) => {
      expect(params).toEqual({ name: "model-key", expected_revision: 4 });
      credential.active = false;
      return credential;
    },
  });
  await userEvent.click(
    await screen.findByText(
      "Advanced: legacy model connections, credentials & usage",
    ),
  );
  await screen.findByRole("heading", { name: "model-key" });
  await userEvent.click(screen.getByRole("button", { name: "Rotate secret" }));
  const secret = screen.getByLabelText("Secret") as HTMLInputElement;
  await userEvent.type(secret, "new-private-secret");
  await userEvent.click(
    screen.getByRole("button", { name: "Store credential" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(secret.value).toBe("");
  expect(document.body.textContent).not.toContain("new-private-secret");
  expect(JSON.stringify({ ...sessionStorage })).not.toContain(
    "new-private-secret",
  );
  await screen.findByText("opaque-current");
  await userEvent.click(
    screen.getByRole("button", { name: "Revoke", exact: true }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Keep credential" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Revoke", exact: true }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Revoke credential" }),
  );
  await screen.findByText("Revoked");
  expect(
    page.calls.some((call) => call.method === "providers.credentials.revoke"),
  ).toBe(true);
});

it("tool policies and one-use approvals retain their exact bound version and event identity", async () => {
  const policy = {
    id: "workspace.write",
    revision: 6,
    mode: "ask",
    stale: true,
    tool: { description: "Write a scoped file" },
  };
  const requests = [
    { id: "approve-event", call: { tool: "write source" } },
    { id: "deny-event", call: { tool: "erase source" } },
  ];
  const page = settings({
    "permissions.list": () => [policy],
    "permissions.pending": () => requests,
    "permissions.set": (params) => {
      expect(params).toEqual({
        tool: "workspace.write",
        mode: "deny",
        expected_revision: 6,
      });
      policy.mode = "deny";
      return policy;
    },
    "permissions.approve": (params) => params,
    "permissions.deny": (params) => params,
  });
  await screen.findByText("Choose a mind for your agent");
  await tab("Permissions");
  expect(screen.getByText("Tool changed — review this policy")).toBeTruthy();
  screen.getByRole("combobox", { name: "Policy for workspace.write" }).focus();
  await userEvent.keyboard("{Enter}{End}{Enter}");
  await waitFor(() =>
    expect(page.calls.some((call) => call.method === "permissions.set")).toBe(
      true,
    ),
  );
  const approve = screen
    .getByRole("heading", { name: "write source" })
    .closest("article")!;
  await userEvent.click(
    within(approve).getByRole("button", { name: "Approve once" }),
  );
  const deny = screen
    .getByRole("heading", { name: "erase source" })
    .closest("article")!;
  await userEvent.click(
    within(deny).getByRole("button", { name: "Deny", exact: true }),
  );
  await waitFor(() =>
    expect(
      page.calls.find((call) => call.method === "permissions.deny")?.params,
    ).toEqual({ event_id: "deny-event" }),
  );
  expect(
    page.calls.find((call) => call.method === "permissions.approve")?.params,
  ).toEqual({ event_id: "approve-event" });
});

it("procedures and extensions expose real lifecycle operations with current revisions", async () => {
  const procedure = {
    id: "review",
    revision: 3,
    state: "available",
    manifest: {
      id: "review",
      version: "2",
      description: "Review evidence",
      triggers: ["source"],
      steps: ["Read", "Review"],
      required_tools: [],
    },
  };
  const plugin = { id: "formatter", revision: 5, status: "active" };
  const page = settings({
    "skills.list": () => [procedure],
    "plugins.list": () => [plugin],
    "skills.enable": (params) => {
      expect(params.expected_revision).toBe(procedure.revision);
      procedure.state = params.enabled ? "available" : "disabled";
      procedure.revision++;
      return procedure;
    },
    "skills.rollback": (params) => params,
    "plugins.disable": (params) => {
      expect(params.expected_revision).toBe(plugin.revision);
      plugin.status = "disabled";
      plugin.revision++;
      return plugin;
    },
    "plugins.activate": (params) => {
      expect(params.expected_revision).toBe(plugin.revision);
      plugin.status = "active";
      plugin.revision++;
      return plugin;
    },
    "plugins.rollback": (params) => params,
    "skills.install": (params) => params,
  });
  await screen.findByText("Choose a mind for your agent");
  await tab("Procedures");
  await userEvent.click(
    screen.getByRole("button", { name: "Disable procedure" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Enable procedure" }),
  );
  await screen.findByRole("button", { name: "Disable procedure" });
  await userEvent.click(
    screen.getByRole("button", { name: "Previous version" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Disable extension" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Activate extension" }),
  );
  await screen.findByRole("button", { name: "Disable extension" });
  await userEvent.click(screen.getByRole("button", { name: "Roll back" }));
  await userEvent.click(screen.getByRole("button", { name: "Add procedure" }));
  fireEvent.change(screen.getByLabelText("Procedure ID"), {
    target: { value: "read" },
  });
  fireEvent.change(screen.getByLabelText("Description"), {
    target: { value: "Read sources" },
  });
  fireEvent.change(screen.getByLabelText(/^Trigger words/), {
    target: { value: " source, evidence, " },
  });
  fireEvent.change(screen.getByLabelText(/^Procedure steps/), {
    target: { value: "Read source\n\nReview evidence" },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Install procedure" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.method === "skills.install")?.params,
  ).toEqual({
    manifest: {
      id: "read",
      version: "1",
      description: "Read sources",
      triggers: ["source", "evidence"],
      steps: ["Read source", "Review evidence"],
      required_tools: [],
    },
  });
  expect(
    page.calls.find((call) => call.method === "skills.rollback")?.params
      .expected_revision,
  ).toBe(5);
  expect(
    page.calls.find((call) => call.method === "plugins.rollback")?.params
      .expected_revision,
  ).toBe(7);
});

it("runtime actions report observed outcomes and rename/archive use owner-provided CAS revisions", async () => {
  let fail = true;
  const page = settings(
    {
      "POST /agents/agent/lifecycle/start": () => ({
        agent_id: "agent",
        desired: "running",
        observed: "running",
      }),
      "POST /agents/agent/lifecycle/suspend": () => {
        throw new Error("Work must drain first");
      },
      "POST /agents/agent/lifecycle/recover": () => ({
        agent_id: "agent",
        desired: "running",
        observed: "recovered",
      }),
      "diagnostics.get": () => ({
        native_memory_available: false,
        error: { code: "native_unavailable" },
      }),
      "PATCH /agents/agent": (params) => {
        expect(params.expected_revision).toBe(7);
        if (fail) throw new Error("Agent revision changed");
        return { ...agent, ...params };
      },
    },
    { mode: "hosted", accepting: false, memory: { available: false } },
  );
  await screen.findByText("Choose a mind for your agent");
  await tab("Agent & data");
  expect(screen.getByText("Hosted")).toBeTruthy();
  expect(screen.getByText("running")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Start runtime" }));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith("Runtime: running"),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Suspend runtime" }),
  );
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith("Work must drain first", "error"),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Recover runtime" }),
  );
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith("Runtime: recovered"),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Runtime diagnostics" }),
  );
  await screen.findByRole("dialog", { name: "Runtime diagnostics" });
  expect(screen.getByText(/native_unavailable/)).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Create another agent" }),
  );
  expect(page.onCreate).toHaveBeenCalledOnce();
  await userEvent.click(
    screen.getByRole("button", { name: "Rename", exact: true }),
  );
  fireEvent.change(screen.getByLabelText("Display name"), {
    target: { value: "Cedar updated" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Rename agent" }));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith("Agent revision changed", "error"),
  );
  expect(page.onChanged).not.toHaveBeenCalled();
  fail = false;
  await userEvent.click(screen.getByRole("button", { name: "Rename agent" }));
  await waitFor(() => expect(page.onChanged).toHaveBeenCalledOnce());
  await userEvent.click(screen.getByRole("button", { name: "Archive agent" }));
  await userEvent.click(screen.getByRole("button", { name: "Keep agent" }));
  await userEvent.click(screen.getByRole("button", { name: "Archive agent" }));
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Archive agent",
    }),
  );
  await waitFor(() => expect(page.onArchived).toHaveBeenCalledOnce());
  expect(
    page.calls.filter((call) => call.method === "PATCH /agents/agent").at(-1)
      ?.params,
  ).toEqual({ archived: true, expected_revision: 7 });
});

const fixture = () =>
  JSON.parse(
    execFileSync(
      ".venv/bin/python",
      ["frontend/tests/backup_fixture.py", "create"],
      { encoding: "utf8", timeout: 10000 },
    ),
  );
function archiveFile(bytes: Uint8Array, name = "Cedar.sturgis") {
  const file = new File([bytes], name, { type: "application/zip" });
  Object.defineProperty(file, "arrayBuffer", {
    value: async () => Uint8Array.from(bytes).buffer,
  });
  return file;
}
it("raw ZIP exports interoperate with the CLI and restore uploads the identical bytes and computed SHA-256", async () => {
  vi.stubGlobal("Blob", NodeBlob);
  const { backup, cli } = fixture();
  const original = Buffer.from(backup.archive_base64, "base64");
  expect(cli).toEqual({
    sha256: backup.sha256,
    bytes: original.length,
    mode: "0600",
  });
  let blob: Blob | null = null,
    name = "";
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn((value: Blob) => {
      blob = value;
      return "blob:backup";
    }),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    name = this.download;
  });
  const page = settings({
    "POST /agents/agent/backup": () => backup,
    "POST /agents/restore": (params) => {
      expect(params).toEqual({
        name: "Cedar research restored",
        archive_base64: backup.archive_base64,
        sha256: backup.sha256,
        request_id: expect.stringMatching(/^[A-Za-z0-9_-]{1,128}$/),
      });
      const valid = JSON.parse(
        execFileSync(
          ".venv/bin/python",
          ["frontend/tests/backup_fixture.py", "validate"],
          { input: JSON.stringify(params), encoding: "utf8", timeout: 10000 },
        ),
      );
      expect(valid.ok).toBe(true);
      expect(valid.files).toContain("workspace/notes.txt");
      return { id: "restored" };
    },
  });
  await screen.findByText("Choose a mind for your agent");
  await tab("Agent & data");
  await userEvent.click(screen.getByRole("button", { name: "Export backup" }));
  await waitFor(() => expect(blob).not.toBeNull());
  const bytes = Buffer.from(await blob!.arrayBuffer());
  expect(bytes).toEqual(original);
  expect(bytes.subarray(0, 4).toString("hex")).toBe("504b0304");
  expect(blob!.type).toBe("application/zip");
  expect(name).toBe("Cedar_research.sturgis");
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(backup.sha256);
  await userEvent.click(screen.getByRole("button", { name: "Restore backup" }));
  await userEvent.upload(
    screen.getByLabelText("Backup file"),
    archiveFile(bytes),
  );
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Restore backup",
    }),
  );
  await waitFor(() => expect(page.onChanged).toHaveBeenCalledOnce());
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("restore rejects missing or oversized files locally and displays a real invalid-ZIP backend error", async () => {
  const page = settings({
    "POST /agents/restore": (params) => {
      const result = JSON.parse(
        execFileSync(
          ".venv/bin/python",
          ["frontend/tests/backup_fixture.py", "validate"],
          { input: JSON.stringify(params), encoding: "utf8", timeout: 10000 },
        ),
      );
      expect(result.ok).toBe(false);
      expect(result.error.code).toBe("invalid_backup");
      throw new Error(result.error.message);
    },
  });
  await screen.findByText("Choose a mind for your agent");
  await tab("Agent & data");
  await userEvent.click(screen.getByRole("button", { name: "Restore backup" }));
  const submit = within(screen.getByRole("dialog")).getByRole("button", {
    name: "Restore backup",
  });
  await userEvent.click(submit);
  expect(page.notify).toHaveBeenCalledWith(
    "Choose a Sturgis backup file.",
    "error",
  );
  await userEvent.upload(
    screen.getByLabelText("Backup file"),
    archiveFile(new Uint8Array(4 * 1024 * 1024 + 1)),
  );
  await userEvent.click(submit);
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Choose a file up to 4 MiB.",
      "error",
    ),
  );
  expect(
    page.calls.some((call) => call.method === "POST /agents/restore"),
  ).toBe(false);
  await userEvent.upload(
    screen.getByLabelText("Backup file"),
    archiveFile(new TextEncoder().encode("not a ZIP archive")),
  );
  await userEvent.click(submit);
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Archive structure or compressed content is invalid",
      "error",
    ),
  );
  expect(page.onChanged).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toBeTruthy();
});

it.each([
  { format: "unknown-format" },
  { bytes: 0 },
  { bytes: 4194305 },
  { archive_base64: "ab!=" },
  { archive_base64: "a=b=" },
  { sha256: "invalid" },
  { sha256: "0".repeat(64) },
  { bytes: 5 },
])("corrupt backup metadata prevents a download (%j)", async (corruption) => {
  const bytes = Buffer.from("archive bytes");
  const create = vi.fn();
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: create,
  });
  const page = settings({
    "POST /agents/agent/backup": () => ({
      format: "sturgis-backup/1",
      archive_base64: bytes.toString("base64"),
      bytes: bytes.length,
      sha256: createHash("sha256").update(bytes).digest("hex"),
      ...corruption,
    }),
  });
  await screen.findByText("Choose a mind for your agent");
  await tab("Agent & data");
  await userEvent.click(screen.getByRole("button", { name: "Export backup" }));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Backup response failed its format, size or digest verification.",
      "error",
    ),
  );
  expect(create).not.toHaveBeenCalled();
});

async function openRestore(bytes: Uint8Array) {
  await screen.findByText("Choose a mind for your agent");
  await tab("Agent & data");
  await userEvent.click(screen.getByRole("button", { name: "Restore backup" }));
  await userEvent.upload(
    screen.getByLabelText("Backup file"),
    archiveFile(bytes),
  );
  return within(screen.getByRole("dialog")).getByRole("button", {
    name: "Restore backup",
  });
}
const pendingRestoreKey = "sturgis:pending-restore:owner";
it("an unknown restore acknowledgement survives remount and authentication rejection without duplicating the imported agent", async () => {
  const bytes = new TextEncoder().encode("private archive content");
  const sent: Record<string, unknown>[] = [];
  let phase = 0;
  const response = (params: Record<string, unknown>) => {
    sent.push(params);
    if (phase === 0) throw new TypeError("Acknowledgement lost");
    if (phase === 1)
      return new Response(
        JSON.stringify({
          version: "consumer/1",
          data: null,
          error: { code: "unauthenticated", message: "Sign in required" },
        }),
        { status: 401 },
      );
    return {
      id: "single-restored-agent",
      restore: { request_id: params.request_id },
    };
  };
  const first = settings({ "POST /agents/restore": response });
  await userEvent.click(await openRestore(bytes));
  await waitFor(() =>
    expect(first.notify).toHaveBeenCalledWith(
      expect.stringContaining("Restore may have completed"),
      "error",
    ),
  );
  const retained = JSON.parse(sessionStorage.getItem(pendingRestoreKey)!);
  expect(Object.keys(retained).sort()).toEqual(["digest", "request_id"]);
  expect(retained.digest).toMatch(/^[a-f0-9]{64}$/);
  expect(sessionStorage.getItem(pendingRestoreKey)).not.toContain(
    "private archive content",
  );
  expect(sessionStorage.getItem(pendingRestoreKey)).not.toContain("Cedar");
  first.unmount();
  first.client.clear();
  phase = 1;
  const second = settings({ "POST /agents/restore": response });
  const submit = await openRestore(bytes);
  await userEvent.click(submit);
  await waitFor(() => expect(sent).toHaveLength(2));
  await waitFor(() =>
    expect(second.notify).toHaveBeenCalledWith(
      expect.stringContaining("Restore may have completed"),
      "error",
    ),
  );
  expect(sent[1]).toEqual(sent[0]);
  expect(JSON.parse(sessionStorage.getItem(pendingRestoreKey)!)).toEqual(
    retained,
  );
  fireEvent.change(screen.getByLabelText("Restored agent name"), {
    target: { value: "Different agent" },
  });
  await userEvent.click(submit);
  await waitFor(() =>
    expect(second.notify).toHaveBeenCalledWith(
      expect.stringContaining("previous restore has an unknown outcome"),
      "error",
    ),
  );
  expect(sent).toHaveLength(2);
  fireEvent.change(screen.getByLabelText("Restored agent name"), {
    target: { value: "Cedar research restored" },
  });
  await userEvent.upload(
    screen.getByLabelText("Backup file"),
    archiveFile(new TextEncoder().encode("changed bytes")),
  );
  await userEvent.click(submit);
  expect(sent).toHaveLength(2);
  await userEvent.upload(
    screen.getByLabelText("Backup file"),
    archiveFile(bytes),
  );
  phase = 2;
  await userEvent.click(submit);
  await waitFor(() => expect(second.onChanged).toHaveBeenCalledOnce());
  expect(sent[2]).toEqual(sent[0]);
  expect(sessionStorage.getItem(pendingRestoreKey)).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Restore backup" }));
  await userEvent.upload(
    screen.getByLabelText("Backup file"),
    archiveFile(bytes),
  );
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Restore backup",
    }),
  );
  await waitFor(() => expect(second.onChanged).toHaveBeenCalledTimes(2));
  expect(sent[3].request_id).not.toBe(sent[0].request_id);
});

it("restore recovery blocks unreadable receipts or unavailable storage and releases a definitively interrupted attempt", async () => {
  let interrupted = true;
  const page = settings({
    "POST /agents/restore": (params) =>
      interrupted
        ? new Response(
            JSON.stringify({
              version: "consumer/1",
              data: null,
              error: {
                code: "restore_interrupted",
                message: "Restore interrupted before publication",
              },
            }),
            { status: 400 },
          )
        : { id: "restored", restore: { request_id: params.request_id } },
  });
  const submit = await openRestore(
    new TextEncoder().encode("bounded ZIP fixture"),
  );
  for (const raw of [
    "bad-json",
    JSON.stringify({ digest: "invalid", request_id: "invalid" }),
  ]) {
    sessionStorage.setItem(pendingRestoreKey, raw);
    await userEvent.click(submit);
    await waitFor(() =>
      expect(page.notify).toHaveBeenLastCalledWith(
        expect.stringContaining("Saved restore recovery cannot be read"),
        "error",
      ),
    );
  }
  expect(
    page.calls.filter((call) => call.method === "POST /agents/restore"),
  ).toHaveLength(0);
  sessionStorage.removeItem(pendingRestoreKey);
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("Full", "QuotaExceededError");
  });
  await userEvent.click(submit);
  await waitFor(() =>
    expect(page.notify).toHaveBeenLastCalledWith(
      "Restore recovery could not be saved. No restore was sent.",
      "error",
    ),
  );
  set.mockRestore();
  await userEvent.click(submit);
  await waitFor(() =>
    expect(page.notify).toHaveBeenLastCalledWith(
      "Restore interrupted before publication",
      "error",
    ),
  );
  expect(sessionStorage.getItem(pendingRestoreKey)).toBeNull();
  interrupted = false;
  await userEvent.click(submit);
  await waitFor(() => expect(page.onChanged).toHaveBeenCalledOnce());
  const calls = page.calls.filter(
    (call) => call.method === "POST /agents/restore",
  );
  expect(calls).toHaveLength(2);
  expect(calls[0].params.request_id).not.toBe(calls[1].params.request_id);
});
