import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
  AgentContext,
  useAction,
  useAgent,
  useAgentQuery,
} from "../src/hooks/useAgent";
import { useEvents } from "../src/hooks/useEvents";
const reply = (data: unknown) =>
  new Response(JSON.stringify({ version: "consumer/1", data, error: null }));
const flush = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

it("event replay deduplicates, advances cursor, reconnects and cancels obsolete conversation polls", async () => {
  vi.useFakeTimers();
  const one = {
    id: "one",
    cursor: 1,
    kind: "output.persisted",
    payload: { content: { type: "text", text: "Hello" } },
  };
  const two = {
    id: "two",
    cursor: 2,
    kind: "output.persisted",
    payload: { content: { type: "text", text: " world" } },
  };
  let late: (response: Response) => void = () => {};
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(reply({ items: [one], cursor: 1, has_more: true }))
    .mockResolvedValueOnce(
      reply({ items: [one, two], cursor: 2, has_more: false }),
    )
    .mockRejectedValueOnce(new TypeError("Disconnected"))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          late = resolve;
        }),
    );
  vi.stubGlobal("fetch", fetcher);
  const hook = renderHook(
    ({ conversation }) => useEvents("agent-a", conversation),
    { initialProps: { conversation: null as string | null } },
  );
  expect(hook.result.current).toEqual({
    events: [],
    connection: "idle",
    loading: false,
    error: null,
  });
  hook.rerender({ conversation: "conversation-a" });
  await flush();
  expect(hook.result.current.events).toEqual([one]);
  expect(hook.result.current.connection).toBe("idle");
  expect(hook.result.current.loading).toBe(true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(hook.result.current.events).toEqual([one, two]);
  expect(hook.result.current.loading).toBe(false);
  expect(hook.result.current.connection).toBe("live");
  expect(fetcher.mock.calls[1][0]).toContain("after=1");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20);
  });
  expect(hook.result.current.connection).toBe("reconnecting");
  expect(hook.result.current.events).toEqual([one, two]);
  expect(hook.result.current.error).toBe("Disconnected");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1200);
  });
  expect(fetcher.mock.calls[3][0]).toContain("after=2");
  const signal = fetcher.mock.calls[3][1].signal as AbortSignal;
  hook.rerender({ conversation: null });
  expect(signal.aborted).toBe(true);
  await act(async () =>
    late(reply({ items: [two], cursor: 3, has_more: false })),
  );
  expect(hook.result.current).toEqual({
    events: [],
    connection: "idle",
    loading: false,
    error: null,
  });
  hook.unmount();
});

it("empty replay remains live and an aborted rejected request never schedules a reconnect", async () => {
  vi.useFakeTimers();
  let reject: (reason: unknown) => void = () => {};
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(reply({ items: [], cursor: 0, has_more: false }))
    .mockImplementationOnce(
      () =>
        new Promise((_resolve, no) => {
          reject = no;
        }),
    );
  vi.stubGlobal("fetch", fetcher);
  const hook = renderHook(() => useEvents("agent-a", "conversation-a"));
  await flush();
  expect(hook.result.current.connection).toBe("live");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20);
  });
  hook.unmount();
  await act(async () => reject(new DOMException("Aborted", "AbortError")));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("switching saved rooms resets readiness, drains empty history pages and resumes a paused cursor without losing messages", async () => {
  vi.useFakeTimers();
  const first = {
    id: "first",
    cursor: 10,
    kind: "user.prompt",
    payload: { text: "Saved first room" },
  };
  const second = {
    id: "second",
    cursor: 30,
    kind: "user.prompt",
    payload: { text: "Saved second room" },
  };
  let obsolete: (response: Response) => void = () => {};
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      reply({ items: [first], cursor: 10, has_more: false }),
    )
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          obsolete = resolve;
        }),
    )
    .mockResolvedValueOnce(reply({ items: [], cursor: 20, has_more: true }))
    .mockResolvedValueOnce(
      reply({ items: [second], cursor: 30, has_more: false }),
    )
    .mockResolvedValueOnce(
      reply({ items: [second], cursor: 30, has_more: false }),
    )
    .mockResolvedValueOnce(
      reply({ items: [first], cursor: 10, has_more: false }),
    );
  vi.stubGlobal("fetch", fetcher);
  const hook = renderHook(
    ({ conversation, enabled }) =>
      useEvents("agent", conversation, { enabled }),
    {
      initialProps: { conversation: "first", enabled: true },
    },
  );
  await flush();
  expect(hook.result.current.events).toEqual([first]);
  expect(hook.result.current.loading).toBe(false);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20);
  });
  const obsoleteSignal = fetcher.mock.calls[1][1].signal as AbortSignal;
  hook.rerender({ conversation: "second", enabled: true });
  expect(hook.result.current.events).toEqual([]);
  expect(hook.result.current.loading).toBe(true);
  expect(hook.result.current.connection).toBe("idle");
  expect(obsoleteSignal.aborted).toBe(true);
  await flush();
  expect(hook.result.current.loading).toBe(true);
  expect(hook.result.current.connection).toBe("idle");
  expect(fetcher.mock.calls[2][0]).toContain("conversation_id=second&after=0");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(fetcher.mock.calls[3][0]).toContain("after=20");
  expect(hook.result.current.events).toEqual([second]);
  expect(hook.result.current.loading).toBe(false);
  await act(async () =>
    obsolete(reply({ items: [first], cursor: 40, has_more: false })),
  );
  expect(hook.result.current.events).toEqual([second]);
  hook.rerender({ conversation: "second", enabled: false });
  expect(hook.result.current.events).toEqual([second]);
  expect(hook.result.current.connection).toBe("idle");
  expect((fetcher.mock.calls[3][1].signal as AbortSignal).aborted).toBe(true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(fetcher).toHaveBeenCalledTimes(4);
  hook.rerender({ conversation: "second", enabled: true });
  await flush();
  expect(fetcher.mock.calls[4][0]).toContain("conversation_id=second&after=30");
  expect(hook.result.current.events).toEqual([second]);
  hook.rerender({ conversation: "first", enabled: true });
  expect(hook.result.current.loading).toBe(true);
  await flush();
  expect(fetcher.mock.calls[5][0]).toContain("conversation_id=first&after=0");
  expect(hook.result.current.events).toEqual([first]);
  hook.unmount();
});

function wrapper(agentId = "agent-a", notify = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <AgentContext.Provider value={{ agentId, accountId: "owner", notify }}>
        {children}
      </AgentContext.Provider>
    </QueryClientProvider>
  );
  return { Wrapper, client, notify };
}
it("agent queries have agent-specific keys, pass cancellation and can be disabled", async () => {
  const fetcher = vi.fn().mockImplementation(async (url: string) =>
    reply(
      url.endsWith("/agents/agent-a")
        ? {
            runtime: {
              agent_id: "agent-a",
              desired: "running",
              observed: "running",
            },
          }
        : { objective: "Read evidence" },
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const { Wrapper, client } = wrapper();
  const hook = renderHook(
    () => useAgentQuery("goals.list", { state: "active" }),
    { wrapper: Wrapper },
  );
  await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
  expect(hook.result.current.data).toEqual({ objective: "Read evidence" });
  expect(
    client.getQueryData([
      "agent",
      "agent-a",
      "goals.list",
      { state: "active" },
    ]),
  ).toEqual({ objective: "Read evidence" });
  expect(fetcher.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
  const disabled = renderHook(
    () => useAgentQuery("overview.get", {}, { enabled: false, interval: 1000 }),
    { wrapper: Wrapper },
  );
  expect(disabled.result.current.fetchStatus).toBe("idle");
  disabled.unmount();
  hook.unmount();
  client.clear();
});
it("actions invalidate only their agent, expose pending state and report service or unknown errors", async () => {
  let resolve: (response: Response) => void = () => {};
  const fetcher = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    )
    .mockResolvedValueOnce(reply({ ok: true }))
    .mockRejectedValueOnce(new Error("Revision changed"))
    .mockRejectedValueOnce("non-error");
  vi.stubGlobal("fetch", fetcher);
  const { Wrapper, client, notify } = wrapper();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const hook = renderHook(() => useAction(), { wrapper: Wrapper });
  let pending: Promise<unknown>;
  act(() => {
    pending = hook.result.current.execute(
      "goals.create",
      { objective: "Study" },
      "Goal saved",
    );
  });
  expect(hook.result.current.pending).toBe(true);
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  await act(async () => {
    resolve(reply({ id: "goal" }));
    expect(await pending).toEqual({ id: "goal" });
  });
  expect(hook.result.current.pending).toBe(false);
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["agent", "agent-a"] });
  expect(notify).toHaveBeenCalledWith("Goal saved");
  await act(async () => {
    await hook.result.current.execute("overview.get");
  });
  expect(notify).toHaveBeenCalledTimes(1);
  await act(async () => {
    expect(await hook.result.current.execute("goals.update")).toBeUndefined();
  });
  expect(notify).toHaveBeenCalledWith(
    expect.stringContaining("outcome is unconfirmed"),
    "error",
  );
  await act(async () => {
    await hook.result.current.execute("overview");
  });
  expect(notify).toHaveBeenCalledWith(
    "This action could not be completed.",
    "error",
  );
  client.clear();
});
it("requires an agent context for agent-bound hooks", () => {
  expect(() => renderHook(() => useAgent())).toThrow(
    "An active agent is required.",
  );
});
