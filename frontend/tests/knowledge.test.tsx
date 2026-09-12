import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { File as NodeFile } from "node:buffer";
import { createHash } from "node:crypto";
import { expect, it } from "vitest";
import KnowledgePage from "../src/pages/KnowledgePage";
import { mountPage } from "./harness";

it("uploads exact source bytes, indexes and reads them, then removes and re-indexes after failures", async () => {
  const raw = "<p>Observatory α</p><script>untrusted()</script>";
  const source = { id: "source", name: "source.html", media_type: "text/html", bytes: Buffer.byteLength(raw),
    sha256: createHash("sha256").update(raw).digest("hex"), artifact_id: "artifact_source", extraction: { status: "ready" } };
  const document = { id: "document", title: "source.html", revision: 1, text: "Observatory α", attachment_id: "source", memory_ids: ["memory"] };
  let listed = false, uploaded = false, indexed = false, uploadFails = true, readFails = true, removeFails = true;
  const page = mountPage(<KnowledgePage />, {
    "attachments.list": () => {
      if (!listed) { listed = true; throw new Error("Source listing interrupted"); }
      return { items: uploaded ? [source] : [] };
    },
    "knowledge.list": () => ({ items: indexed ? [document] : [] }),
    "attachments.upload": (params) => {
      expect(params.name).toBe(source.name);
      expect(params.media_type).toBe("text/html");
      expect(Buffer.from(String(params.data_base64), "base64").toString()).toBe(raw);
      expect(typeof params.operation_id).toBe("string");
      if (uploadFails) throw new Error("Upload rejected before admission");
      uploaded = true; return source;
    },
    "knowledge.add": (params) => { expect(params.attachment_id).toBe(source.id); indexed = true; return { document }; },
    "knowledge.get": () => { if (readFails) throw new Error("Source read unavailable"); return document; },
    "knowledge.delete": (params) => {
      expect(params).toMatchObject({ id: "document", expected_revision: 1 });
      if (removeFails) throw new Error("Knowledge changed before removal");
      indexed = false; return { deleted: true };
    },
    "GET /agents/agent/attachments/source": () => new Response(raw, { headers: { "Content-Type": "application/octet-stream" } }),
  });
  await screen.findByText("Source listing interrupted");
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  await userEvent.click(await screen.findByRole("button", { name: "Upload your first source" }));
  const zone = screen.getByLabelText("Source file").closest(".upload-zone")!;
  expect(fireEvent.dragOver(zone)).toBe(false);
  const file = new NodeFile([raw], source.name, { type: source.media_type });
  fireEvent.drop(zone, { dataTransfer: { files: [file] } });
  expect(within(screen.getByRole("dialog")).getByText(source.name)).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Upload source" }));
  await waitFor(() => expect(page.notify).toHaveBeenCalledWith("Upload rejected before admission", "error"));
  expect(screen.getByRole("dialog")).toBeTruthy();
  uploadFails = false;
  await userEvent.click(screen.getByRole("button", { name: "Upload source" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  const download = await screen.findByRole("link", { name: "Download" });
  expect(download.getAttribute("href")).toBe("/api/v1/agents/agent/attachments/source");
  expect(download.getAttribute("download")).toBe(source.name);
  const bytes = await fetch(download.getAttribute("href")!, { method: "GET" });
  expect(createHash("sha256").update(await bytes.text()).digest("hex")).toBe(source.sha256);
  await userEvent.click(screen.getByRole("button", { name: "Add to knowledge" }));
  await userEvent.click(await screen.findByRole("button", { name: "Read source" }));
  await waitFor(() => expect(page.notify).toHaveBeenCalledWith("Source read unavailable", "error"));
  expect(screen.queryByRole("dialog")).toBeNull();
  readFails = false;
  await userEvent.click(screen.getByRole("button", { name: "Read source" }));
  expect(await screen.findByText(document.text)).toBeTruthy();
  expect(screen.getByRole("dialog").querySelector("script")).toBeNull();
  await userEvent.keyboard("{Escape}");
  await userEvent.click(screen.getByRole("button", { name: "Remove source.html from knowledge" }));
  await userEvent.keyboard("{Escape}");
  await userEvent.click(screen.getByRole("button", { name: "Remove source.html from knowledge" }));
  await userEvent.click(screen.getByRole("button", { name: "Keep knowledge" }));
  expect(page.calls.some(call => call.method === "knowledge.delete")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Remove source.html from knowledge" }));
  await userEvent.click(screen.getByRole("button", { name: "Remove from knowledge" }));
  await waitFor(() => expect(page.notify).toHaveBeenCalledWith("Knowledge changed before removal", "error"));
  expect(screen.getByRole("dialog")).toBeTruthy();
  removeFails = false;
  await userEvent.click(screen.getByRole("button", { name: "Remove from knowledge" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await screen.findByText("A library your agent can use");
  expect(screen.getByRole("link", { name: "Download" }).getAttribute("href")).toBe(download.getAttribute("href"));
  await userEvent.click(screen.getByRole("button", { name: "Add to knowledge" }));
  await screen.findByRole("button", { name: "Read source" });
  expect(page.calls.filter(call => call.method === "knowledge.add")).toHaveLength(2);
});

it("rejects an oversized source before upload and preserves owner cancellation", async () => {
  const page = mountPage(<KnowledgePage />, {
    "attachments.list": () => ({ items: [] }), "knowledge.list": () => ({ items: [] }),
  });
  await screen.findByText("Good context starts here");
  await userEvent.click(screen.getByRole("button", { name: "Add a source" }));
  const file = new NodeFile([new Uint8Array(4 * 1024 * 1024 + 1)], "too-large.txt", { type: "text/plain" });
  fireEvent.change(screen.getByLabelText("Source file"), { target: { files: [file] } });
  await userEvent.click(screen.getByRole("button", { name: "Upload source" }));
  await waitFor(() => expect(page.notify).toHaveBeenCalledWith("Choose a file up to 4 MiB.", "error"));
  expect(page.calls.some(call => call.method === "attachments.upload")).toBe(false);
  fireEvent.drop(screen.getByLabelText("Source file").closest(".upload-zone")!, { dataTransfer: { files: [] } });
  expect(screen.getByRole("button", { name: "Upload source" })).toHaveProperty("disabled", true);
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("retries memory queries and applies correction and retraction to the observed new revision", async () => {
  let failQuery = true, failCorrection = true, failRetraction = true;
  let record: { id: string; revision: number; content: string; kind: string; concepts: string[]; sources: string[] } | null =
    { id: "memory", revision: 3, content: "Old source claim", kind: "fact", concepts: ["observatory"], sources: ["source"] };
  const page = mountPage(<KnowledgePage />, {
    "attachments.list": () => ({ items: [] }), "knowledge.list": () => ({ items: [] }),
    "memory.query": () => { if (failQuery) throw new Error("Memory query interrupted"); return { status: "ok", data: { records: record ? [{ record }] : [] } }; },
    "memory.correct": (params) => {
      expect(params.expected_revision).toBe(3);
      if (failCorrection) return { status: "error" };
      record = { ...record!, revision: 4, content: String(params.content) }; return { status: "ok" };
    },
    "memory.retract": (params) => {
      expect(params).toMatchObject({ id: "memory", revision: 4, reason: "Withdrawn original source" });
      if (failRetraction) throw new Error("Retraction was not admitted");
      record = null; return { status: "ok" };
    },
  });
  await screen.findByText("Good context starts here");
  await userEvent.click(screen.getByRole("tab", { name: "Agent memory" }));
  await screen.findByText("Memory query interrupted");
  failQuery = false;
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  await userEvent.click(await screen.findByRole("button", { name: "Correct memory" }));
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(page.calls.some(call => call.method === "memory.correct")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Correct memory" }));
  fireEvent.change(screen.getByLabelText("Corrected memory"), { target: { value: "Corrected source claim" } });
  await userEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await waitFor(() => expect(page.notify).toHaveBeenCalledWith("Memory correction failed.", "error"));
  expect(screen.getByRole("dialog")).toBeTruthy();
  failCorrection = false;
  await userEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await screen.findByText("Revision 4");
  expect(screen.getByText("Corrected source claim")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Retract" }));
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(page.calls.some(call => call.method === "memory.retract")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Retract" }));
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Withdrawn original source" } });
  await userEvent.click(screen.getByRole("button", { name: "Retract memory" }));
  await waitFor(() => expect(page.notify).toHaveBeenCalledWith("Retraction was not admitted", "error"));
  failRetraction = false;
  await userEvent.click(screen.getByRole("button", { name: "Retract memory" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await screen.findByText("Room to remember");
  expect(screen.queryByText("Corrected source claim")).toBeNull();
});
