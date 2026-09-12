import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../src/App";
const answer = (data: unknown, error: unknown = null, status = 200) =>
  new Response(JSON.stringify({ version: "consumer/1", data, error }), {
    status,
  });
const principal = {
  id: "owner",
  email: "owner@example.com",
  admin: true,
  session_id: "session",
};
const clients: QueryClient[] = [];
afterEach(() => {
  for (const client of clients) client.clear();
  clients.length = 0;
  localStorage.clear();
});
function start(fetcher: ReturnType<typeof vi.fn>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, options: RequestInit) =>
      url.endsWith("/agents/agent")
        ? Promise.resolve(
            answer({
              id: "agent",
              runtime: {
                agent_id: "agent",
                desired: "running",
                observed: "running",
              },
            }),
          )
        : fetcher(url, options),
    ),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

it("installation failures require a successful reconnect and only unauthenticated errors lead to sign-in", async () => {
  let phase = 0;
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith("/health")) {
      if (!phase) throw new TypeError("Installation is offline");
      return answer({ mode: "self_hosted" });
    }
    if (url.endsWith("/bootstrap"))
      return answer({ bootstrap_required: false });
    if (url.endsWith("/auth/me"))
      return answer(
        null,
        {
          code: phase === 1 ? "forbidden" : "unauthenticated",
          message: phase === 1 ? "Account access denied" : "Sign in required",
        },
        phase === 1 ? 403 : 401,
      );
    throw new Error("Unexpected route " + url);
  });
  start(fetcher);
  await screen.findByRole("heading", { name: "Unable to open this space" });
  expect(screen.getByText("Installation is offline")).toBeTruthy();
  phase = 1;
  await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  await screen.findByText("Account access denied");
  expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
  phase = 2;
  await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
});
it("owner onboarding clears secrets from the form and exposes agent-creation failures before a retry", async () => {
  let authenticated = false,
    created = false,
    failCreate = true;
  const sent: unknown[] = [];
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/health")) return answer({ mode: "self_hosted" });
    if (url.endsWith("/bootstrap") && options.method === "GET")
      return answer({ bootstrap_required: !authenticated });
    if (url.endsWith("/bootstrap")) {
      sent.push(JSON.parse(String(options.body)));
      authenticated = true;
      return answer({ principal });
    }
    if (url.endsWith("/agents") && options.method === "GET")
      return answer({
        items: created ? [{ id: "agent", name: "Cedar", revision: 1 }] : [],
      });
    if (url.endsWith("/agents")) {
      if (failCreate)
        return answer(
          null,
          { code: "conflict", message: "Choose a different name" },
          409,
        );
      created = true;
      return answer({ id: "agent", name: "Cedar", revision: 1 });
    }
    if (url.endsWith("/conversations.list")) return answer({ items: [] });
    if (url.endsWith("/overview")) return answer({ provider_settings: [] });
    throw new Error("Unexpected route " + url);
  });
  start(fetcher);
  await screen.findByRole("button", { name: "Create owner account" });
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Email"), "owner@example.com");
  await user.type(screen.getByLabelText("Password"), "private-owner-password");
  await user.type(
    screen.getByLabelText(/^Installation token/),
    "private-installation-proof",
  );
  await user.click(
    screen.getByRole("button", { name: "Create owner account" }),
  );
  await screen.findByRole("button", { name: "Create agent" });
  expect(screen.queryByLabelText("Password")).toBeNull();
  expect(screen.queryByLabelText(/^Installation token/)).toBeNull();
  expect(sent).toEqual([
    {
      email: "owner@example.com",
      password: "private-owner-password",
      token: "private-installation-proof",
    },
  ]);
  await user.clear(screen.getByLabelText("Agent name"));
  await user.type(screen.getByLabelText("Agent name"), "Cedar");
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Choose a different name",
  );
  failCreate = false;
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  await screen.findByLabelText("Message");
  expect(
    screen.getByText("Your new space is ready. Connect a model to begin."),
  ).toBeTruthy();
});
it("command palette keyboard navigation and failed sign-out preserve the current account", async () => {
  let authenticated = true,
    logoutFails = true;
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/health")) return answer({ mode: "hosted" });
    if (url.endsWith("/bootstrap"))
      return answer({ bootstrap_required: false });
    if (url.endsWith("/auth/me"))
      return authenticated
        ? answer(principal)
        : answer(
            null,
            { code: "unauthenticated", message: "Sign in required" },
            401,
          );
    if (url.endsWith("/agents")) return answer({ items: [] });
    if (url.endsWith("/auth/logout")) {
      if (logoutFails)
        return answer(
          null,
          { code: "unavailable", message: "Sign-out could not be confirmed" },
          503,
        );
      authenticated = false;
      return answer({ logged_out: true });
    }
    if (url.endsWith("/auth/login"))
      return answer(
        null,
        { code: "unauthenticated", message: "Password did not match" },
        401,
      );
    throw new Error("Unexpected route " + url + " " + options.method);
  });
  start(fetcher);
  await screen.findByRole("button", { name: "Create agent" });
  const user = userEvent.setup();
  await user.keyboard("{Control>}k{/Control}");
  const dialog = screen.getByRole("dialog", { name: "Go anywhere" });
  await user.type(within(dialog).getByLabelText("Search your space"), "files");
  expect(
    within(dialog)
      .getAllByRole("button")
      .filter((button) => button.textContent === "Files"),
  ).toHaveLength(1);
  await user.click(within(dialog).getByRole("button", { name: "Files" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Account menu" }));
  await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    expect.stringContaining("Sign-out could not be confirmed"),
  );
  expect(screen.getByRole("button", { name: "Account menu" })).toBeTruthy();
  await user.click(
    screen.getByRole("button", { name: "Dismiss notification" }),
  );
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  logoutFails = false;
  await user.click(screen.getByRole("button", { name: "Account menu" }));
  await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
  await screen.findByRole("button", { name: "Sign in" });
  await user.type(screen.getByLabelText("Email"), "owner@example.com");
  await user.type(screen.getByLabelText("Password"), "wrong-private-password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Password did not match",
  );
});
it.each([false, true])(
  "hosted signup is exposed only when the installation enables it (%s)",
  async (allowSignup) => {
    const calls: { url: string; body: unknown }[] = [];
    let signupFails = true;
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (url.endsWith("/health")) return answer({ mode: "hosted" });
      if (url.endsWith("/bootstrap"))
        return answer({ bootstrap_required: false, allow_signup: allowSignup });
      if (url.endsWith("/auth/me"))
        return answer(
          null,
          { code: "unauthenticated", message: "Sign in required" },
          401,
        );
      if (url.endsWith("/auth/signup")) {
        calls.push({ url, body: JSON.parse(String(options.body)) });
        return signupFails
          ? answer(
              null,
              { code: "conflict", message: "Account could not be created" },
              409,
            )
          : answer({ principal: { ...principal, admin: false } });
      }
      if (url.endsWith("/agents")) return answer({ items: [] });
      throw new Error("Unexpected route " + url);
    });
    start(fetcher);
    await screen.findByRole("button", { name: "Sign in", exact: true });
    if (!allowSignup) {
      expect(
        screen.queryByRole("button", { name: "Create an account" }),
      ).toBeNull();
      expect(calls).toEqual([]);
      return;
    }
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Create an account" }));
    expect(screen.getByLabelText("Password").getAttribute("autocomplete")).toBe(
      "new-password",
    );
    expect(screen.queryByLabelText(/^Installation token/)).toBeNull();
    await user.type(screen.getByLabelText("Email"), "new@example.com");
    await user.type(screen.getByLabelText("Password"), "private-new-password");
    await user.click(
      screen.getByRole("button", { name: "Create account", exact: true }),
    );
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Account could not be created",
    );
    await user.click(
      screen.getByRole("button", { name: "Already have an account? Sign in" }),
    );
    expect(screen.queryByRole("alert")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Create an account" }));
    signupFails = false;
    await user.click(
      screen.getByRole("button", { name: "Create account", exact: true }),
    );
    await screen.findByRole("button", { name: "Create agent" });
    expect(calls.at(-1)?.body).toEqual({
      email: "new@example.com",
      password: "private-new-password",
    });
    expect(screen.queryByLabelText("Password")).toBeNull();
  },
);

it("agent switching isolates drafts and conversation actions, while failed creation leaves the selected history intact", async () => {
  let listFails = true,
    newFails = true;
  const agents = [
    { id: "alpha", name: "Cedar", revision: 3, archived: false },
    { id: "beta", name: "Maple", revision: 2, archived: false },
  ];
  const rooms: Record<string, Record<string, unknown>[]> = {
    alpha: [
      {
        id: "alpha-one",
        title: "Cedar research",
        revision: 1,
        archived: false,
      },
      {
        id: "alpha-two",
        title: "Earlier Cedar notes",
        revision: 2,
        archived: false,
      },
    ],
    beta: [
      { id: "beta-one", title: "Maple planning", revision: 1, archived: false },
    ],
  };
  const commands: {
    agent: string;
    method: string;
    body: Record<string, unknown>;
  }[] = [];
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (url.endsWith("/health")) return answer({ mode: "hosted" });
    if (url.endsWith("/bootstrap"))
      return answer({ bootstrap_required: false });
    if (url.endsWith("/auth/me")) return answer(principal);
    if (url.endsWith("/agents"))
      return listFails
        ? answer(
            null,
            {
              code: "unavailable",
              message: "Agent list temporarily unavailable",
            },
            503,
          )
        : answer({ items: agents });
    const agent = url.match(/\/agents\/(alpha|beta)(?:\/|$)/)?.[1];
    if (agent && url.endsWith("/" + agent))
      return answer({
        ...agents.find((item) => item.id === agent),
        runtime: { agent_id: agent, desired: "running", observed: "running" },
      });
    if (url.includes("/events?"))
      return new Promise<Response>((_resolve, reject) =>
        options.signal?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        ),
      );
    const method = url.split("/").at(-1)!;
    if (agent && url.includes("/commands/")) {
      const body = JSON.parse(String(options.body));
      commands.push({ agent, method, body });
      if (method === "conversations.list")
        return answer({ items: rooms[agent] });
      if (method === "overview")
        return answer({
          provider_settings: [{ active: true, model: "Local model" }],
        });
      if (method === "conversations.create") {
        if (newFails)
          return answer(
            null,
            { code: "conflict", message: "Conversation creation rejected" },
            409,
          );
        const room = {
          id: "alpha-new",
          title: "Fresh Cedar thread",
          revision: 1,
          archived: false,
        };
        rooms[agent].unshift(room);
        return answer(room);
      }
    }
    throw new Error("Unexpected route " + url);
  });
  start(fetcher);
  await screen.findByText("Agent list temporarily unavailable");
  listFails = false;
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByRole("button", { name: "Earlier Cedar notes" });
  await userEvent.click(
    screen.getByRole("button", { name: "Earlier Cedar notes" }),
  );
  expect(
    screen
      .getByRole("button", { name: "Earlier Cedar notes" })
      .getAttribute("aria-current"),
  ).toBe("page");
  await userEvent.click(
    screen.getByRole("button", { name: "New conversation" }),
  );
  await screen.findByText("Conversation creation rejected");
  expect(
    screen
      .getByRole("button", { name: "Earlier Cedar notes" })
      .getAttribute("aria-current"),
  ).toBe("page");
  newFails = false;
  await userEvent.click(
    screen.getByRole("button", { name: "New conversation" }),
  );
  await screen.findByRole("button", { name: "Fresh Cedar thread" });
  const creations = commands.filter(
    (call) => call.method === "conversations.create",
  );
  expect(creations).toHaveLength(2);
  expect(creations.every((call) => call.agent === "alpha")).toBe(true);
  expect(creations[0].body.request_id).not.toBe(creations[1].body.request_id);
  await userEvent.type(screen.getByLabelText("Message"), "Cedar-only draft");
  await userEvent.click(screen.getByRole("button", { name: "Switch agent" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Maple" }));
  await screen.findByRole("button", { name: "Maple planning" });
  expect(screen.getByLabelText("Message")).toHaveProperty("value", "");
  expect(
    screen.queryByRole("button", { name: "Fresh Cedar thread" }),
  ).toBeNull();
  await userEvent.type(screen.getByLabelText("Message"), "Maple-only draft");
  await userEvent.click(screen.getByRole("button", { name: "Switch agent" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Cedar" }));
  await screen.findByRole("button", { name: "Fresh Cedar thread" });
  expect(screen.getByLabelText("Message")).toHaveProperty(
    "value",
    "Cedar-only draft",
  );
  expect(localStorage.getItem("sturgis:draft:owner:beta:beta-one")).toContain(
    "Maple-only draft",
  );
  await userEvent.click(screen.getByRole("button", { name: "Switch agent" }));
  await userEvent.click(
    screen.getByRole("menuitem", { name: "Create an agent" }),
  );
  expect(
    screen.getByRole("dialog", { name: "Create a new agent" }),
  ).toBeTruthy();
  await userEvent.keyboard("{Escape}");
  await userEvent.click(
    screen.getByRole("button", { name: "Open command palette" }),
  );
  expect(screen.getByRole("dialog", { name: "Go anywhere" })).toBeTruthy();
});
