import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  focusManager,
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
  AgentContext,
  canWakeRuntime,
  pauseRuntimeQueries,
  useAgentQuery,
  useRuntimeState,
} from "../src/hooks/useAgent";
import { api, command } from "../src/lib/api";
import type { RuntimeState } from "../src/lib/types";

const reply = (data: unknown, status = 200) =>
  new Response(
    JSON.stringify({
      version: "consumer/1",
      data: status === 200 ? data : null,
      error:
        status === 200
          ? null
          : {
              code: "control_unavailable",
              message: "Control status unavailable",
            },
    }),
    { status },
  );
const disposals: Array<() => void> = [];
afterEach(() => {
  for (const dispose of disposals) dispose();
  disposals.length = 0;
  focusManager.setFocused(undefined);
});

function rig() {
  const agent = "guard-agent",
    owner = "guard-owner";
  let state: RuntimeState = {
    agent_id: agent,
    desired: "running",
    observed: "running",
  };
  let initial: Promise<Response> | undefined,
    controlFailure = false,
    suspendFailure = false;
  const requests: string[] = [];
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    const path = url.replace("/api/v1", "");
    requests.push(path);
    if (path === `/agents/${agent}`) {
      if (initial) {
        const response = initial;
        initial = undefined;
        return response;
      }
      return controlFailure
        ? reply(null, 503)
        : reply({ id: agent, runtime: state });
    }
    if (path.includes("/commands/"))
      return reply({ items: [], observed: state.observed });
    if (path === `/agents/${agent}/lifecycle/suspend`) {
      if (suspendFailure) return reply(null, 503);
      state = { ...state, desired: "suspended", observed: "suspended" };
      return reply(state);
    }
    if (path === `/agents/${agent}/lifecycle/start`) {
      state = { ...state, desired: "running", observed: "running" };
      return reply(state);
    }
    throw new Error(`Unexpected request ${options.method} ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <AgentContext.Provider
        value={{ agentId: agent, accountId: owner, notify: vi.fn() }}
      >
        {children}
      </AgentContext.Provider>
    </QueryClientProvider>
  );
  function mount() {
    const hook = renderHook(
      () => {
        const runtime = useRuntimeState(owner, agent);
        const records = useAgentQuery(
          "permissions.pending",
          {},
          { interval: 25 },
        );
        // The shell has its own observer of this same key, outside useAgentQuery.
        const shell = useQuery({
          queryKey: ["agent", agent, "conversations.list", {}],
          queryFn: ({ signal }) =>
            command(agent, "conversations.list", {}, { signal }),
          enabled: canWakeRuntime(runtime.data),
          staleTime: 0,
        });
        return { runtime, records, shell };
      },
      { wrapper },
    );
    disposals.push(() => {
      hook.unmount();
      pauseRuntimeQueries(client, owner, agent, false);
      client.clear();
    });
    return hook;
  }
  async function control(action: "start" | "suspend") {
    pauseRuntimeQueries(client, owner, agent, true);
    try {
      await client.cancelQueries({ queryKey: ["agent", agent] });
      return await api<RuntimeState>(
        `/agents/${agent}/lifecycle/${action}`,
        {},
      );
    } finally {
      pauseRuntimeQueries(client, owner, agent, false);
    }
  }
  return {
    client,
    mount,
    control,
    requests,
    delayFirst: (response: Promise<Response>) => {
      initial = response;
    },
    failControl: (value: boolean) => {
      controlFailure = value;
    },
    failSuspend: (value: boolean) => {
      suspendFailure = value;
    },
    setState: (value: RuntimeState) => {
      state = value;
    },
    privateCount: () =>
      requests.filter((path) => path.includes("/commands/")).length,
  };
}
const cycle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 90));
  });
async function focusAndInvalidate(client: QueryClient) {
  await act(async () => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
    await client.invalidateQueries({ queryKey: ["agent", "guard-agent"] });
  });
}

it("waits for observed control state before any private or shell data request", async () => {
  const test = rig();
  let release!: (value: Response) => void;
  test.delayFirst(
    new Promise((resolve) => {
      release = resolve;
    }),
  );
  const hook = test.mount();
  await waitFor(() => expect(test.requests).toEqual(["/agents/guard-agent"]));
  await cycle();
  expect(test.privateCount()).toBe(0);
  expect(hook.result.current.records.error).toBeNull();
  expect(hook.result.current.records.isPending).toBe(true);
  await act(async () =>
    release(
      reply({
        runtime: {
          agent_id: "guard-agent",
          desired: "running",
          observed: "running",
        },
      }),
    ),
  );
  await waitFor(() => expect(test.privateCount()).toBeGreaterThanOrEqual(2));
  expect(hook.result.current.runtime.data?.observed).toBe("running");
});

it("suspension fences interval, focus and shell-key invalidation until an explicit start is observed", async () => {
  const test = rig(),
    hook = test.mount();
  await waitFor(() => expect(test.privateCount()).toBeGreaterThanOrEqual(2));
  await act(async () => {
    await test.control("suspend");
  });
  await waitFor(() =>
    expect(hook.result.current.runtime.data?.observed).toBe("suspended"),
  );
  const before = test.privateCount();
  await cycle();
  await focusAndInvalidate(test.client);
  await cycle();
  expect(test.privateCount()).toBe(before);
  expect(hook.result.current.records.isPending).toBe(false);
  await act(async () => {
    await test.control("start");
  });
  await waitFor(() =>
    expect(hook.result.current.runtime.data?.observed).toBe("running"),
  );
  await waitFor(() => expect(test.privateCount()).toBeGreaterThan(before));
});

it("a failed suspend releases the temporary gate only after fresh running status arrives", async () => {
  const test = rig(),
    hook = test.mount();
  await waitFor(() => expect(test.privateCount()).toBeGreaterThanOrEqual(2));
  test.failSuspend(true);
  await act(async () => {
    await expect(test.control("suspend")).rejects.toThrow(
      "Control status unavailable",
    );
  });
  await waitFor(() =>
    expect(hook.result.current.runtime.data?.private_queries_paused).toBe(
      false,
    ),
  );
  const before = test.privateCount();
  await cycle();
  expect(test.privateCount()).toBeGreaterThan(before);
  expect(hook.result.current.runtime.data?.observed).toBe("running");
});

it("control failure keeps a failed lifecycle action fenced until observation recovers", async () => {
  const test = rig(),
    hook = test.mount();
  await waitFor(() => expect(test.privateCount()).toBeGreaterThanOrEqual(2));
  test.failSuspend(true);
  test.failControl(true);
  await act(async () => {
    await expect(test.control("suspend")).rejects.toThrow();
  });
  await waitFor(() => expect(hook.result.current.runtime.error).toBeTruthy());
  const before = test.privateCount();
  await cycle();
  await focusAndInvalidate(test.client);
  await cycle();
  expect(test.privateCount()).toBe(before);
  test.failControl(false);
  await act(async () => {
    await hook.result.current.runtime.refetch();
  });
  await waitFor(() => expect(test.privateCount()).toBeGreaterThan(before));
});

it("a failed background control observation cannot reuse an old running status to wake private work", async () => {
  const test = rig(),
    hook = test.mount();
  await waitFor(() => expect(test.privateCount()).toBeGreaterThanOrEqual(2));
  test.failControl(true);
  await act(async () => {
    await hook.result.current.runtime.refetch();
  });
  await waitFor(() => expect(hook.result.current.runtime.error).toBeTruthy());
  const before = test.privateCount();
  await cycle();
  await focusAndInvalidate(test.client);
  await cycle();
  expect(test.privateCount()).toBe(before);
  test.failControl(false);
  test.setState({
    agent_id: "guard-agent",
    desired: "suspended",
    observed: "suspended",
  });
  await act(async () => {
    await hook.result.current.runtime.refetch();
  });
  await cycle();
  expect(test.privateCount()).toBe(before);
});
