import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import GatewayPage from "../src/pages/gateway/GatewayPage";
import AgentGatewayModel from "../src/pages/gateway/AgentGatewayModel";
import App from "../src/App";
import StandaloneGateway from "../src/pages/gateway/StandaloneGateway";
import NativeAuthDialog from "../src/pages/gateway/NativeAuthDialog";
import { useAgentQuery } from "../src/hooks/useAgent";
import type {
  GatewayConnection,
  GatewayKey,
  GatewayModel,
  GatewayPreset,
  GatewayRoute,
  Provider,
} from "../src/lib/types";
import { mountPage } from "./harness";
const model = (id: string): GatewayModel => ({
  id,
  connection_id: "conn",
  name: id === "alpha" ? "Alpha model" : "Beta model",
  context_limit: 8192,
  output_limit: 1024,
  tools: id === "alpha",
});
const presets: GatewayPreset[] = [
  {
    id: "groq",
    name: "Groq",
    endpoint: "https://api.groq.com/openai/v1",
    protocol: "openai",
    auth: "api_key",
    category: "free_tier",
    description: "Fast inference with a free tier.",
    key_url: "https://console.groq.com/keys",
    defaults: {},
  },
  {
    id: "ollama",
    name: "Ollama",
    endpoint: "http://127.0.0.1:11434/v1",
    protocol: "openai",
    auth: "none",
    category: "local",
    description: "Models on your own machine.",
    defaults: {},
  },
  {
    id: "custom",
    name: "Custom",
    endpoint: "",
    protocol: "openai",
    auth: "none",
    category: "custom",
    description: "Your own compatible server.",
    defaults: {},
  },
];
const connection = (): GatewayConnection => ({
  id: "conn",
  revision: 3,
  name: "My Groq",
  preset_id: "groq",
  endpoint: presets[0].endpoint,
  protocol: "openai",
  models: [model("alpha"), model("beta")],
  credential_configured: true,
  status: "connected",
  error: null,
});
const clients: QueryClient[] = [];
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
});
const answer = (data: unknown, error: unknown = null) =>
  new Response(JSON.stringify({ version: "consumer/1", data, error }), {
    status: error ? 409 : 200,
  });
type Handler = (body: Record<string, unknown>) => unknown | Promise<unknown>;
function gateway(
  extra: Record<string, Handler> = {},
  app: boolean | "standalone" = false,
) {
  const calls: Array<{
    path: string;
    method: string;
    body: Record<string, unknown>;
  }> = [];
  const handlers: Record<string, Handler> = {
    "GET presets": () => presets,
    "GET auth/providers": () => [],
    "GET auth/accounts": () => [],
    "GET connections": () => [],
    "GET status": () => ({
      endpoint: "http://127.0.0.1:8787/model-gateway/v1",
      connections: 0,
      models: 0,
    }),
    "GET models": () => [],
    "GET routes": () => [],
    "GET keys": () => [],
    "GET usage": () => ({
      requests: 0,
      total_tokens: 0,
      failures: 0,
      input_tokens: 0,
      output_tokens: 0,
      recent: [],
    }),
    ...extra,
  };
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    const path = url.replace("/api/v1/gateway/", ""),
      method = options.method || "GET";
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ path, method, body });
    if (app === true && url === "/api/v1/health")
      return answer({ mode: "self_hosted" });
    if (app === true && url === "/api/v1/bootstrap")
      return answer({ bootstrap_required: false });
    if (app === true && url === "/api/v1/auth/me")
      return answer({ id: "owner", email: "owner@example.com", admin: true });
    if (app === true && url === "/api/v1/agents") return answer({ items: [] });
    const handler = handlers[`${method} ${path}`];
    if (!handler) throw new Error(`Unexpected ${method} ${path}`);
    try {
      return answer(await handler(body));
    } catch (error) {
      if (error instanceof TypeError) throw error;
      return answer(null, {
        code: "gateway_error",
        message: (error as Error).message,
      });
    }
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  clients.push(client);
  return {
    calls,
    client,
    ...render(
      <QueryClientProvider client={client}>
        {app === "standalone" ? (
          <StandaloneGateway />
        ) : app ? (
          <App />
        ) : (
          <GatewayPage accountId="owner" />
        )}
      </QueryClientProvider>,
    ),
  };
}
const click = async (name: string) =>
  userEvent.click(screen.getByRole("button", { name, exact: true }));
const fill = (label: string | RegExp, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

it("opens the installation gateway before any agent exists, with no private worker request", async () => {
  const page = gateway({}, true);
  await screen.findByRole("button", { name: "Create agent" });
  await click("Model Gateway");
  await screen.findByRole("heading", { name: "Model Gateway" });
  await screen.findByRole("button", { name: "Connect Groq" });
  expect(
    page.calls.some(
      (call) =>
        call.path.includes("/commands/") || call.path.includes("/lifecycle/"),
    ),
  ).toBe(false);
});

it("filters provider presets and copies the standalone endpoint", async () => {
  const page = gateway();
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: copy },
  });
  await screen.findByRole("button", { name: "Connect Groq" });
  await click("Local");
  expect(screen.queryByRole("button", { name: "Connect Groq" })).toBeNull();
  await click("All providers");
  fill("Search providers", "missing");
  await screen.findByText("No providers found");
  fill("Search providers", "Groq");
  expect(screen.getByRole("button", { name: "Connect Groq" })).toBeTruthy();
  await click("Copy gateway endpoint");
  expect(copy).toHaveBeenCalledWith("http://127.0.0.1:8787/model-gateway/v1");
  copy.mockRejectedValueOnce(new Error("clipboard denied"));
  await click("Copy gateway endpoint");
  await screen.findByText(
    "Clipboard unavailable. Select and copy the endpoint below.",
  );
  expect(page.calls.every((call) => !call.path.includes("agents"))).toBe(true);
});

it("imports live models and connects from one drawer without caching or retaining API keys", async () => {
  const page = gateway({
    "POST discover": () => ({
      models: [model("alpha"), model("beta")],
      source: "live",
    }),
    "POST connections": () => connection(),
  });
  await screen.findByRole("button", { name: "Connect Groq" });
  await click("Connect Groq");
  expect((screen.getByLabelText("Endpoint") as HTMLInputElement).value).toBe(
    presets[0].endpoint,
  );
  const secretInput = screen.getByLabelText("API key") as HTMLInputElement;
  expect(secretInput.type).toBe("password");
  fill("API key", "private-provider-key");
  fill("Provider name", "My fast provider");
  await click("Load models");
  await screen.findByText("Alpha model");
  fill("Search models", "Alpha");
  await click("Select visible");
  fill("Search models", "absent");
  expect(screen.getByText("No models match your search.")).toBeTruthy();
  fill("Search models", "");
  await click("Connect provider");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find(
      (call) => call.method === "POST" && call.path === "connections",
    )?.body,
  ).toEqual({
    preset_id: "groq",
    name: "My fast provider",
    endpoint: presets[0].endpoint,
    api_key: "private-provider-key",
    models: ["alpha"],
  });
  expect(
    JSON.stringify(
      page.client
        .getQueryCache()
        .getAll()
        .map((query) => query.state),
    ),
  ).not.toContain("private-provider-key");
  expect(page.client.getMutationCache().getAll()).toEqual([]);
  expect(JSON.stringify({ ...sessionStorage, ...localStorage })).not.toContain(
    "private-provider-key",
  );
  await click("Connect Groq");
  expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe("");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("retains form values on discovery or connect errors and supports explicit manual model IDs", async () => {
  let fail = true;
  const page = gateway({
    "POST discover": () => {
      throw new Error(
        "API key was rejected. Check the key in your provider account.",
      );
    },
    "POST connections": () => {
      if (fail) throw new Error("Model is unavailable. Choose another model.");
      return connection();
    },
  });
  await screen.findByRole("button", { name: "Connect Groq" });
  await click("Connect Groq");
  fill("API key", "wrong-key");
  await click("Load models");
  await screen.findByRole("alert");
  expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe(
    "wrong-key",
  );
  await userEvent.click(screen.getByText("Advanced: add a model ID"));
  fill("Model ID", "manual-model");
  await click("Add model ID");
  await click("Connect provider");
  await screen.findByText("Model is unavailable. Choose another model.");
  expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe(
    "wrong-key",
  );
  fail = false;
  await click("Connect provider");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.filter(
      (call) => call.path === "connections" && call.method === "POST",
    ),
  ).toHaveLength(2);
});

it("supports local servers without a key, empty discovery and resetting models when endpoint changes", async () => {
  let empty = true;
  const page = gateway({
    "POST discover": () => ({
      models: empty ? [] : [model("alpha")],
      source: "live",
    }),
    "POST connections": () => connection(),
  });
  await screen.findByRole("button", { name: "Connect Ollama" });
  await click("Connect Ollama");
  await click("Load models");
  await screen.findByText(/This provider returned no models/);
  empty = false;
  await click("Load models");
  await screen.findByRole("checkbox");
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("checkbox"));
  fill("Endpoint", "http://localhost:11434/v1");
  expect(screen.queryByRole("checkbox")).toBeNull();
  await click("Load models");
  await screen.findByRole("checkbox");
  await click("Select visible");
  await click("Connect provider");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.path === "discover")?.body,
  ).not.toHaveProperty("api_key");
});

it("edits secured connections without reentering a key, refreshes and disconnects with current revisions", async () => {
  let current: GatewayConnection[] = [connection()];
  const page = gateway({
    "GET connections": () => current,
    "PATCH connections/conn": (body) => {
      expect(body.expected_revision).toBe(3);
      current[0] = { ...current[0], revision: 4, name: body.name as string };
      return current[0];
    },
    "POST connections/conn/refresh": (body) => {
      expect(body.expected_revision).toBe(4);
      current[0].revision = 5;
      return current[0];
    },
    "DELETE connections/conn": (body) => {
      expect(body.expected_revision).toBe(5);
      current = [];
      return { disconnected: true };
    },
  });
  await screen.findByRole("heading", { name: "My Groq" });
  expect(screen.getByText("Models imported")).toBeTruthy();
  expect(
    screen.queryByText("Models ready to use through your gateway."),
  ).toBeNull();
  await click("Edit");
  expect((screen.getByLabelText("API key") as HTMLInputElement).required).toBe(
    false,
  );
  fill("Provider name", "Renamed provider");
  await click("Save connection");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await click("Refresh Renamed provider models");
  await waitFor(() =>
    expect(page.calls.some((call) => call.path.endsWith("/refresh"))).toBe(
      true,
    ),
  );
  await click("Disconnect Renamed provider");
  await click("Keep connection");
  await click("Disconnect Renamed provider");
  await click("Disconnect provider");
  await waitFor(() =>
    expect(
      screen.queryByRole("heading", { name: "Renamed provider" }),
    ).toBeNull(),
  );
  expect(
    page.calls.find((call) => call.method === "PATCH")?.body,
  ).not.toHaveProperty("api_key");
});

it("creates and reorders route fallbacks and edits an existing route", async () => {
  let routes: GatewayRoute[] = [];
  const page = gateway({
    "GET models": () => [model("alpha"), model("beta")],
    "GET routes": () => routes,
    "POST routes": (body) => {
      routes = [{ ...body, revision: 1 } as unknown as GatewayRoute];
      return routes[0];
    },
  });
  await screen.findByRole("button", { name: "Connect Groq" });
  await userEvent.click(screen.getByRole("tab", { name: "Model routes" }));
  await screen.findByText("A plan B, built in");
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Create route" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  await click("Create route");
  fill("Route name", "Everyday assistant");
  fill("Model alias", "everyday");
  await userEvent.click(screen.getByRole("checkbox", { name: /Alpha model/ }));
  await userEvent.click(screen.getByRole("checkbox", { name: /Beta model/ }));
  await click("Move beta up");
  await click("Move beta down");
  await click("Remove alpha");
  await userEvent.click(screen.getByRole("checkbox", { name: /Alpha model/ }));
  await click("Save route");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.path === "routes" && call.method === "POST")
      ?.body,
  ).toEqual({
    id: "everyday",
    name: "Everyday assistant",
    models: ["beta", "alpha"],
    strategy: "priority",
    expected_revision: 0,
  });
  await click("Edit route");
  screen.getByRole("combobox", { name: "Routing strategy" }).focus();
  await userEvent.keyboard("{Enter}{End}{Enter}");
  await click("Save route");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.filter(
      (call) => call.path === "routes" && call.method === "POST",
    )[1].body.strategy,
  ).toBe("round_robin");
});

it("generates a one-time gateway key without caching it and revokes after confirmation", async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: copy },
  });
  let keys: GatewayKey[] = [];
  const page = gateway({
    "GET keys": () => keys,
    "POST keys": (body) => {
      keys = [
        {
          id: "key",
          name: body.name as string,
          prefix: "stg_public",
          created_at: 1,
          models: [],
          revoked: false,
        },
      ];
      return { ...keys[0], key: "stg_private_once" };
    },
    "DELETE keys/key": () => {
      keys[0].revoked = true;
      return { revoked: true };
    },
  });
  await screen.findByRole("button", { name: "Connect Groq" });
  await userEvent.click(screen.getByRole("tab", { name: "Access & usage" }));
  await screen.findByText("Ready when you are");
  await click("Create API key");
  fill("Key name", "My app");
  await click("Generate key");
  await screen.findByRole("heading", { name: "Your key is ready" });
  expect(
    (screen.getByLabelText("Gateway API key") as HTMLInputElement).value,
  ).toBe("stg_private_once");
  await click("Copy key");
  expect(copy).toHaveBeenCalledWith("stg_private_once");
  await click("Done");
  expect(
    JSON.stringify(
      page.client
        .getQueryCache()
        .getAll()
        .map((query) => query.state),
    ),
  ).not.toContain("stg_private_once");
  expect(page.client.getMutationCache().getAll()).toEqual([]);
  expect(screen.queryByDisplayValue("stg_private_once")).toBeNull();
  await click("Revoke");
  await click("Keep key");
  await click("Revoke");
  await click("Revoke key");
  await screen.findByText("Revoked");
});

it("shows upstream connection and usage errors without treating them as readiness", async () => {
  gateway({
    "GET connections": () => [
      {
        ...connection(),
        status: "error",
        error: {
          code: "auth",
          message: "Reconnect this provider with a current key.",
        },
      },
    ],
    "GET usage": () => ({
      requests: 2,
      total_tokens: 100,
      failures: 1,
      input_tokens: 80,
      output_tokens: 20,
      recent: [
        { model: "alpha", status: "ok", tokens: 100, created_at: 1 },
        { model: "beta", status: "error", tokens: 0, created_at: 2 },
      ],
    }),
    "POST connections/conn/refresh": () => {
      throw new Error("Provider is offline. Try again later.");
    },
  });
  await screen.findByText("Needs attention");
  await click("Refresh My Groq models");
  await screen.findByText("Provider is offline. Try again later.");
  await userEvent.click(screen.getByRole("tab", { name: "Access & usage" }));
  await screen.findByText("100 tokens");
  expect(screen.getByText("error")).toBeTruthy();
});

it("agent model selection only binds a gateway model and surfaces failed probes", async () => {
  const open = vi.fn();
  let fail = true;
  const page = mountPage(
    <AgentGatewayModel providers={[]} onOpenGateway={open} />,
    {
      "GET /gateway/models": () => [
        { ...model("alpha"), provider_name: "Groq" },
      ],
      "POST /agents/agent/gateway-model": (params) => {
        expect(params).toEqual({ model: "alpha" });
        if (fail) throw new Error("Gateway probe failed. Check the provider.");
        return { tools: true };
      },
    },
  );
  await screen.findByText("Alpha model");
  await click("Open Model Gateway");
  expect(open).toHaveBeenCalledOnce();
  await click("Use model");
  await screen.findByRole("alert");
  expect(screen.getByText("Could not use Alpha model")).toBeTruthy();
  expect(
    screen.getByText("Gateway probe failed. Check the provider."),
  ).toBeTruthy();
  fail = false;
  await click("Retry");
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Gateway model selected. Chat and tools are ready.",
    ),
  );
  expect(
    page.calls.filter((call) => call.method.startsWith("providers.")).length,
  ).toBe(0);
});

it("identifies the failed model, shows check progress and refreshes agent state before a scoped retry", async () => {
  let overviewReads = 0,
    attempts = 0;
  let failCheck: (error: Error) => void = () => {};
  function ObservedModelSettings() {
    const overview = useAgentQuery<{ provider_settings: Provider[] }>(
      "overview",
    );
    return (
      <AgentGatewayModel providers={overview.data?.provider_settings || []} />
    );
  }
  const page = mountPage(<ObservedModelSettings />, {
    overview: () => {
      overviewReads += 1;
      return { provider_settings: [] };
    },
    "GET /gateway/models": () => [model("alpha"), model("beta")],
    "POST /agents/agent/gateway-model": (params) => {
      expect(params).toEqual({ model: "alpha" });
      attempts += 1;
      if (attempts === 1)
        return new Promise((_resolve, reject) => {
          failCheck = reject;
        });
      return { tools: true };
    },
  });
  await screen.findByRole("heading", { name: "Alpha model" });
  await waitFor(() => expect(overviewReads).toBeGreaterThan(0));
  const previousReads = overviewReads;
  const alpha = within(
    screen.getByRole("heading", { name: "Alpha model" }).closest("article")!,
  );
  const beta = within(
    screen.getByRole("heading", { name: "Beta model" }).closest("article")!,
  );
  await userEvent.click(alpha.getByRole("button", { name: "Use model" }));
  expect(
    alpha
      .getByRole("button", { name: "Checking model…" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    beta.getByRole("button", { name: "Use model" }).hasAttribute("disabled"),
  ).toBe(true);
  expect(page.notify).not.toHaveBeenCalled();
  failCheck(new Error("The provider timed out. Retry this model."));
  const error = await alpha.findByRole("alert");
  expect(error.textContent).toContain("Could not use Alpha model");
  expect(error.textContent).toContain(
    "The provider timed out. Retry this model.",
  );
  expect(beta.queryByRole("alert")).toBeNull();
  await waitFor(() => expect(overviewReads).toBeGreaterThan(previousReads));
  expect(page.notify).not.toHaveBeenCalled();
  await userEvent.click(await alpha.findByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Gateway model selected. Chat and tools are ready.",
    ),
  );
  expect(alpha.queryByRole("alert")).toBeNull();
  expect(attempts).toBe(2);
});

it("identifies chat-only fallback and retries the selected model before reporting tools ready", async () => {
  let active: Provider | undefined;
  let attempts = 0;
  let completeToolCheck: (provider: Provider) => void = () => {};
  function ObservedModelSettings() {
    const overview = useAgentQuery<{ provider_settings: Provider[] }>(
      "overview",
    );
    return (
      <AgentGatewayModel providers={overview.data?.provider_settings || []} />
    );
  }
  const page = mountPage(<ObservedModelSettings />, {
    overview: () => ({ provider_settings: active ? [active] : [] }),
    "GET /gateway/models": () => [model("alpha"), model("beta")],
    "POST /agents/agent/gateway-model": (params) => {
      expect(params).toEqual({ model: "alpha" });
      attempts += 1;
      if (attempts === 1) {
        active = {
          id: "gateway",
          revision: 1,
          model: "alpha",
          endpoint: "http://127.0.0.1/model-gateway/v1/chat/completions",
          context_limit: 8192,
          output_limit: 1024,
          tools: false,
          credential_ref: "gateway-key",
          ready: true,
          active: true,
        };
        return active;
      }
      return new Promise<Provider>((resolve) => {
        completeToolCheck = resolve;
      });
    },
  });
  const alpha = within(
    (await screen.findByRole("heading", { name: "Alpha model" })).closest(
      "article",
    )!,
  );
  const beta = within(
    screen.getByRole("heading", { name: "Beta model" }).closest("article")!,
  );
  await userEvent.click(alpha.getByRole("button", { name: "Use model" }));
  await screen.findByText("Chat only");
  expect(screen.queryByText("Chat and tools")).toBeNull();
  expect(
    screen.getByText(
      /This model can reply, but tool use has not been verified/,
    ),
  ).toBeTruthy();
  expect(alpha.getByText("Selected")).toBeTruthy();
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Gateway model selected for chat only. Tool use is not verified.",
    ),
  );
  await userEvent.click(
    alpha.getByRole("button", { name: "Retry tool check" }),
  );
  expect(attempts).toBe(2);
  expect(
    alpha
      .getByRole("button", { name: "Checking model…" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    beta.getByRole("button", { name: "Use model" }).hasAttribute("disabled"),
  ).toBe(true);
  expect(screen.getByText("Chat only")).toBeTruthy();
  expect(page.notify).toHaveBeenCalledTimes(1);
  active = { ...active!, revision: 2, tools: true };
  completeToolCheck(active);
  await screen.findByText("Chat and tools");
  expect(screen.queryByText("Chat only")).toBeNull();
  expect(
    screen.queryByText(
      /This model can reply, but tool use has not been verified/,
    ),
  ).toBeNull();
  expect(alpha.queryByRole("button", { name: "Retry tool check" })).toBeNull();
  expect(alpha.getByText("Selected")).toBeTruthy();
  await waitFor(() =>
    expect(page.notify).toHaveBeenLastCalledWith(
      "Gateway model selected. Chat and tools are ready.",
    ),
  );
  expect(
    page.calls
      .filter((call) => call.method === "POST /agents/agent/gateway-model")
      .map((call) => call.params),
  ).toEqual([{ model: "alpha" }, { model: "alpha" }]);
});

it("standalone dashboard authenticates with an ephemeral administrator key and clears metadata on logout", async () => {
  let loggedIn = false,
    rejectLogin = true,
    rejectLogout = true;
  const page = gateway(
    {
      "GET session": () => ({ authenticated: loggedIn }),
      "POST session": (body) => {
        expect(body).toEqual({ admin_key: "private-admin" });
        if (rejectLogin) throw new Error("Administrator key is incorrect.");
        loggedIn = true;
        return { authenticated: true };
      },
      "DELETE session": () => {
        if (rejectLogout) throw new Error("Could not end session.");
        loggedIn = false;
        return {};
      },
    },
    "standalone",
  );
  await screen.findByRole("heading", { name: "Welcome to your gateway" });
  fill("Administrator key", "private-admin");
  await click("Open gateway");
  await screen.findByText("Administrator key is incorrect.");
  rejectLogin = false;
  await click("Open gateway");
  await screen.findByRole("button", { name: "Connect Groq" });
  expect(page.client.getMutationCache().getAll()).toEqual([]);
  expect(
    JSON.stringify(
      page.client
        .getQueryCache()
        .getAll()
        .map((query) => query.state),
    ),
  ).not.toContain("private-admin");
  await click("Sign out");
  await screen.findByText("Could not end session.");
  rejectLogout = false;
  await click("Sign out");
  await screen.findByRole("heading", { name: "Welcome to your gateway" });
  expect(
    (screen.getByLabelText("Administrator key") as HTMLInputElement).value,
  ).toBe("");
  expect(
    page.client
      .getQueryCache()
      .getAll()
      .every((query) => query.queryKey[0] !== "gateway"),
  ).toBe(true);
});

const authProvider = {
  id: "copilot",
  name: "GitHub Copilot",
  flow: "device_code" as const,
  description: "Connect your existing account.",
  options: [],
};
const authSession = {
  id: "sign-in",
  provider_id: "copilot",
  name: "My account",
  flow: "device_code" as const,
  status: "pending" as const,
  expires_at: 100000,
  poll_after: 1,
  user_code: "ABCD-EFGH",
  verification_uri: "https://github.com/login/device",
};

it("native device sign-in polls a server session then imports models with an account reference, never a token", async () => {
  let authenticated = false;
  const nativePreset = {
    ...presets[0],
    id: "copilot",
    name: "GitHub Copilot",
    auth: "oauth",
  };
  const page = gateway({
    "GET presets": () => [...presets, nativePreset],
    "GET auth/providers": () => [authProvider],
    "GET auth/accounts": () =>
      authenticated
        ? [
            {
              id: "account",
              provider_id: "copilot",
              name: "My account",
              status: "connected",
              expires_at: 0,
              revision: 1,
            },
          ]
        : [],
    "POST auth/start": () => authSession,
    "POST auth/sign-in/poll": () => {
      authenticated = true;
      return { ...authSession, status: "connected", account_id: "account" };
    },
    "POST discover": () => ({ models: [model("alpha")], source: "live" }),
    "POST connections": () => connection(),
  });
  await screen.findByRole("button", { name: "Sign in to GitHub Copilot" });
  await click("Sign in to GitHub Copilot");
  await click("Continue with GitHub Copilot");
  await screen.findByText("ABCD-EFGH");
  expect(
    screen
      .getByRole("link", { name: "Open GitHub Copilot sign-in" })
      .getAttribute("href"),
  ).toBe(authSession.verification_uri);
  await screen.findByText("Signed in as My account", {}, { timeout: 4000 });
  expect(screen.queryByLabelText("API key")).toBeNull();
  await click("Load models");
  await screen.findByRole("checkbox");
  await click("Select visible");
  await click("Connect provider");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.path === "discover")?.body.auth_account_id,
  ).toBe("account");
  expect(
    page.calls.find(
      (call) => call.path === "connections" && call.method === "POST",
    )?.body.auth_account_id,
  ).toBe("account");
  await click("Use My account");
  await screen.findByText("Signed in as My account");
  await click("Cancel");
});

it("browser sign-in supports callback completion and cancellation with actionable validation", async () => {
  const provider = {
    ...authProvider,
    id: "codex",
    name: "Codex",
    flow: "authorization_code_pkce" as const,
    default_redirect_uri: "http://localhost:1455/auth/callback",
  };
  const session = {
    ...authSession,
    provider_id: "codex",
    flow: "authorization_code_pkce",
    user_code: undefined,
    authorization_url: "https://auth.example.com/authorize?state=nonce",
    verification_uri: undefined,
    poll_after: 100,
  };
  const done = vi.fn(),
    close = vi.fn();
  const page = mountPage(
    <NativeAuthDialog
      accountId="owner"
      provider={provider}
      onConnected={done}
      onClose={close}
    />,
    {
      "POST /gateway/auth/start": (body) => {
        expect(body.redirect_uri).toBe(provider.default_redirect_uri);
        return session;
      },
      "POST /gateway/auth/sign-in/complete": (body) => {
        expect(body).toEqual({ code: "private-code", state: "nonce" });
        return { ...session, status: "connected", account_id: "account" };
      },
      "DELETE /gateway/auth/sign-in": () => ({}),
    },
  );
  await click("Continue with Codex");
  await screen.findByRole("link", { name: "Open Codex sign-in" });
  await userEvent.click(
    screen.getByText("Sign-in did not return automatically?"),
  );
  fill("Callback URL", "invalid");
  await click("Complete sign-in");
  await screen.findByText(
    "Paste the complete callback URL, including its code and state.",
  );
  fill(
    "Callback URL",
    "http://localhost:1455/auth/callback?code=private-code&state=nonce",
  );
  await click("Complete sign-in");
  await waitFor(() =>
    expect(done).toHaveBeenCalledWith("account", "My account"),
  );
  expect(page.client.getMutationCache().getAll()).toEqual([]);
});

it("expired native sessions can restart and cancel without presenting an authenticated account", async () => {
  const done = vi.fn(),
    close = vi.fn();
  let expires = true;
  mountPage(
    <NativeAuthDialog
      accountId="owner"
      provider={authProvider}
      onConnected={done}
      onClose={close}
    />,
    {
      "POST /gateway/auth/start": () => authSession,
      "POST /gateway/auth/sign-in/poll": () => ({
        ...authSession,
        status: expires ? "expired" : "pending",
      }),
      "DELETE /gateway/auth/sign-in": () => ({}),
    },
  );
  await click("Continue with GitHub Copilot");
  await screen.findByText("ABCD-EFGH");
  await click("Check sign-in");
  await screen.findByText(
    "This sign-in expired. Start again to get a new link or code.",
  );
  expires = false;
  await click("Start again");
  await screen.findByText("ABCD-EFGH");
  await click("Cancel");
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(done).not.toHaveBeenCalled();
});

it("shows exact gateway model aliases with search and copy for external clients", async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: copy },
  });
  gateway({
    "GET models": () => [
      { ...model("conn/alpha"), name: "Alpha", provider_name: "Groq" },
    ],
  });
  await screen.findByRole("button", { name: "Connect Groq" });
  await userEvent.click(
    screen.getByRole("tab", { name: "Models", exact: true }),
  );
  await screen.findByText("conn/alpha");
  await click("Copy model ID conn/alpha");
  expect(copy).toHaveBeenCalledWith("conn/alpha");
  copy.mockRejectedValueOnce(new Error());
  await click("Copy model ID conn/alpha");
  await screen.findByText(
    "Clipboard unavailable. Select and copy the model ID shown here.",
  );
  fill("Search available models", "absent");
  await screen.findByText("No models found");
});

it("handles native service failures and cancellation denial without claiming sign-in", async () => {
  const done = vi.fn(),
    close = vi.fn();
  let failStart = true,
    failCancel = true;
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: copy },
  });
  mountPage(
    <NativeAuthDialog
      accountId="owner"
      provider={authProvider}
      onConnected={done}
      onClose={close}
    />,
    {
      "POST /gateway/auth/start": () => {
        if (failStart)
          throw new Error("Provider sign-in is temporarily unavailable.");
        return { ...authSession, poll_after: 100 };
      },
      "DELETE /gateway/auth/sign-in": () => {
        if (failCancel) throw new Error("Cancellation could not be confirmed.");
        return {};
      },
    },
  );
  fill("Connection name", "Personal account");
  await click("Continue with GitHub Copilot");
  await screen.findByText("Provider sign-in is temporarily unavailable.");
  failStart = false;
  await click("Continue with GitHub Copilot");
  await screen.findByText("ABCD-EFGH");
  await click("Copy code");
  expect(copy).toHaveBeenCalledWith("ABCD-EFGH");
  copy.mockRejectedValueOnce(new Error());
  await click("Copied");
  await screen.findByText(
    "Clipboard unavailable. Select and copy the code above.",
  );
  await click("Cancel");
  await screen.findByText("Cancellation could not be confirmed.");
  expect(close).not.toHaveBeenCalled();
  failCancel = false;
  await click("Cancel");
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(done).not.toHaveBeenCalled();
});

it("retains route and key forms on rejected operations and never stores a failed key response", async () => {
  let failRoute = true,
    failKey = true;
  gateway({
    "GET models": () => [model("alpha")],
    "POST routes": () => {
      if (failRoute) throw new Error("Route model no longer exists.");
      return { id: "route" };
    },
    "POST keys": () => {
      if (failKey) throw new Error("Choose another key name.");
      return { id: "key", key: "one-time-key" };
    },
  });
  await screen.findByRole("button", { name: "Connect Groq" });
  await userEvent.click(screen.getByRole("tab", { name: "Model routes" }));
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Create route" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  await click("Create route");
  fill("Route name", "My route");
  await userEvent.click(screen.getByRole("checkbox", { name: /Alpha model/ }));
  await click("Save route");
  await screen.findByText("Route model no longer exists.");
  failRoute = false;
  await click("Save route");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await userEvent.click(screen.getByRole("tab", { name: "Access & usage" }));
  await click("Create API key");
  fill("Key name", "My key");
  await click("Generate key");
  expect(
    (await within(screen.getByRole("dialog")).findByRole("alert")).textContent,
  ).toBe("Choose another key name.");
  failKey = false;
  await click("Generate key");
  await screen.findByRole("heading", { name: "Your key is ready" });
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockRejectedValue(new Error()) },
  });
  await click("Copy key");
  await screen.findByText(
    "Clipboard unavailable. Select and copy the key above.",
  );
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByDisplayValue("one-time-key")).toBeNull();
});

it("manual model imports declare their mode and supported native account options stay optional", async () => {
  const page = gateway({ "POST connections": () => connection() });
  await screen.findByRole("button", { name: "Connect Custom" });
  await click("Connect Custom");
  fill("Endpoint", "https://private.example.com/v1");
  await userEvent.click(screen.getByText("Advanced: add a model ID"));
  fill("Model ID", "private-model");
  await click("Add model ID");
  await click("Connect provider");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find(
      (call) => call.path === "connections" && call.method === "POST",
    )?.body,
  ).toMatchObject({ catalog_mode: "manual", models: ["private-model"] });
});

it("passes only explicitly filled advanced sign-in options and permits cancelling before authorization", async () => {
  const close = vi.fn();
  const provider = {
    ...authProvider,
    id: "kiro",
    name: "Kiro",
    options: ["region", "start_url", "project_id", "unsupported"],
  };
  const page = mountPage(
    <NativeAuthDialog
      accountId="owner"
      provider={provider}
      onConnected={vi.fn()}
      onClose={close}
    />,
    {
      "POST /gateway/auth/start": () => ({
        ...authSession,
        status: "failed",
        error_code: "not_eligible",
      }),
    },
  );
  await userEvent.click(screen.getByText("Advanced account options"));
  fill("AWS region", " eu-west-1 ");
  fill("AWS access portal URL", " ");
  await click("Continue with Kiro");
  await screen.findByText(
    "Sign-in could not finish (not_eligible). Start again or check your provider account.",
  );
  expect(
    page.calls.find((call) => call.method === "POST /gateway/auth/start")
      ?.params.options,
  ).toEqual({ region: "eu-west-1" });
  await click("Cancel");
  expect(close).toHaveBeenCalledOnce();
});

it("keeps missing provider usage distinct from zero tokens", async () => {
  gateway({
    "GET usage": () => ({
      requests: 1,
      total_tokens: 0,
      failures: 0,
      unreported: 1,
      input_tokens: 0,
      output_tokens: 0,
      recent: [
        { model: "alpha", status: "completed", tokens: null, created_at: 1 },
      ],
    }),
  });
  await screen.findByRole("button", { name: "Connect Groq" });
  await userEvent.click(screen.getByRole("tab", { name: "Access & usage" }));
  await screen.findByText("Usage not reported");
  expect(
    screen.getByText(
      "Usage is missing for 1 provider requests. Totals include reported usage only.",
    ),
  ).toBeTruthy();
});

const gatewaySettings = {
  revision: 0,
  request_timeout: 30,
  max_concurrency: 4,
  requests_per_minute: 120,
  operator_limits: { request_timeout: 30, max_concurrency: 4 },
};
const configuration = {
  format: "sturgis-gateway-config/1",
  created_at: 1,
  credentials_included: false,
  connections: [{ id: "old" }],
  routes: [],
  settings: gatewaySettings,
};
async function openControls() {
  await screen.findByRole("button", { name: "Connect Groq" });
  await userEvent.click(screen.getByRole("tab", { name: "Access & usage" }));
  await userEvent.click(
    screen.getByText("Advanced: limits & configuration transfer"),
  );
  await screen.findByLabelText("Request timeout (seconds)");
}
function importFile(
  body: string,
  options: { size?: number; failure?: boolean } = {},
) {
  const file = new File([body], "gateway.json", { type: "application/json" });
  Object.defineProperty(file, "text", {
    value: options.failure
      ? () => Promise.reject(new Error("The file could not be read."))
      : () => Promise.resolve(body),
  });
  if (options.size)
    Object.defineProperty(file, "size", { value: options.size });
  fireEvent.change(screen.getByLabelText("Import configuration file"), {
    target: { files: [file] },
  });
}

it("saves gateway limits with their revision and preserves inputs on conflicts", async () => {
  let fail = true;
  let current = { ...gatewaySettings };
  const page = gateway({
    "GET settings": () => current,
    "POST settings": (body) => {
      if (fail) throw new Error("Settings changed. Reload and try again.");
      current = {
        ...current,
        request_timeout: Number(body.request_timeout),
        revision: 1,
      };
      return current;
    },
  });
  await openControls();
  fill("Request timeout (seconds)", "15");
  fill("Concurrent requests", "2");
  fill("Requests per minute", "60");
  await click("Save gateway limits");
  await screen.findByText("Settings changed. Reload and try again.");
  expect(
    (screen.getByLabelText("Request timeout (seconds)") as HTMLInputElement)
      .value,
  ).toBe("15");
  fail = false;
  await click("Save gateway limits");
  await screen.findByText("Gateway limits saved.");
  expect(
    page.calls.find(
      (call) => call.path === "settings" && call.method === "POST",
    )?.body,
  ).toEqual({
    expected_revision: 0,
    request_timeout: 15,
    max_concurrency: 2,
    requests_per_minute: 60,
  });
});

it("downloads credential-free metadata directly without persisting the export", async () => {
  const page = gateway({
    "GET settings": () => gatewaySettings,
    "GET export": () => configuration,
  });
  const create = vi.fn(() => "blob:gateway-export"),
    revoke = vi.fn();
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: create,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: revoke,
  });
  const anchor = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  await openControls();
  await click("Download configuration");
  await screen.findByText(
    "Configuration downloaded. Provider credentials and app keys are excluded.",
  );
  expect(create).toHaveBeenCalledOnce();
  expect(anchor).toHaveBeenCalledOnce();
  expect(revoke).toHaveBeenCalledWith("blob:gateway-export");
  expect(
    page.client
      .getQueryCache()
      .findAll()
      .some((query) => query.queryKey.includes("export")),
  ).toBe(false);
  expect(page.client.getMutationCache().getAll()).toHaveLength(0);
});

it("confirms configuration import, keeps failed imports retryable, and explains remapped authority", async () => {
  let fail = true;
  const page = gateway({
    "GET settings": () => gatewaySettings,
    "POST import": () => {
      if (fail) throw new Error("A provider preset is unavailable.");
      return {
        imported_connections: 1,
        imported_routes: 0,
        reconnection_required: true,
        settings_applied: false,
      };
    },
  });
  await openControls();
  importFile(JSON.stringify(configuration));
  await screen.findByRole("dialog", { name: "Import gateway configuration?" });
  expect(page.calls.some((call) => call.path === "import")).toBe(false);
  await click("Cancel import");
  expect(screen.queryByRole("dialog")).toBeNull();
  importFile(JSON.stringify(configuration));
  await screen.findByRole("dialog");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  importFile(JSON.stringify(configuration));
  await screen.findByRole("dialog");
  await click("Import configuration");
  await screen.findByText("A provider preset is unavailable.");
  expect(screen.getByRole("dialog")).toBeTruthy();
  fail = false;
  await click("Import configuration");
  await screen.findByText(
    "Imported 1 connections and 0 routes. Reconnect each provider, copy its new model IDs, and create app keys. Gateway limits were not changed.",
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(page.calls.find((call) => call.path === "import")?.body).toEqual({
    archive: configuration,
  });
});

it("rejects invalid, oversized and unreadable transfers and reports download failures", async () => {
  const page = gateway({
    "GET settings": () => gatewaySettings,
    "GET export": () => {
      throw new Error("Export unavailable. Try again.");
    },
  });
  await openControls();
  await click("Download configuration");
  await screen.findByText("Export unavailable. Try again.");
  importFile("not-json");
  await screen.findByText(
    "This file is not valid JSON. Choose a gateway configuration export.",
  );
  importFile(JSON.stringify({ ...configuration, credentials_included: true }));
  await screen.findByText(
    "Choose a credential-free Sturgis gateway configuration export.",
  );
  importFile("{}", { size: 2 * 1024 * 1024 + 1 });
  await screen.findByText("Choose a gateway configuration file up to 2 MiB.");
  importFile("{}", { failure: true });
  await screen.findByText("The file could not be read.");
  expect(page.calls.some((call) => call.path === "import")).toBe(false);
  fireEvent.change(screen.getByLabelText("Import configuration file"), {
    target: { files: [] },
  });
});

it("disconnects a native account only after confirmation and checks its revision", async () => {
  let connected = true,
    fail = true;
  const page = gateway({
    "GET auth/accounts": () =>
      connected
        ? [
            {
              id: "account",
              provider_id: "codex",
              name: "My Codex",
              status: "connected",
              revision: 8,
            },
          ]
        : [],
    "DELETE auth/accounts/account": () => {
      if (fail) throw new Error("Account changed. Refresh and retry.");
      connected = false;
      return { disconnected: true };
    },
  });
  await screen.findByRole("button", { name: "Disconnect account My Codex" });
  await click("Disconnect account My Codex");
  await click("Keep account");
  expect(page.calls.some((call) => call.method === "DELETE")).toBe(false);
  await click("Disconnect account My Codex");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  await click("Disconnect account My Codex");
  await click("Disconnect account");
  await within(screen.getByRole("dialog")).findByText(
    "Account changed. Refresh and retry.",
  );
  fail = false;
  await click("Disconnect account");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.queryByRole("button", { name: "Use My Codex" })).toBeNull();
  expect(page.calls.find((call) => call.method === "DELETE")?.body).toEqual({
    expected_revision: 8,
  });
});

it("makes manual callback recovery explicit when the registered listener is unavailable", async () => {
  mountPage(
    <NativeAuthDialog
      accountId="owner"
      provider={{ ...authProvider, flow: "authorization_code_pkce" }}
      onConnected={vi.fn()}
      onClose={vi.fn()}
    />,
    {
      "POST /gateway/auth/start": () => ({
        ...authSession,
        flow: "authorization_code_pkce",
        callback_mode: "manual",
      }),
      "DELETE /gateway/auth/session": () => ({
        ...authSession,
        status: "cancelled",
      }),
    },
  );
  await click(`Continue with ${authProvider.name}`);
  await screen.findByText(
    "Automatic return is unavailable. Finish signing in, then use the callback URL option below.",
  );
  expect(screen.getByText("Complete with callback URL")).toBeTruthy();
});

it("reconnects an imported native connection through sign-in without replacing its model IDs", async () => {
  const imported = {
    ...connection(),
    preset_id: "copilot",
    auth_account_id: null,
    credential_configured: false,
    status: "error",
    error: { code: "reconnect_required", message: "Reconnect this provider." },
  };
  const page = gateway({
    "GET presets": () => [
      { ...presets[0], id: "copilot", auth: "oauth", name: "GitHub Copilot" },
    ],
    "GET auth/providers": () => [authProvider],
    "GET connections": () => [imported],
    "POST auth/start": () => ({
      ...authSession,
      status: "connected",
      account_id: "restored-account",
    }),
    "PATCH connections/conn": () => ({
      ...imported,
      auth_account_id: "restored-account",
      status: "connected",
    }),
  });
  await screen.findByRole("button", { name: "Edit" });
  await click("Edit");
  await screen.findByRole("dialog", { name: "Sign in to GitHub Copilot" });
  await click("Continue with GitHub Copilot");
  await screen.findByText("Signed in as My account");
  expect(screen.queryByLabelText("API key")).toBeNull();
  await click("Save connection");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    page.calls.find((call) => call.method === "PATCH")?.body,
  ).toMatchObject({
    expected_revision: 3,
    auth_account_id: "restored-account",
    models: ["alpha", "beta"],
  });
  expect(
    page.calls.some(
      (call) => call.path === "connections" && call.method === "POST",
    ),
  ).toBe(false);
});

it("confirms route deletion with revision checking and preserves providers", async () => {
  let routes = [
    {
      id: "daily",
      name: "Daily",
      models: ["conn/alpha"],
      revision: 5,
      strategy: "priority",
    },
  ];
  let fail = true;
  const page = gateway({
    "GET routes": () => routes,
    "GET models": () => [{ ...model("alpha"), id: "conn/alpha" }],
    "DELETE routes/daily": () => {
      if (fail) throw new Error("The route changed. Refresh and retry.");
      routes = [];
      return { deleted: true };
    },
  });
  await screen.findByRole("button", { name: "Connect Groq" });
  await userEvent.click(screen.getByRole("tab", { name: "Model routes" }));
  await screen.findByRole("button", { name: "Delete route Daily" });
  await click("Delete route Daily");
  await click("Keep route");
  expect(page.calls.some((call) => call.method === "DELETE")).toBe(false);
  await click("Delete route Daily");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  await click("Delete route Daily");
  await click("Delete route");
  await screen.findByText("The route changed. Refresh and retry.");
  fail = false;
  await click("Delete route");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    screen.queryByRole("button", { name: "Delete route Daily" }),
  ).toBeNull();
  expect(page.calls.filter((call) => call.method === "DELETE")).toEqual([
    { path: "routes/daily", method: "DELETE", body: { expected_revision: 5 } },
    { path: "routes/daily", method: "DELETE", body: { expected_revision: 5 } },
  ]);
});
