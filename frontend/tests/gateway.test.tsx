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
import type {
  GatewayConnection,
  GatewayKey,
  GatewayModel,
  GatewayPreset,
  GatewayRoute,
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
        return {};
      },
    },
  );
  await screen.findByText("Alpha model");
  await click("Open Model Gateway");
  expect(open).toHaveBeenCalledOnce();
  await click("Use model");
  await screen.findByRole("alert");
  fail = false;
  await click("Use model");
  await waitFor(() =>
    expect(page.notify).toHaveBeenCalledWith(
      "Gateway model selected. Your agent is ready.",
    ),
  );
  expect(
    page.calls.filter((call) => call.method.startsWith("providers.")).length,
  ).toBe(0);
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
