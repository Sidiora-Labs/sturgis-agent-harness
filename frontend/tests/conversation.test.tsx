import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentContext } from "../src/hooks/useAgent";
import ConversationPage from "../src/pages/ConversationPage";
const envelope = (data: unknown, error: unknown = null, status = 200) =>
  new Response(JSON.stringify({ version: "consumer/1", data, error }), {
    status,
  });
afterEach(() => localStorage.clear());
function mount({
  events = [],
  failSend = false,
  failCancel = false,
  ready = true,
  suspended = false,
  failResume = false,
  handlers = {},
}: {
  events?: unknown[];
  failSend?: boolean;
  failCancel?: boolean;
  ready?: boolean;
  suspended?: boolean;
  failResume?: boolean;
  handlers?: Record<
    string,
    (params: Record<string, unknown>, body: Record<string, unknown>) => unknown
  >;
} = {}) {
  const notify = vi.fn(),
    onConfigure = vi.fn(),
    onConnection = vi.fn(),
    onSelect = vi.fn();
  const sent: Record<string, unknown>[] = [];
  let polled = false,
    failed = false;
  let selectConversation: (id: string) => void = () => {};
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/agents/agent"))
      return envelope({
        runtime: {
          agent_id: "agent",
          desired: suspended ? "suspended" : "running",
          observed: suspended ? "suspended" : "running",
        },
      });
    if (url.endsWith("/lifecycle/start")) {
      if (failResume)
        return envelope(
          null,
          { code: "conflict", message: "Runtime cannot resume" },
          409,
        );
      suspended = false;
      return envelope({
        agent_id: "agent",
        desired: "running",
        observed: "running",
      });
    }
    if (url.includes("/events?")) {
      if (handlers.events) {
        const params = Object.fromEntries(
          new URL(url, "http://consumer.test").searchParams,
        );
        const result = await handlers.events(params, {});
        return result instanceof Response ? result : envelope(result);
      }
      if (!polled) {
        polled = true;
        return envelope({
          items: events,
          cursor: events.length,
          has_more: false,
        });
      }
      return new Promise<Response>((_resolve, reject) =>
        options.signal?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        ),
      );
    }
    const method = url.split("/").at(-1),
      body = JSON.parse(String(options.body));
    if (handlers[method!]) {
      const result = await handlers[method!](body.params, body);
      return result instanceof Response ? result : envelope(result);
    }
    if (method === "conversations.list")
      return envelope({
        items: [
          {
            id: "room",
            title: "Research thread",
            revision: 1,
            archived: false,
          },
        ],
      });
    if (method === "overview")
      return envelope({
        provider_settings: ready
          ? [{ active: true, model: "local model" }]
          : [],
      });
    if (method === "messages.send") {
      sent.push(body);
      if (failSend && !failed) {
        failed = true;
        throw new TypeError("Connection lost before acknowledgement");
      }
      return envelope({ id: "receipt", status: "admitted" });
    }
    if (method === "messages.cancel" && failCancel)
      return envelope(
        null,
        { code: "conflict", message: "Cancellation rejected" },
        409,
      );
    return envelope({});
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  function Page() {
    const [selected, setSelected] = useState<string | null>(null);
    selectConversation = setSelected;
    return (
      <ConversationPage
        selectedConversation={selected}
        onSelect={(id) => {
          onSelect(id);
          setSelected(id);
        }}
        onConnection={onConnection}
        onConfigure={onConfigure}
      />
    );
  }
  const rendered = render(
    <QueryClientProvider client={client}>
      <AgentContext.Provider
        value={{ agentId: "agent", accountId: "owner", notify }}
      >
        <Page />
      </AgentContext.Provider>
    </QueryClientProvider>,
  );
  return {
    notify,
    onConfigure,
    onConnection,
    onSelect,
    sent,
    fetcher,
    client,
    selectConversation: (id: string) => selectConversation(id),
    ...rendered,
  };
}
it("opening a listed saved chat stays loading through empty replay pages, renders saved messages and reloads them after remount", async () => {
  const rooms = [
    { id: "room", title: "Current chat", revision: 1, archived: false },
    { id: "older", title: "Previous chat", revision: 1, archived: false },
  ];
  const saved = [
    {
      id: "old-user",
      kind: "user.prompt",
      cursor: 501,
      payload: { text: "My previous question" },
    },
    {
      id: "old-answer",
      kind: "output.persisted",
      cursor: 502,
      payload: {
        stream_id: "old-stream",
        content: { type: "text", text: "The saved answer" },
      },
    },
  ];
  let firstPage: (value: unknown) => void = () => {};
  let lastPage: (value: unknown) => void = () => {};
  let nextPageReads = 0,
    olderStarts = 0;
  const handlers = {
    "conversations.list": () => ({ items: rooms }),
    events: (params: Record<string, unknown>) => {
      if (params.conversation_id === "room" && params.after === "0")
        return {
          items: [
            {
              id: "current",
              kind: "user.prompt",
              cursor: 1,
              payload: { text: "Current room message" },
            },
          ],
          cursor: 1,
          has_more: false,
        };
      if (params.conversation_id === "older" && params.after === "0") {
        olderStarts += 1;
        if (olderStarts > 1)
          return { items: saved, cursor: 502, has_more: false };
        return new Promise((resolve) => {
          firstPage = resolve;
        });
      }
      if (params.conversation_id === "older" && params.after === "500") {
        nextPageReads += 1;
        return new Promise((resolve) => {
          lastPage = resolve;
        });
      }
      return new Promise(() => {});
    },
  };
  const page = mount({ handlers });
  await screen.findByText("Current room message");
  await act(async () => page.selectConversation("older"));
  expect(screen.getByText("Previous chat")).toBeTruthy();
  expect(
    screen.getByRole("status", { name: "Opening conversation" }),
  ).toBeTruthy();
  expect(screen.queryByText("Current room message")).toBeNull();
  expect(screen.queryByText("A little space to think")).toBeNull();
  await act(async () => firstPage({ items: [], cursor: 500, has_more: true }));
  await waitFor(() => expect(nextPageReads).toBe(1));
  expect(
    screen.getByRole("status", { name: "Opening conversation" }),
  ).toBeTruthy();
  expect(screen.queryByText("A little space to think")).toBeNull();
  await act(async () =>
    lastPage({ items: saved, cursor: 502, has_more: false }),
  );
  await screen.findByText("The saved answer");
  expect(screen.getByText("My previous question")).toBeTruthy();
  expect(
    screen.queryByRole("status", { name: "Opening conversation" }),
  ).toBeNull();
  page.unmount();
  const reopened = mount({ handlers });
  await screen.findByText("Current room message");
  await act(async () => reopened.selectConversation("older"));
  await screen.findByText("The saved answer");
  expect(screen.getByText("My previous question")).toBeTruthy();
  expect(olderStarts).toBe(2);
  expect(
    reopened.fetcher.mock.calls.some(([url]) =>
      url.includes("conversation_id=older&after=0"),
    ),
  ).toBe(true);
});

it("reconnect errors retain loaded history and temporary runtime fences pause updates without clearing the chat", async () => {
  let updates = 0;
  const saved = {
    id: "saved",
    kind: "user.prompt",
    cursor: 10,
    payload: { text: "Keep my saved message visible" },
  };
  const page = mount({
    handlers: {
      events: (params) => {
        if (params.after === "0")
          return { items: [saved], cursor: 10, has_more: false };
        updates += 1;
        if (updates === 1)
          return envelope(
            null,
            {
              code: "worker_unavailable",
              message: "Conversation service is temporarily unavailable.",
            },
            503,
          );
        if (updates === 2)
          return { items: [saved], cursor: 10, has_more: false };
        return new Promise(() => {});
      },
    },
  });
  await screen.findByText("Keep my saved message visible");
  const error = await screen.findByRole("alert");
  expect(error.textContent).toContain(
    "Conversation service is temporarily unavailable.",
  );
  expect(error.textContent).toContain(
    "Showing loaded messages while reconnecting.",
  );
  expect(screen.getByText("Keep my saved message visible")).toBeTruthy();
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull(), {
    timeout: 2500,
  });
  expect(screen.getAllByText("Keep my saved message visible")).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("Message"), {
    target: { value: "Preserve this draft while paused" },
  });
  expect(screen.getByRole("button", { name: "Send message" })).toHaveProperty(
    "disabled",
    false,
  );
  await act(async () => {
    page.client.setQueryData(["runtime", "owner", "agent"], {
      agent_id: "agent",
      desired: "running",
      observed: "running",
      private_queries_paused: true,
    });
  });
  expect(screen.getByText("Keep my saved message visible")).toBeTruthy();
  expect(
    await screen.findByText(
      "Conversation updates are paused. Your loaded messages and draft stay here.",
    ),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Send message" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(screen.queryByText("A little space to think")).toBeNull();
});

it("ambiguous send failures retain the message identity across retries and prevent accidental changed-message reuse", async () => {
  const page = mount({ failSend: true });
  await screen.findByText("Research thread");
  const user = userEvent.setup();
  const input = screen.getByRole("textbox", { name: "Message" });
  await user.type(input, "Preserve this exact request");
  await user.click(screen.getByRole("button", { name: "Send message" }));
  await screen.findByRole("button", { name: "Retry message" });
  expect(page.notify).toHaveBeenCalledWith(
    "Connection lost before acknowledgement",
    "error",
  );
  expect(page.sent).toHaveLength(1);
  const retained = JSON.parse(
    localStorage.getItem("sturgis:draft:owner:agent:room")!,
  );
  expect(retained.pending.requestId).toBe(page.sent[0].request_id);
  await user.type(input, " changed");
  await user.click(screen.getByRole("button", { name: "Retry message" }));
  expect(page.sent).toHaveLength(1);
  expect(page.notify).toHaveBeenCalledWith(
    expect.stringContaining("Retry the saved message"),
    "error",
  );
  await user.clear(input);
  await user.type(input, "Preserve this exact request");
  await user.click(screen.getByRole("button", { name: "Retry message" }));
  await waitFor(() => expect(page.sent).toHaveLength(2));
  expect(page.sent[1]).toEqual(page.sent[0]);
  await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(""));
  expect(
    JSON.parse(localStorage.getItem("sturgis:draft:owner:agent:room")!),
  ).toEqual({ text: "", attachments: [] });
});
it("renders imported evidence and markdown without executing HTML, remote images or unsafe links", async () => {
  const events = [
    {
      id: "u",
      cursor: 1,
      kind: "user.steer",
      origin: "portable.import",
      payload: { text: '<img src=x onerror="alert(1)">' },
    },
    {
      id: "a",
      cursor: 2,
      kind: "output.persisted",
      origin: "portable.import",
      payload: {
        stream_id: "s",
        content: {
          type: "text",
          text: "**Evidence** [source](https://example.com) ![diagram](https://example.com/image.png) ![](https://example.com/empty.png)\n<script>window.attacked=1</script>",
        },
      },
    },
    {
      id: "status",
      cursor: 3,
      kind: "output.persisted",
      payload: {
        stream_id: "s",
        content: {
          type: "status",
          state: "failed",
          error: { code: "max_tokens", message: "Output limit reached" },
        },
      },
    },
  ];
  const page = mount({ events });
  expect(await screen.findByText("Evidence")).toBeTruthy();
  expect(screen.getAllByText("Imported history")).toHaveLength(2);
  expect(screen.getByText("Steering")).toBeTruthy();
  expect(screen.getByText("[Image: diagram]")).toBeTruthy();
  expect(screen.getByText("[Image: external image]")).toBeTruthy();
  const link = screen.getByRole("link", { name: "source" });
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(link.getAttribute("target")).toBe("_blank");
  expect(
    page.container.querySelector(".message-body img,.message-body script"),
  ).toBeNull();
  expect(screen.getByText("failed · Output limit reached")).toBeTruthy();
});
it("recovers corrupt local drafts, persists navigation drafts and surfaces model/cancellation guidance", async () => {
  localStorage.setItem("sturgis:draft:owner:agent:room", "not json");
  const page = mount({ ready: false, failCancel: true });
  await screen.findByText("Research thread");
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Set up model" }));
  expect(page.onConfigure).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: /Shape an idea/ }));
  const input = screen.getByRole("textbox", { name: "Message" });
  expect((input as HTMLTextAreaElement).value).toBe("Help me shape an idea");
  await user.keyboard("{Shift>}{Enter}{/Shift}");
  expect((input as HTMLTextAreaElement).value).toContain("\n");
  await user.click(screen.getByRole("button", { name: "Stop" }));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith("Cancellation rejected", "error"),
  );
  page.unmount();
  expect(
    JSON.parse(localStorage.getItem("sturgis:draft:owner:agent:room")!).text,
  ).toContain("Help me shape an idea");
});
it("draft persistence failure is visible and composition Enter does not submit partial IME input", async () => {
  const page = mount();
  await screen.findByText("Research thread");
  const storage = vi
    .spyOn(Storage.prototype, "setItem")
    .mockImplementation(() => {
      throw new DOMException("Quota", "QuotaExceededError");
    });
  const input = screen.getByRole("textbox", { name: "Message" });
  fireEvent.change(input, { target: { value: "still composing" } });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  expect(page.sent).toHaveLength(0);
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      expect.stringContaining("could not save the local draft"),
      "error",
    ),
  );
  storage.mockRestore();
});

it.each([false, true])(
  "a suspended agent keeps its draft and requires an observed successful resume before sending (failure=%s)",
  async (failResume) => {
    const page = mount({ suspended: true, failResume });
    await screen.findByRole("button", { name: "Resume agent" });
    const input = screen.getByLabelText("Message");
    await userEvent.type(input, "Keep my paused draft");
    expect(screen.getByRole("button", { name: "Send message" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(
      page.fetcher.mock.calls.some(
        ([url]) => url.includes("/commands/") || url.includes("/events?"),
      ),
    ).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Resume agent" }));
    if (failResume) {
      await waitFor(() =>
        expect(page.notify).toHaveBeenCalledWith(
          "Runtime cannot resume",
          "error",
        ),
      );
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toHaveProperty("disabled", true);
      expect(page.sent).toHaveLength(0);
    } else {
      await screen.findByText("Research thread");
      expect(input).toHaveProperty("value", "Keep my paused draft");
      await userEvent.click(
        screen.getByRole("button", { name: "Send message" }),
      );
      await waitFor(() => expect(page.sent).toHaveLength(1));
    }
  },
);

it("renaming and archiving preserve current revisions, surface rejection, and refresh selection only after success", async () => {
  let rooms = [
      { id: "room", title: "Research thread", revision: 4, archived: false },
    ],
    reject = true;
  const changes: Record<string, unknown>[] = [];
  const page = mount({
    handlers: {
      "conversations.list": () => ({ items: rooms }),
      "conversations.update": (params) => {
        changes.push(params);
        if (reject) throw new Error("Conversation revision changed");
        expect(params.expected_revision).toBe(rooms[0].revision);
        if (params.archived) rooms = [];
        else
          rooms = [{ ...rooms[0], title: String(params.title), revision: 5 }];
        return { updated: true };
      },
    },
  });
  await screen.findByText("Research thread");
  const menu = async (label: string) => {
    await userEvent.click(
      screen.getByRole("button", { name: "Conversation options" }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: label }));
  };
  await menu("Rename conversation");
  fireEvent.change(screen.getByLabelText("Conversation title"), {
    target: { value: "Collected evidence" },
  });
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }),
  );
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Conversation revision changed",
      "error",
    ),
  );
  expect(screen.getByRole("dialog")).toBeTruthy();
  reject = false;
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }),
  );
  await screen.findByText("Collected evidence");
  expect(changes.at(-1)).toEqual({
    id: "room",
    title: "Collected evidence",
    expected_revision: 4,
  });
  await menu("Archive conversation");
  await userEvent.click(
    screen.getByRole("button", { name: "Keep conversation" }),
  );
  expect(changes).toHaveLength(2);
  await menu("Archive conversation");
  reject = true;
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Archive conversation",
    }),
  );
  await waitFor(() => expect(changes).toHaveLength(3));
  expect(page.onSelect).not.toHaveBeenCalled();
  reject = false;
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Archive conversation",
    }),
  );
  await waitFor(() => expect(page.onSelect).toHaveBeenCalledWith(null));
  expect(changes.at(-1)).toEqual({
    id: "room",
    archived: true,
    expected_revision: 5,
  });
  await waitFor(() =>
    expect(screen.queryByText("Collected evidence")).toBeNull(),
  );
});

it("attachment effects are sent as source IDs, removed sources stay out of messages, and upload failures preserve the draft", async () => {
  const uploads: Record<string, unknown>[] = [];
  const page = mount({
    handlers: {
      "attachments.upload": (params) => {
        uploads.push(params);
        if (params.name === "failed.txt")
          return envelope(
            null,
            {
              code: "attachment_rejected",
              message: "Attachment upload rejected",
            },
            409,
          );
        return {
          id: params.name === "notes.txt" ? "source-notes" : "source-evidence",
          name: params.name,
        };
      },
    },
  });
  await screen.findByText("Research thread");
  const input = screen.getByLabelText("Attachment file") as HTMLInputElement;
  const chooser = vi.spyOn(input, "click");
  await userEvent.click(screen.getByRole("button", { name: "Attach file" }));
  expect(chooser).toHaveBeenCalledOnce();
  const file = (name: string, text: string) => {
    const value = new File([text], name, { type: "text/plain" });
    Object.defineProperty(value, "arrayBuffer", {
      value: async () => new TextEncoder().encode(text).buffer,
    });
    return value;
  };
  await userEvent.upload(input, file("notes.txt", "grounded notes"));
  await screen.findByRole("button", { name: "Remove notes.txt" });
  await userEvent.upload(input, file("evidence.txt", "source evidence"));
  await screen.findByRole("button", { name: "Remove evidence.txt" });
  expect(uploads[0]).toMatchObject({
    name: "notes.txt",
    media_type: "text/plain",
    data_base64: btoa("grounded notes"),
    operation_id: expect.any(String),
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Remove notes.txt" }),
  );
  expect(screen.queryByRole("button", { name: "Remove notes.txt" })).toBeNull();
  await userEvent.type(
    screen.getByLabelText("Message"),
    "Review the remaining source",
  );
  await userEvent.upload(input, file("failed.txt", "failed bytes"));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Attachment upload rejected",
      "error",
    ),
  );
  expect(input.value).toBe("");
  expect(screen.getByLabelText("Message")).toHaveProperty(
    "value",
    "Review the remaining source",
  );
  screen.getByRole("combobox", { name: "Message mode" }).focus();
  await userEvent.keyboard("{Enter}{End}{Enter}");
  await userEvent.click(screen.getByLabelText("Message"));
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(page.sent).toHaveLength(1));
  expect(page.sent[0].params).toMatchObject({
    text: "Review the remaining source",
    attachments: ["source-evidence"],
    kind: "steer",
  });
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Message accepted. Your agent is continuing the conversation.",
    ),
  );
  await userEvent.click(screen.getByRole("button", { name: "Stop" }));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Cancellation requested. Recorded effects remain visible.",
    ),
  );
});

it("the first message creates a conversation, returns its selection, and jump-to-latest resumes following output", async () => {
  let rooms: Record<string, unknown>[] = [];
  const created = vi.fn(() => {
    rooms = [
      { id: "fresh", title: "A fresh start", revision: 1, archived: false },
    ];
    return rooms[0];
  });
  const page = mount({
    handlers: {
      "conversations.list": () => ({ items: rooms }),
      "conversations.create": created,
    },
  });
  await screen.findByRole("button", { name: /Explore a question/ });
  await userEvent.click(
    screen.getByRole("button", { name: /Explore a question/ }),
  );
  expect(document.activeElement).toBe(screen.getByLabelText("Message"));
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(page.onSelect).toHaveBeenCalledWith("fresh"));
  expect(created).toHaveBeenCalledWith(
    { title: "A fresh start" },
    expect.objectContaining({ request_id: expect.any(String) }),
  );
  expect(page.sent[0].params).toMatchObject({
    conversation_id: "fresh",
    text: "Explore a question with me",
  });
  await screen.findByText("A fresh start");
  const history = screen.getByRole("region", { name: "Conversation history" });
  Object.defineProperties(history, {
    scrollHeight: { value: 1200, configurable: true },
    clientHeight: { value: 400, configurable: true },
    scrollTop: { value: 100, writable: true, configurable: true },
  });
  const scroll = vi.spyOn(history, "scrollTo");
  fireEvent.scroll(history);
  await userEvent.click(
    await screen.findByRole("button", { name: "Jump to latest" }),
  );
  expect(scroll).toHaveBeenCalledWith({ top: 1200, behavior: "smooth" });
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Jump to latest" })).toBeNull(),
  );
});

it("an acknowledged-late attachment reuses its request and operation IDs while changed content is fenced and repeated source receipts produce one chip", async () => {
  const calls: Record<string, unknown>[] = [];
  const receipts = new Map<string, { id: string; name: string }>();
  let lose = true;
  const page = mount({
    handlers: {
      "attachments.upload": (params, body) => {
        calls.push(body);
        expect(params.operation_id).toBe(body.request_id);
        let source = receipts.get(String(body.request_id));
        if (!source) {
          source = { id: "same-source", name: String(params.name) };
          receipts.set(String(body.request_id), source);
        }
        if (lose) {
          lose = false;
          throw new TypeError("Upload acknowledgement lost");
        }
        return source;
      },
    },
  });
  await screen.findByText("Research thread");
  const input = screen.getByLabelText("Attachment file") as HTMLInputElement;
  const file = (text: string) => {
    const f = new File([text], "private-notes.txt", { type: "text/plain" });
    Object.defineProperty(f, "arrayBuffer", {
      value: async () => new TextEncoder().encode(text).buffer,
    });
    return f;
  };
  await userEvent.upload(input, file("original private evidence"));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      expect.stringContaining("outcome is unconfirmed"),
      "error",
    ),
  );
  expect(calls).toHaveLength(1);
  expect(receipts.size).toBe(1);
  expect(
    screen.queryByRole("button", { name: "Remove private-notes.txt" }),
  ).toBeNull();
  const retained = Object.keys(sessionStorage).find((key) =>
    key.includes("attachments.upload"),
  )!;
  const metadata = sessionStorage.getItem(retained)!;
  expect(metadata).not.toContain("private-notes");
  expect(metadata).not.toContain(btoa("original private evidence"));
  await userEvent.upload(input, file("different private evidence"));
  await waitFor(() =>
    expect(page.notify).toHaveBeenLastCalledWith(
      expect.stringContaining("has an unknown outcome"),
      "error",
    ),
  );
  expect(calls).toHaveLength(1);
  await userEvent.upload(input, file("original private evidence"));
  await screen.findByRole("button", { name: "Remove private-notes.txt" });
  expect(calls).toHaveLength(2);
  expect(calls[1]).toEqual(calls[0]);
  expect(receipts.size).toBe(1);
  expect(sessionStorage.getItem(retained)).toBeNull();
  await userEvent.upload(input, file("original private evidence"));
  await waitFor(() => expect(calls).toHaveLength(3));
  expect(
    screen.getAllByRole("button", { name: "Remove private-notes.txt" }),
  ).toHaveLength(1);
  await userEvent.type(
    screen.getByLabelText("Message"),
    "Use the one admitted source",
  );
  await userEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(page.sent).toHaveLength(1));
  expect(page.sent[0].params).toMatchObject({ attachments: ["same-source"] });
});
