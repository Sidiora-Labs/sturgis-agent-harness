import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, m } from "motion/react";
import {
  ArrowRight,
  Check,
  LoaderCircle,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import { api, ApiError, command } from "./lib/api";
import type {
  Account,
  Agent,
  Conversation,
  Health,
  Page,
  RuntimeState,
} from "./lib/types";
import {
  AgentContext,
  useRuntimeState,
  canWakeRuntime,
} from "./hooks/useAgent";
import { Shell, BrandMark } from "./components/Shell";
import type { ViewName } from "./components/Shell";
import { Button, Input, Modal, Skeleton } from "./components/ui";
const Conversation = lazy(() => import("./pages/ConversationPage"));
const Work = lazy(() => import("./pages/WorkPage"));
const Knowledge = lazy(() => import("./pages/KnowledgePage"));
const Files = lazy(() => import("./pages/FilesPage"));
const Connections = lazy(() => import("./pages/ConnectionsPage"));
const Settings = lazy(() => import("./pages/SettingsPage"));
const ModelGateway = lazy(() => import("./pages/gateway/GatewayPage"));
type Installation = {
  health: Health;
  bootstrap: boolean;
  allowSignup: boolean;
  user: Account | null;
};
const views: ViewName[] = [
  "Conversation",
  "Work",
  "Knowledge",
  "Files",
  "Connections",
  "Settings",
  "Model Gateway",
];
export default function App() {
  const client = useQueryClient();
  const boot = useQuery({
    queryKey: ["installation"],
    queryFn: async ({ signal }): Promise<Installation> => {
      const [health, status] = await Promise.all([
        api<Health>("/health", undefined, { signal }),
        api<{ bootstrap_required: boolean; allow_signup: boolean }>(
          "/bootstrap",
          undefined,
          {
            signal,
          },
        ),
      ]);
      let user: Account | null = null;
      if (!status.bootstrap_required) {
        try {
          user = await api<Account>("/auth/me", undefined, { signal });
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 401) throw error;
        }
      }
      return {
        health,
        bootstrap: status.bootstrap_required,
        allowSignup: !!status.allow_signup,
        user,
      };
    },
    staleTime: Infinity,
  });
  const user = boot.data?.user;
  const agents = useQuery({
    queryKey: ["agents", user?.id],
    queryFn: ({ signal }) => api<Page<Agent>>("/agents", undefined, { signal }),
    enabled: !!user,
  });
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [view, setView] = useState<ViewName>("Conversation");
  const [creating, setCreating] = useState(false);
  const [palette, setPalette] = useState(false);
  const [search, setSearch] = useState("");
  const runtime = useRuntimeState(user?.id, agentId);
  const conversations = useQuery({
    queryKey: ["agent", agentId, "conversations.list", {}],
    queryFn: ({ signal }) => {
      if (
        !canWakeRuntime(
          client.getQueryData<RuntimeState>(["runtime", user?.id, agentId]),
        )
      )
        throw new ApiError(
          "Resume the agent before reading conversations.",
          "runtime_paused",
          409,
        );
      return command<Page<Conversation>>(
        agentId!,
        "conversations.list",
        {},
        { signal },
      );
    },
    enabled: !!user && !!agentId && canWakeRuntime(runtime.data),
    staleTime: 15000,
  });
  const [connection, setConnection] = useState<
    "live" | "reconnecting" | "idle"
  >("idle");
  const [toasts, setToasts] = useState<
    {
      id: number;
      message: string;
      tone: "success" | "error";
      expiresAt: number;
    }[]
  >([]);
  const notify = useCallback(
    (message: string, tone: "success" | "error" = "success") => {
      setToasts((previous) => [
        ...previous.slice(-2),
        {
          id: Date.now() + Math.random(),
          message,
          tone,
          expiresAt: Date.now() + (tone === "error" ? 9000 : 4500),
        },
      ]);
    },
    [],
  );
  useEffect(() => {
    if (!toasts.length) return;
    const timer = setTimeout(
      () =>
        setToasts((previous) =>
          previous.filter((toast) => toast.expiresAt > Date.now()),
        ),
      Math.max(
        0,
        Math.min(...toasts.map((toast) => toast.expiresAt)) - Date.now(),
      ),
    );
    return () => clearTimeout(timer);
  }, [toasts]);
  useEffect(() => {
    if (agents.data && !agents.data.items.some((a) => a.id === agentId))
      setAgentId(agents.data.items[0]?.id || null);
  }, [agents.data, agentId]);
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "k" && user) {
        event.preventDefault();
        setPalette((open) => !open);
      }
    };
    document.addEventListener("keydown", handle);
    return () => document.removeEventListener("keydown", handle);
  }, [user]);
  const authenticate = (account: Account) =>
    client.setQueryData<Installation>(["installation"], (previous) => ({
      ...previous!,
      bootstrap: false,
      user: account,
    }));
  const signOut = async () => {
    try {
      await api("/auth/logout", {});
      client.clear();
      setAgentId(null);
      setView("Conversation");
      setToasts([]);
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };
  const createAgent = async (name: string) => {
    const created = await api<Agent>("/agents", { name });
    await client.invalidateQueries({ queryKey: ["agents"] });
    setAgentId(created.id);
    setCreating(false);
    setView("Conversation");
    notify("Your new space is ready. Connect a model to begin.");
  };
  const navigate = (next: ViewName) => {
    setView(next);
    setPalette(false);
    setSearch("");
  };
  const current = agents.data?.items.find((agent) => agent.id === agentId);
  let content;
  if (boot.isPending)
    content = (
      <div className="auth-screen">
        <div className="auth-story">
          <Brand />
          <Skeleton className="h-40" />
        </div>
        <div className="auth-form-wrap">
          <LoaderCircle
            className="animate-spin text-accent"
            aria-label="Opening Sturgis"
          />
        </div>
      </div>
    );
  else if (boot.error)
    content = (
      <div className="empty-install">
        <Brand />
        <h1>Unable to open this space</h1>
        <p>{boot.error.message}</p>
        <Button onClick={() => boot.refetch()}>Reconnect</Button>
      </div>
    );
  else if (!user)
    content = (
      <Auth
        bootstrap={boot.data!.bootstrap}
        allowSignup={boot.data!.allowSignup}
        onAuthenticated={authenticate}
      />
    );
  else
    content = (
      <Shell
        view={view}
        onNavigate={navigate}
        agents={agents.data?.items || []}
        agentId={agentId}
        onAgentChange={(id) => {
          setAgentId(id);
          setConversationId(null);
          setConnection("idle");
        }}
        onNewAgent={() => setCreating(true)}
        user={user}
        onSignOut={signOut}
        mode={boot.data!.health.mode}
        connection={connection}
        onCommandPalette={() => setPalette(true)}
        conversations={conversations.data?.items || []}
        conversationId={
          conversationId || conversations.data?.items[0]?.id || null
        }
        onConversationSelect={(id) => {
          setConversationId(id);
          setView("Conversation");
        }}
        onNewConversation={
          agentId && canWakeRuntime(runtime.data)
            ? async () => {
                try {
                  const room = await command<Conversation>(
                    agentId,
                    "conversations.create",
                  );
                  client.setQueryData<Page<Conversation>>(
                    ["agent", agentId, "conversations.list", {}],
                    (previous) => ({
                      ...previous,
                      items: [room, ...(previous?.items || [])],
                    }),
                  );
                  setConversationId(room.id);
                  setView("Conversation");
                  await client.invalidateQueries({
                    queryKey: ["agent", agentId, "conversations.list"],
                  });
                } catch (error) {
                  notify((error as Error).message, "error");
                }
              }
            : undefined
        }
      >
        {view === "Model Gateway" ? (
          <Suspense fallback={<PageLoading />}>
            <ModelGateway accountId={user.id} />
          </Suspense>
        ) : agents.isPending ? (
          <PageLoading />
        ) : agents.error ? (
          <div className="page-error">
            <p>{agents.error.message}</p>
            <Button onClick={() => agents.refetch()}>Try again</Button>
          </div>
        ) : !current ? (
          <div className="empty-install">
            <div className="eyebrow">A new beginning</div>
            <h1>A space for your best work.</h1>
            <p>
              Create an agent that keeps your conversations, knowledge, and
              ongoing work together. You choose its model and its permissions.
            </p>
            <CreateAgentForm onCreate={createAgent} />
          </div>
        ) : (
          <AgentContext.Provider
            value={{ agentId: current.id, accountId: user.id, notify }}
          >
            <Suspense fallback={<PageLoading />}>
              <div key={view + current.id} className="page-enter">
                {view === "Conversation" ? (
                  <Conversation
                    selectedConversation={conversationId}
                    onSelect={setConversationId}
                    onConnection={setConnection}
                    onConfigure={() => setView("Settings")}
                  />
                ) : view === "Work" ? (
                  <Work />
                ) : view === "Knowledge" ? (
                  <Knowledge />
                ) : view === "Files" ? (
                  <Files />
                ) : view === "Connections" ? (
                  <Connections />
                ) : (
                  <Settings
                    agent={current}
                    onOpenGateway={() => navigate("Model Gateway")}
                    onChanged={() =>
                      client.invalidateQueries({ queryKey: ["agents"] })
                    }
                    onCreate={() => setCreating(true)}
                    onArchived={async () => {
                      setAgentId(null);
                      await client.invalidateQueries({ queryKey: ["agents"] });
                      setView("Conversation");
                    }}
                  />
                )}
              </div>
            </Suspense>
          </AgentContext.Provider>
        )}
      </Shell>
    );
  return (
    <>
      {content}
      <Modal
        open={creating}
        onOpenChange={setCreating}
        title="Create a new agent"
        description="An independent space for a different part of your life or work."
      >
        <CreateAgentForm onCreate={createAgent} />
      </Modal>
      <Modal
        open={palette}
        onOpenChange={setPalette}
        title="Go anywhere"
        description="Navigate your space without leaving the keyboard."
      >
        <div className="row">
          <Search size={17} />
          <Input
            aria-label="Search your space"
            placeholder="Search views…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="command-list">
          {views
            .filter((name) => name.toLowerCase().includes(search.toLowerCase()))
            .map((name) => (
              <button
                className="command-item"
                key={name}
                onClick={() => navigate(name)}
              >
                <span>{name}</span>
                <ArrowRight size={15} />
              </button>
            ))}
        </div>
      </Modal>
      <div className="notice-region" aria-live="polite">
        <AnimatePresence>
          {toasts.map((toast) => (
            <m.div
              className={"toast " + (toast.tone === "error" ? "error" : "")}
              key={toast.id}
              initial={{ y: 8 }}
              animate={{ y: 0 }}
              exit={{ y: 4 }}
              transition={{ duration: 0.16 }}
              role={toast.tone === "error" ? "alert" : "status"}
            >
              {toast.tone === "success" ? <Check size={16} /> : <X size={16} />}
              <span>{toast.message}</span>
              <button
                aria-label="Dismiss notification"
                onClick={() =>
                  setToasts((previous) =>
                    previous.filter((t) => t.id !== toast.id),
                  )
                }
              >
                <X size={14} />
              </button>
            </m.div>
          ))}
        </AnimatePresence>
      </div>
    </>
  );
}
function Brand() {
  return (
    <div className="auth-logo">
      <span className="auth-logo-icon">
        <BrandMark />
      </span>
      sturgis
    </div>
  );
}
function PageLoading() {
  return (
    <div className="page stack" aria-label="Loading view">
      <Skeleton className="h-10 w-64" />
      <Skeleton className="h-5 w-96 max-w-full" />
      <div className="page-grid">
        <Skeleton className="h-72" />
        <Skeleton className="h-72" />
      </div>
    </div>
  );
}
function Auth({
  bootstrap,
  allowSignup,
  onAuthenticated,
}: {
  bootstrap: boolean;
  allowSignup: boolean;
  onAuthenticated: (user: Account) => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [signup, setSignup] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    setPending(true);
    setError("");
    try {
      const session = await api<{ principal: Account }>(
        bootstrap
          ? "/bootstrap"
          : signup && allowSignup
            ? "/auth/signup"
            : "/auth/login",
        data,
      );
      onAuthenticated(session.principal);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setPending(false);
    }
  }
  return (
    <main className="auth-screen">
      <section className="auth-story">
        <Brand />
        <div>
          <div className="eyebrow">Intelligence, with continuity</div>
          <h1>
            Good work deserves
            <br />a place to continue.
          </h1>
          <p>
            A thoughtful collaborator. A private space. Your ideas,
            conversations, and ambitions — connected.
          </p>
        </div>
        <div className="auth-footnote">
          YOUR AGENT. YOUR MODELS. YOUR SPACE.
        </div>
      </section>
      <section className="auth-form-wrap">
        <div className="auth-form">
          <Sparkles size={25} className="text-accent mb-6" />
          <h2>
            {bootstrap || signup ? "Make yourself at home." : "Welcome back."}
          </h2>
          <p>
            {bootstrap
              ? "Create your owner account to begin."
              : signup
                ? "Create your private space on this installation."
                : "Pick up where your thinking left off."}
          </p>
          <form onSubmit={submit}>
            <label className="field">
              Email
              <Input
                name="email"
                type="email"
                autoComplete="username"
                required
                placeholder="you@example.com"
              />
            </label>
            <label className="field">
              Password
              <Input
                name="password"
                type="password"
                autoComplete={
                  bootstrap || signup ? "new-password" : "current-password"
                }
                minLength={12}
                required
                placeholder="At least 12 characters"
              />
            </label>
            {bootstrap && (
              <label className="field">
                Installation token
                <Input
                  aria-label="Installation token"
                  aria-describedby="installation-token-help"
                  name="token"
                  type="password"
                  autoComplete="off"
                  required
                />
                <span id="installation-token-help" className="field-hint">
                  Available in your installation’s private data directory.
                </span>
              </label>
            )}
            {error && (
              <p role="alert" className="text-sm text-rose-700">
                {error}
              </p>
            )}
            <Button type="submit" loading={pending}>
              {bootstrap
                ? "Create owner account"
                : signup
                  ? "Create account"
                  : "Sign in"}
              <ArrowRight size={15} />
            </Button>
          </form>
          {!bootstrap && allowSignup && (
            <Button
              variant="ghost"
              disabled={pending}
              onClick={() => {
                setSignup(!signup);
                setError("");
              }}
            >
              {signup
                ? "Already have an account? Sign in"
                : "Create an account"}
            </Button>
          )}
          <p className="auth-note">
            Connected directly to your Sturgis installation.
            <br />
            Your workspace belongs to you.
          </p>
        </div>
      </section>
    </main>
  );
}
function CreateAgentForm({
  onCreate,
}: {
  onCreate: (name: string) => Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  return (
    <form
      className="stack"
      onSubmit={async (event) => {
        event.preventDefault();
        const name = String(new FormData(event.currentTarget).get("name"));
        setPending(true);
        setError("");
        try {
          await onCreate(name);
        } catch (error) {
          setError((error as Error).message);
        } finally {
          setPending(false);
        }
      }}
    >
      <label className="field">
        Agent name
        <Input
          name="name"
          defaultValue="My agent"
          maxLength={160}
          required
          autoFocus
        />
      </label>
      {error && (
        <p role="alert" className="text-sm text-rose-700">
          {error}
        </p>
      )}
      <Button type="submit" loading={pending}>
        Create agent
        <ArrowRight size={15} />
      </Button>
    </form>
  );
}
