import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { AgentContext, useAction } from "../src/hooks/useAgent";
const clients: QueryClient[] = [];
afterEach(() => {
  for (const client of clients) client.clear();
  clients.length = 0;
});
const key = "sturgis:pending-action:owner:agent:files.write";
const reply = (data: unknown, error: unknown = null, status = 200) =>
  new Response(JSON.stringify({ version: "consumer/1", data, error }), {
    status,
  });
function mount(agentId = "agent", accountId = "owner") {
  const notify = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  clients.push(client);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <AgentContext.Provider value={{ agentId, accountId, notify }}>
        {children}
      </AgentContext.Provider>
    </QueryClientProvider>
  );
  return { notify, ...renderHook(() => useAction(), { wrapper }) };
}
async function execute(
  hook: ReturnType<typeof mount>,
  params: Record<string, unknown> = { path: "note", data_base64: "c2VjcmV0" },
  method = "files.write",
) {
  let result: unknown;
  await act(async () => {
    result = await hook.result.current.execute(method, params);
  });
  return result;
}
it("unknown acknowledgement preserves only a digest and stable request identity across hook remount", async () => {
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("Dropped after acceptance"))
    .mockImplementationOnce(async () => reply({ after_sha256: "verified" }));
  vi.stubGlobal("fetch", fetcher);
  const first = mount();
  expect(await execute(first)).toBeUndefined();
  expect(first.notify).toHaveBeenCalledWith(
    expect.stringContaining("outcome is unconfirmed"),
    "error",
  );
  const stored = JSON.parse(sessionStorage.getItem(key)!);
  expect(Object.keys(stored).sort()).toEqual([
    "digest",
    "requestId",
    "version",
  ]);
  expect(stored.digest).toMatch(/^[a-f0-9]{64}$/);
  expect(sessionStorage.getItem(key)).not.toContain("c2VjcmV0");
  expect(sessionStorage.getItem(key)).not.toContain("note");
  const initial = JSON.parse(fetcher.mock.calls[0][1].body);
  first.unmount();
  const second = mount();
  expect(
    await execute(second, { data_base64: "c2VjcmV0", path: "note" }),
  ).toEqual({ after_sha256: "verified" });
  const retried = JSON.parse(fetcher.mock.calls[1][1].body);
  expect(retried.request_id).toBe(initial.request_id);
  expect(retried.params).toEqual(initial.params);
  expect(sessionStorage.getItem(key)).toBeNull();
});
it("an unresolved mutation blocks changed values but leaves independent account and agent scopes available", async () => {
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("Lost"))
    .mockImplementation(async () => reply({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  const first = mount();
  await execute(first);
  await execute(first, { path: "different", data_base64: "new" });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(first.notify).toHaveBeenLastCalledWith(
    expect.stringContaining("unknown outcome"),
    "error",
  );
  const other = mount("other-agent");
  await execute(other, { path: "different" });
  expect(fetcher).toHaveBeenCalledTimes(2);
  const account = mount("agent", "other-owner");
  await execute(account, { path: "different" });
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(sessionStorage.getItem(key)).not.toBeNull();
});
it("known rejection unlocks new values while uncertain consumer outcomes retain the original identity", async () => {
  const fetcher = vi
    .fn()
    .mockImplementationOnce(async () =>
      reply(null, { code: "conflict", message: "Preimage mismatch" }, 409),
    )
    .mockImplementationOnce(async () =>
      reply(
        null,
        { code: "worker_indeterminate", message: "Outcome unknown" },
        503,
      ),
    )
    .mockImplementationOnce(async () => reply({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  const hook = mount();
  await execute(hook);
  expect(sessionStorage.getItem(key)).toBeNull();
  expect(hook.notify).toHaveBeenLastCalledWith("Preimage mismatch", "error");
  await execute(hook, { path: "different" });
  const retained = JSON.parse(sessionStorage.getItem(key)!);
  expect(retained.requestId).not.toBe(
    JSON.parse(fetcher.mock.calls[0][1].body).request_id,
  );
  await execute(hook, { path: "different" });
  expect(JSON.parse(fetcher.mock.calls[2][1].body).request_id).toBe(
    retained.requestId,
  );
  expect(sessionStorage.getItem(key)).toBeNull();
});
it("concurrent same-method mutations cannot dispatch twice and pending stays true for the active request", async () => {
  let resolve: (response: Response) => void = () => {};
  const fetcher = vi.fn().mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  const hook = mount();
  let active: Promise<unknown>;
  act(() => {
    active = hook.result.current.execute("files.write", { path: "same" });
  });
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  await execute(hook, { path: "same" });
  expect(fetcher).toHaveBeenCalledOnce();
  expect(hook.result.current.pending).toBe(true);
  expect(hook.notify).toHaveBeenLastCalledWith(
    "This action is already waiting for a response.",
    "error",
  );
  await act(async () => {
    resolve(reply({ ok: true }));
    await active;
  });
  expect(hook.result.current.pending).toBe(false);
});
it("unsafe storage, invalid recovery receipts, invalid JSON and unavailable hashing block dispatch", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const hook = mount();
  const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("Storage denied");
  });
  await execute(hook);
  expect(hook.notify).toHaveBeenLastCalledWith(
    expect.stringContaining("storage is unavailable"),
    "error",
  );
  read.mockRestore();
  sessionStorage.setItem(key, '{"version":1,"digest":"bad","requestId":"bad"}');
  await execute(hook);
  expect(hook.notify).toHaveBeenLastCalledWith(
    expect.stringContaining("cannot be read"),
    "error",
  );
  sessionStorage.clear();
  const write = vi
    .spyOn(Storage.prototype, "setItem")
    .mockImplementation(() => {
      throw new Error("Quota");
    });
  await execute(hook);
  expect(hook.notify).toHaveBeenLastCalledWith(
    expect.stringContaining("No action was sent"),
    "error",
  );
  write.mockRestore();
  const cyclic: Record<string, unknown> = {};
  cyclic.self = cyclic;
  await execute(hook, cyclic);
  expect(hook.notify).toHaveBeenLastCalledWith(
    "Action parameters must contain valid JSON.",
    "error",
  );
  vi.stubGlobal("crypto", {
    randomUUID: () => "",
    subtle: {
      digest: async () => {
        throw new Error("Unsupported");
      },
    },
  });
  await execute(hook);
  expect(hook.notify).toHaveBeenLastCalledWith(
    expect.stringContaining("Secure request recovery is unavailable"),
    "error",
  );
  expect(fetcher).not.toHaveBeenCalled();
});
it("read operations need no mutation storage and dispatch a snapshot of mutable mutation inputs", async () => {
  const fetcher = vi.fn().mockImplementation(async () => reply({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  const hook = mount();
  const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("Storage unavailable");
  });
  expect(await execute(hook, {}, "files.list")).toEqual({ ok: true });
  read.mockRestore();
  const params = { path: "original", nested: { values: [3, 2, 1] } };
  let pending: Promise<unknown>;
  act(() => {
    pending = hook.result.current.execute("files.write", params);
    params.path = "changed-after-click";
  });
  await act(async () => {
    await pending;
  });
  expect(JSON.parse(fetcher.mock.calls[1][1].body).params).toEqual({
    path: "original",
    nested: { values: [3, 2, 1] },
  });
});
it.each([
  [
    "attachments.upload",
    { name: "source.txt", media_type: "text/plain", data_base64: "YQ==" },
  ],
  ["knowledge.add", { attachment_id: "source", title: "Cedar" }],
  ["knowledge.delete", { id: "document", expected_revision: 2 }],
  [
    "memory.correct",
    { id: "memory", content: "Corrected fact", expected_revision: 2 },
  ],
  ["memory.retract", { id: "memory", revision: 2, reason: "Source withdrawn" }],
] as const)(
  "recovered %s keeps the domain operation identity equal to the control request identity",
  async (method, params) => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Acknowledgement lost"))
      .mockImplementationOnce(async () => reply({ ok: true }));
    vi.stubGlobal("fetch", fetcher);
    const first = mount();
    await execute(first, { ...params, operation_id: "legacy-first" }, method);
    const initial = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(initial.params.operation_id).toBe(initial.request_id);
    first.unmount();
    const next = mount();
    await execute(
      next,
      { ...params, operation_id: "legacy-regenerated" },
      method,
    );
    const retried = JSON.parse(fetcher.mock.calls[1][1].body);
    expect(retried).toEqual(initial);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain("legacy");
  },
);
it.each([
  "unauthenticated",
  "csrf_failed",
  "forbidden",
  "origin_denied",
  "host_denied",
  "scope_denied",
])(
  "a %s rejection before receipt lookup cannot erase an earlier unknown outcome",
  async (code) => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Lost after admission"))
      .mockImplementationOnce(async () =>
        reply(
          null,
          { code, message: "Request rejected before receipt lookup" },
          code === "unauthenticated" || code === "csrf_failed" ? 401 : 403,
        ),
      )
      .mockImplementationOnce(async () => reply({ ok: true }));
    vi.stubGlobal("fetch", fetcher);
    const hook = mount();
    await execute(hook);
    const initial = JSON.parse(sessionStorage.getItem(key)!);
    await execute(hook);
    expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual(initial);
    await execute(hook, { path: "different" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(await execute(hook)).toEqual({ ok: true });
    expect(JSON.parse(fetcher.mock.calls[2][1].body).request_id).toBe(
      initial.requestId,
    );
    expect(sessionStorage.getItem(key)).toBeNull();
  },
);
