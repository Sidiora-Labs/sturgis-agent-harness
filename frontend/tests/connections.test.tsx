import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import ConnectionsPage from "../src/pages/ConnectionsPage";
import { mountPage } from "./harness";
const connection = (patch: Record<string, unknown> = {}) => ({
  id: "source",
  kind: "http",
  endpoint: "https://example.com",
  revision: 3,
  status: "ungranted",
  active: true,
  grants: [],
  options: {},
  credential_ref: null,
  ...patch,
});
async function choose(label: string, option: string) {
  screen.getByRole("combobox", { name: label }).focus();
  await userEvent.keyboard("{Enter}");
  await screen.findByRole("option", { name: option, exact: true });
  const index = screen
    .getAllByRole("option")
    .findIndex((item) => item.textContent === option);
  await userEvent.keyboard("{Home}" + "{ArrowDown}".repeat(index) + "{Enter}");
}

it("configuration validates JSON and exact MCP tools while credentials remain opaque references", async () => {
  const page = mountPage(<ConnectionsPage />, {
    "connections.list": () => [],
    "providers.credentials.list": () => [
      { name: "App key", reference: "opaque-rev-2", active: true },
    ],
    "connections.configure": (params) => params,
  });
  await screen.findByText("Open a thoughtful connection");
  await userEvent.click(
    screen.getAllByRole("button", { name: "Add connection" })[0],
  );
  await userEvent.type(screen.getByLabelText("Connection ID"), "notes");
  fireEvent.change(screen.getByLabelText(/^Endpoint/), {
    target: { value: "https://notes.example/mcp" },
  });
  await choose("Connection type", "App server (MCP)");
  fireEvent.change(
    screen.getByLabelText("Allowed app tools", { exact: false }),
    { target: { value: ", " } },
  );
  fireEvent.change(screen.getByLabelText("Options (JSON)"), {
    target: { value: "[]" },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Configure connection" }),
  );
  expect(screen.getByRole("alert").textContent).toBe(
    "Advanced options must be a JSON object.",
  );
  fireEvent.change(screen.getByLabelText("Options (JSON)"), {
    target: { value: '{"allowed_origins":["https://notes.example"]}' },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Configure connection" }),
  );
  expect(screen.getByRole("alert").textContent).toBe(
    "Name at least one app tool this connection may call.",
  );
  fireEvent.change(
    screen.getByLabelText("Allowed app tools", { exact: false }),
    { target: { value: "search_notes, read_document" } },
  );
  await choose("Credential", "App key");
  await userEvent.click(
    screen.getByRole("button", { name: "Configure connection" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.method === "connections.configure")?.params,
  ).toEqual({
    id: "notes",
    kind: "mcp",
    endpoint: "https://notes.example/mcp",
    credential_ref: "opaque-rev-2",
    expected_revision: 0,
    options: {
      allowed_origins: ["https://notes.example"],
      allowed_tools: ["search_notes", "read_document"],
    },
  });
});
it("grants use current revisions and a failed browser readiness result never becomes Ready", async () => {
  const row = connection({ kind: "browser" });
  const page = mountPage(<ConnectionsPage />, {
    "connections.list": () => [row],
    "providers.credentials.list": () => [],
    "connections.grant": (params) => {
      expect(params).toEqual({
        id: "source",
        operations: ["inspect"],
        expected_revision: 3,
      });
      row.grants = ["inspect"];
      row.revision = 4;
      return row;
    },
    "connections.probe": (params) => {
      expect(params.expected_revision).toBe(4);
      row.status = "unavailable";
      return { ...row, probe: { error: { code: "browser_unavailable" } } };
    },
    "connections.revoke": (params) => {
      expect(params.expected_revision).toBe(4);
      row.status = "revoked";
      row.active = false;
      return row;
    },
  });
  await screen.findByRole("heading", { name: "source" });
  expect(
    screen
      .getByRole("button", { name: "Test connection" })
      .hasAttribute("disabled"),
  ).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Manage grants" }));
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "Save grants" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await userEvent.click(
    screen.getByRole("button", { name: "Test connection" }),
  );
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "A managed browser is not available in this runtime.",
      "error",
    ),
  );
  await userEvent.click(
    screen.getByRole("tab", { name: "Ready", exact: true }),
  );
  await screen.findByText("No matching connections");
  await userEvent.click(screen.getByRole("tab", { name: "Needs attention" }));
  await screen.findByRole("heading", { name: "source" });
  await userEvent.click(
    screen.getByRole("button", { name: "Revoke connection" }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Revoke connection" }),
    ).toBeNull(),
  );
});
it("paired device proofs are transient and clipboard failures are reported without persisting the proof", async () => {
  const row = connection({
    kind: "device",
    probe: { ok: true },
    options: {
      device_id: "desk",
      operations: { read_status: { type: "object" } },
    },
  });
  const page = mountPage(<ConnectionsPage />, {
    "connections.list": () => [row],
    "providers.credentials.list": () => [],
    "devices.pair": () => ({
      connection_id: "source",
      pairing_token: "one-time-proof",
      expires_at: Date.now() / 1000 + 300,
      revision: 3,
    }),
  });
  await screen.findByRole("heading", { name: "source" });
  await userEvent.click(screen.getByRole("button", { name: "Pair device" }));
  const dialog = screen.getByRole("dialog", { name: "Pair your device" });
  expect(within(dialog).getByText("one-time-proof")).toBeTruthy();
  const copy = vi
    .fn()
    .mockRejectedValueOnce(new Error("Denied"))
    .mockResolvedValueOnce(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: copy },
  });
  await userEvent.click(screen.getByRole("button", { name: "Copy token" }));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      expect.stringContaining("Copy is unavailable"),
      "error",
    ),
  );
  await userEvent.click(screen.getByRole("button", { name: "Copy token" }));
  await screen.findByRole("button", { name: "Copied" });
  expect(copy).toHaveBeenLastCalledWith("one-time-proof");
  await userEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(screen.queryByText("one-time-proof")).toBeNull();
  expect(JSON.stringify({ ...localStorage })).not.toContain("one-time-proof");
  await userEvent.click(screen.getByRole("button", { name: "Pair device" }));
  await screen.findByText("one-time-proof");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByText("one-time-proof")).toBeNull();
});

it("media edits preserve credential revisions and explicitly add or remove model and voice options", async () => {
  const row = connection({
    kind: "tts",
    status: "stale_credential",
    credential_ref: "old-reference",
    options: { allowed_origins: ["https://example.com"] },
  });
  const page = mountPage(<ConnectionsPage />, {
    "connections.list": () => [row],
    "providers.credentials.list": () => [],
    "connections.configure": (params) => {
      expect(params.expected_revision).toBe(row.revision);
      Object.assign(row, params, { revision: row.revision + 1 });
      return row;
    },
  });
  await screen.findByRole("heading", { name: "source" });
  await userEvent.click(
    screen.getByRole("button", { name: "Edit connection source" }),
  );
  expect(
    screen.getByRole("combobox", { name: "Credential" }).textContent,
  ).toContain("Previous credential");
  fireEvent.change(screen.getByLabelText("Model (optional)"), {
    target: { value: " voice-model " },
  });
  fireEvent.change(screen.getByLabelText("Voice (optional)"), {
    target: { value: " calm " },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Save connection" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.method === "connections.configure")?.params,
  ).toMatchObject({
    expected_revision: 3,
    credential_ref: "old-reference",
    options: {
      model: "voice-model",
      voice: "calm",
      allowed_origins: ["https://example.com"],
    },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Edit connection source" }),
  );
  expect(
    (screen.getByLabelText("Model (optional)") as HTMLInputElement).value,
  ).toBe("voice-model");
  await userEvent.clear(screen.getByLabelText("Model (optional)"));
  await userEvent.clear(screen.getByLabelText("Voice (optional)"));
  await choose("Credential", "No credential");
  await userEvent.click(
    screen.getByRole("button", { name: "Save connection" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  const last = page.calls
    .filter((call) => call.method === "connections.configure")
    .at(-1)!;
  expect(last.params).toMatchObject({
    expected_revision: 4,
    credential_ref: null,
  });
  expect(last.params.options).toEqual({
    allowed_origins: ["https://example.com"],
  });
});

it("device configuration validates operation names and preserves only the declared per-operation schemas", async () => {
  const rows: ReturnType<typeof connection>[] = [];
  const page = mountPage(<ConnectionsPage />, {
    "connections.list": () => rows,
    "providers.credentials.list": () => [],
    "connections.configure": (params) => {
      const row = connection({ ...params, revision: 1 });
      rows.push(row);
      return row;
    },
  });
  await screen.findByText("Open a thoughtful connection");
  await userEvent.click(
    screen.getAllByRole("button", { name: "Add connection" }).at(-1)!,
  );
  await userEvent.type(screen.getByLabelText("Connection ID"), "desk");
  fireEvent.change(screen.getByLabelText(/^Endpoint/), {
    target: { value: "https://device.example/client" },
  });
  await choose("Connection type", "Paired device");
  await userEvent.type(screen.getByLabelText("Device identity"), "desk-device");
  fireEvent.change(screen.getByLabelText(/^Allowed device operations/), {
    target: { value: ", " },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Configure connection" }),
  );
  expect(screen.getByRole("alert").textContent).toBe(
    "Name at least one operation your device supports.",
  );
  expect(
    page.calls.some((call) => call.method === "connections.configure"),
  ).toBe(false);
  fireEvent.change(screen.getByLabelText(/^Allowed device operations/), {
    target: { value: "read_status, set_light" },
  });
  const readSchema = {
    type: "object",
    properties: { detail: { type: "boolean" } },
    additionalProperties: false,
  };
  fireEvent.change(screen.getByLabelText("Options (JSON)"), {
    target: {
      value: JSON.stringify({
        operations: { read_status: readSchema, undeclared: { type: "object" } },
      }),
    },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Configure connection" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.method === "connections.configure")?.params,
  ).toMatchObject({
    id: "desk",
    kind: "device",
    expected_revision: 0,
    options: {
      device_id: "desk-device",
      operations: {
        read_status: readSchema,
        set_light: { type: "object", additionalProperties: false },
      },
    },
  });
  expect(
    Object.keys(
      (
        page.calls.find((call) => call.method === "connections.configure")!
          .params.options as { operations: object }
      ).operations,
    ),
  ).toEqual(["read_status", "set_light"]);
  await userEvent.click(
    screen.getByRole("button", { name: "Edit connection desk" }),
  );
  expect(
    (screen.getByLabelText(/^Allowed device operations/) as HTMLInputElement)
      .value,
  ).toBe("read_status, set_light");
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(
    page.calls.filter((call) => call.method === "connections.configure"),
  ).toHaveLength(1);
});

it("retry, local search and explicit grant removal retain observed readiness and CAS", async () => {
  let failed = true;
  const row = connection({ status: "ready", grants: ["fetch"] });
  const page = mountPage(<ConnectionsPage />, {
    "connections.list": () => {
      if (failed) throw new Error("Connection observation unavailable");
      return [row];
    },
    "providers.credentials.list": () => [],
    "connections.probe": (params) => {
      expect(params).toEqual({ id: "source", expected_revision: 3 });
      return row;
    },
    "connections.revoke": (params) => {
      expect(params).toEqual({ id: "source", expected_revision: 3 });
      row.grants = [];
      row.active = false;
      row.status = "revoked";
      row.revision++;
      return row;
    },
  });
  await screen.findByText("Connections could not be loaded");
  failed = false;
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByRole("heading", { name: "source" });
  await userEvent.type(screen.getByLabelText("Search connections"), "missing");
  await screen.findByText("No matching connections");
  await userEvent.clear(screen.getByLabelText("Search connections"));
  await userEvent.click(
    screen.getByRole("button", { name: "Test connection" }),
  );
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith("source is ready.", "success"),
  );
  await userEvent.click(screen.getByRole("button", { name: "Manage grants" }));
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Manage grants" }));
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(page.calls.some((call) => call.method === "connections.revoke")).toBe(
    false,
  );
  await userEvent.click(screen.getByRole("button", { name: "Manage grants" }));
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "Save grants" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await screen.findByText("Revoked");
  expect(
    screen
      .getByRole("button", { name: "Test connection" })
      .hasAttribute("disabled"),
  ).toBe(true);
});
