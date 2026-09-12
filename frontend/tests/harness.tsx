import { render } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentContext } from "../src/hooks/useAgent";
export type Call = {
  method: string;
  params: Record<string, unknown>;
  requestId: string;
};
const clients: QueryClient[] = [];
afterEach(() => {
  for (const client of clients) client.clear();
  clients.length = 0;
});
export function mountPage(
  page: ReactNode,
  handlers: Record<
    string,
    (params: Record<string, unknown>) => unknown | Promise<unknown>
  >,
) {
  const calls: Call[] = [];
  const notify = vi.fn();
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    const isCommand = url.includes("/commands/");
    const method = isCommand
      ? url.split("/").at(-1)!
      : `${options.method} ${url.replace(/^\/api\/v1/, "")}`;
    const body =
      options.body === undefined ? {} : JSON.parse(String(options.body));
    const params = isCommand ? body.params : body;
    calls.push({ method, params, requestId: body.request_id });
    const handler =
      handlers[method] ??
      (method === "GET /agents/agent"
        ? () => ({
            id: "agent",
            runtime: {
              agent_id: "agent",
              desired: "running",
              observed: "running",
            },
          })
        : undefined);
    if (!handler) throw new Error("Unexpected consumer command " + method);
    try {
      const data = await handler(params);
      if (data instanceof Response) return data;
      return new Response(
        JSON.stringify({
          version: "consumer/1",
          data,
          error: null,
        }),
      );
    } catch (error) {
      if (error instanceof TypeError) throw error;
      return new Response(
        JSON.stringify({
          version: "consumer/1",
          data: null,
          error: {
            code: "test_service_error",
            message: (error as Error).message,
          },
        }),
        { status: 409 },
      );
    }
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  clients.push(client);
  return {
    calls,
    notify,
    fetcher,
    client,
    ...render(
      <QueryClientProvider client={client}>
        <AgentContext.Provider
          value={{ agentId: "agent", accountId: "owner", notify }}
        >
          {page}
        </AgentContext.Provider>
      </QueryClientProvider>,
    ),
  };
}
