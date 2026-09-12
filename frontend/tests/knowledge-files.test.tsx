import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Blob as NodeBlob } from "node:buffer";
import FilesPage from "../src/pages/FilesPage";
import KnowledgePage from "../src/pages/KnowledgePage";
import { mountPage } from "./harness";

it("file conflicts preserve the preimage and the editor until a checked save succeeds", async () => {
  vi.stubGlobal("Blob", NodeBlob);
  let fail = true;
  const before = "a".repeat(64),
    after = "b".repeat(64);
  const page = mountPage(<FilesPage />, {
    "files.list": () => ({
      path: ".",
      entries: [{ name: "notes.txt", directory: false, file: true, bytes: 4 }],
    }),
    "files.read": () => ({
      path: "notes.txt",
      data_base64: btoa("Cedar"),
      sha256: before,
      bytes: 5,
    }),
    "files.write": (params) => {
      expect(params.expected_sha256).toBe(before);
      if (fail) throw new Error("File changed since it was opened");
      return { after_sha256: after };
    },
    "files.delete": (params) => {
      expect(params.expected_sha256).toBe(after);
      return { deleted: true };
    },
  });
  await userEvent.click(
    await screen.findByRole("button", { name: "Open file notes.txt" }),
  );
  const editor = await screen.findByLabelText("File content");
  fireEvent.change(editor, { target: { value: "Reviewed source" } });
  await userEvent.click(screen.getByRole("button", { name: "Save file" }));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "File changed since it was opened",
      "error",
    ),
  );
  expect((editor as HTMLTextAreaElement).value).toBe("Reviewed source");
  expect(screen.getByText("SHA-256 · " + before)).toBeTruthy();
  fail = false;
  await userEvent.click(screen.getByRole("button", { name: "Save file" }));
  await screen.findByText("SHA-256 · " + after);
  expect(
    page.calls
      .filter((call) => call.method === "files.write")
      .map((call) => call.params.data_base64),
  ).toEqual([btoa("Reviewed source"), btoa("Reviewed source")]);
  await userEvent.click(screen.getByRole("button", { name: "Delete file" }));
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Delete file",
    }),
  );
  await waitFor(() =>
    expect(screen.queryByLabelText("File content")).toBeNull(),
  );
});
it("binary files cannot enter the text editor and nested navigation keeps scoped paths", async () => {
  const page = mountPage(<FilesPage />, {
    "files.list": (params) => ({
      path: params.path,
      entries:
        params.path === "."
          ? [{ name: "sources", directory: true, file: false, bytes: 0 }]
          : [{ name: "raw.bin", directory: false, file: true, bytes: 1 }],
    }),
    "files.read": () => ({
      path: "sources/raw.bin",
      data_base64: "/w==",
      sha256: "c".repeat(64),
      bytes: 1,
    }),
  });
  await userEvent.click(
    await screen.findByRole("button", { name: "Open folder sources" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Open file raw.bin" }),
  );
  await screen.findByText("Download to open this file");
  expect(screen.queryByLabelText("File content")).toBeNull();
  expect(screen.queryByRole("button", { name: "Save file" })).toBeNull();
  expect(
    page.calls.find((call) => call.method === "files.read")?.params,
  ).toEqual({ path: "sources/raw.bin" });
  await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
  await screen.findByRole("button", { name: "Open folder sources" });
  await userEvent.type(screen.getByLabelText("Filter files"), "absent");
  expect(
    screen.queryByRole("button", { name: "Open folder sources" }),
  ).toBeNull();
});
it("memory corrections and retractions honor backend outcomes and retain the correct revision", async () => {
  const record = {
    id: "memory",
    revision: 7,
    content: "Cedar was green",
    kind: "fact",
    concepts: ["Cedar"],
    sources: [],
  };
  let correct = false,
    retract = false;
  const page = mountPage(<KnowledgePage />, {
    "attachments.list": () => ({ items: [] }),
    "knowledge.list": () => ({ items: [] }),
    "memory.query": () => ({
      status: "ok",
      data: { records: [{ record, score: 1 }] },
    }),
    "memory.correct": (params) => {
      expect(params.expected_revision).toBe(7);
      return correct
        ? { status: "ok" }
        : { status: "error", error: { message: "Native memory unavailable" } };
    },
    "memory.retract": (params) => {
      expect(params.revision).toBe(7);
      return retract ? { status: "ok" } : { status: "error" };
    },
  });
  await screen.findByText("Good context starts here");
  await userEvent.click(screen.getByRole("tab", { name: "Agent memory" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Correct memory" }),
  );
  fireEvent.change(screen.getByLabelText("Corrected memory"), {
    target: { value: "Cedar is blue" },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Save correction" }),
  );
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Native memory unavailable",
      "error",
    ),
  );
  expect(screen.getByRole("dialog")).toBeTruthy();
  correct = true;
  await userEvent.click(
    screen.getByRole("button", { name: "Save correction" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.method === "memory.correct")?.params.content,
  ).toBe("Cedar is blue");
  await userEvent.click(screen.getByRole("button", { name: "Retract" }));
  await userEvent.type(screen.getByLabelText("Reason"), "Source withdrawn");
  await userEvent.click(screen.getByRole("button", { name: "Retract memory" }));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Memory retraction failed.",
      "error",
    ),
  );
  retract = true;
  await userEvent.click(screen.getByRole("button", { name: "Retract memory" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
it("unavailable native memory is visible and search retains the selected concept", async () => {
  const page = mountPage(<KnowledgePage />, {
    "attachments.list": () => ({ items: [] }),
    "knowledge.list": () => ({ items: [] }),
    "memory.query": () => ({ status: "unavailable" }),
  });
  await screen.findByText("Good context starts here");
  await userEvent.click(screen.getByRole("tab", { name: "Agent memory" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Native memory is not available.",
  );
  await userEvent.type(screen.getByLabelText("Search memory"), "Cedar");
  await userEvent.click(screen.getByRole("button", { name: "Search memory" }));
  await waitFor(() =>
    expect(
      page.calls.some(
        (call) =>
          call.method === "memory.query" && call.params.text === "Cedar",
      ),
    ).toBe(true),
  );
});
