import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Blob as NodeBlob } from "node:buffer";
import { createHash } from "node:crypto";
import { expect, it, vi } from "vitest";
import FilesPage from "../src/pages/FilesPage";
import { mountPage } from "./harness";

const digest = (text: string) => createHash("sha256").update(text).digest("hex");

it("creates, saves, reopens and downloads the checked bytes after list and create failures", async () => {
  vi.stubGlobal("Blob", NodeBlob);
  let listFailed = false;
  let createFails = true;
  const files = new Map<string, string>();
  const page = mountPage(<FilesPage />, {
    "files.list": () => {
      if (!listFailed) { listFailed = true; throw new Error("Workspace listing interrupted"); }
      return { path: ".", entries: [...files].map(([name, content]) => ({ name, directory: false, file: true, bytes: Buffer.byteLength(content) })) };
    },
    "files.write": (params) => {
      const path = String(params.path);
      const previous = files.get(path);
      expect(params.expected_sha256).toBe(previous === undefined ? undefined : digest(previous));
      if (createFails) throw new Error("Creation was rejected before writing");
      const content = Buffer.from(String(params.data_base64), "base64").toString("utf8");
      files.set(path, content);
      return { after_sha256: digest(content) };
    },
    "files.read": (params) => {
      const content = files.get(String(params.path))!;
      return { path: params.path, sha256: digest(content), data_base64: Buffer.from(content).toString("base64"), bytes: Buffer.byteLength(content) };
    },
  });
  await screen.findByText("Workspace listing interrupted");
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  await userEvent.click(await screen.findByRole("button", { name: "Create your first file" }));
  fireEvent.change(screen.getByLabelText("Workspace path"), { target: { value: "plan.txt" } });
  fireEvent.change(screen.getByLabelText("Initial content"), { target: { value: "Plan α" } });
  await userEvent.click(screen.getByRole("button", { name: "Create file" }));
  await waitFor(() => expect(page.notify).toHaveBeenCalledWith("Creation was rejected before writing", "error"));
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(files.size).toBe(0);
  createFails = false;
  await userEvent.click(screen.getByRole("button", { name: "Create file" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByLabelText("File content")).toHaveProperty("value", "Plan α");
  expect(screen.getByText("SHA-256 · " + digest("Plan α"))).toBeTruthy();
  fireEvent.change(screen.getByLabelText("File content"), { target: { value: "Reviewed β\nExact final bytes" } });
  await userEvent.click(screen.getByRole("button", { name: "Save file" }));
  await screen.findByText("SHA-256 · " + digest("Reviewed β\nExact final bytes"));
  await userEvent.click(await screen.findByRole("button", { name: "Open file plan.txt" }));
  await waitFor(() => expect(page.calls.filter(call => call.method === "files.read")).toHaveLength(1));
  expect(screen.getByLabelText("File content")).toHaveProperty("value", files.get("plan.txt"));
  let downloaded: NodeBlob | undefined;
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn((body: NodeBlob) => { downloaded = body; return "blob:checked-file"; }) });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    expect(this.download).toBe("plan.txt");
    expect(this.getAttribute("href")).toBe("blob:checked-file");
  });
  await userEvent.click(screen.getByRole("button", { name: "Download" }));
  expect(click).toHaveBeenCalledOnce();
  expect(Buffer.from(await downloaded!.arrayBuffer()).toString("utf8")).toBe(files.get("plan.txt"));
  await userEvent.click(screen.getByRole("button", { name: "Delete file" }));
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Keep file" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(page.calls.some(call => call.method === "files.delete")).toBe(false);
  expect(page.calls.filter(call => call.method === "files.write").map(call => call.params.path)).toEqual(["plan.txt", "plan.txt", "plan.txt"]);
  expect(page.fetcher.mock.calls.every(([url]) => url.startsWith("/api/v1/agents/agent"))).toBe(true);
});

it("rejects oversized initial text before HTTP and lets the owner replace it and create", async () => {
  vi.stubGlobal("Blob", NodeBlob);
  const page = mountPage(<FilesPage />, {
    "files.list": () => ({ path: ".", entries: [] }),
    "files.write": (params) => ({ after_sha256: digest(Buffer.from(String(params.data_base64), "base64").toString()) }),
  });
  await screen.findByText("A clean workspace");
  await userEvent.click(screen.getByRole("button", { name: "New file" }));
  fireEvent.change(screen.getByLabelText("Initial content"), { target: { value: "x".repeat(4 * 1024 * 1024 + 1) } });
  await userEvent.click(screen.getByRole("button", { name: "Create file" }));
  await waitFor(() => expect(page.notify).toHaveBeenCalledWith("Choose a file up to 4 MiB.", "error"));
  expect(page.calls.some(call => call.method === "files.write")).toBe(false);
  expect(screen.getByRole("dialog")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Initial content"), { target: { value: "Small valid note" } });
  await userEvent.click(screen.getByRole("button", { name: "Create file" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByLabelText("File content")).toHaveProperty("value", "Small valid note");
  expect(screen.getByText("SHA-256 · " + digest("Small valid note"))).toBeTruthy();
});
