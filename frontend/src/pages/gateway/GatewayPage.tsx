import { useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  Copy,
  Cpu,
  KeyRound,
  Layers2,
  Network,
  Plus,
  RefreshCw,
  Search,
  Server,
  Trash2,
  X,
} from "lucide-react";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Modal,
  Select,
  Tabs,
} from "../../components/ui";
import type {
  GatewayAuthProvider,
  GatewayAuthAccount,
  GatewayConnection,
  GatewayKey,
  GatewayModel,
  GatewayPreset,
  GatewayRoute,
  GatewayUsage,
} from "../../lib/types";
import { PageHeader, QueryState } from "../shared";
import ProviderDrawer from "./ProviderDrawer";
import NativeAuthDialog from "./NativeAuthDialog";
import { useGatewayAction, useGatewayQuery } from "./useGateway";
import "./gateway.css";

const categories = [
  { id: "all", name: "All providers" },
  { id: "free_tier", name: "Free options" },
  { id: "local", name: "Local" },
  { id: "paid", name: "API providers" },
  { id: "gateway", name: "Gateways" },
  { id: "custom", name: "Custom" },
];
const categoryName = (category: string) =>
  ({
    free: "Free access",
    free_tier: "Free tier available",
    local: "Runs locally",
    paid: "Usage based",
    gateway: "Gateway",
    custom: "Custom endpoint",
  })[category] || category;

/** Gateway administration works without an agent or a running private worker. */
export default function GatewayPage({ accountId }: { accountId: string }) {
  const [tab, setTab] = useState("providers");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [drawer, setDrawer] = useState<{
    preset: GatewayPreset;
    connection?: GatewayConnection;
    authAccount?: { id: string; name: string };
  } | null>(null);
  const [authProvider, setAuthProvider] = useState<GatewayAuthProvider | null>(
    null,
  );
  const authProviders = useGatewayQuery<GatewayAuthProvider[]>(
    accountId,
    "auth/providers",
  );
  const authAccounts = useGatewayQuery<GatewayAuthAccount[]>(
    accountId,
    "auth/accounts",
  );
  const [authError, setAuthError] = useState("");
  function useAccount(providerId: string, id: string, name: string) {
    const preset = presets.data?.find((item) => item.id === providerId);
    if (!preset) {
      setAuthError(
        "Your account is connected. Refresh the provider catalog to import its models.",
      );
      return;
    }
    setAuthProvider(null);
    setDrawer({ preset, authAccount: { id, name } });
  }
  const [removing, setRemoving] = useState<GatewayConnection | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");
  const presets = useGatewayQuery<GatewayPreset[]>(accountId, "presets");
  const connections = useGatewayQuery<GatewayConnection[]>(
    accountId,
    "connections",
  );
  const status = useGatewayQuery<{
    endpoint: string;
    connections: number;
    models: number;
  }>(accountId, "status");
  const action = useGatewayAction(accountId);
  const filtered = (presets.data || []).filter(
    (preset) =>
      (category === "all" ||
        preset.category === category ||
        (category === "free_tier" && preset.category === "free")) &&
      `${preset.name} ${preset.description}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  async function copyEndpoint() {
    try {
      await navigator.clipboard.writeText(status.data!.endpoint);
      setCopied(true);
      setCopyError("");
    } catch {
      setCopyError(
        "Clipboard unavailable. Select and copy the endpoint below.",
      );
    }
  }
  return (
    <div className="page gateway-page">
      <PageHeader
        eyebrow="One connection. Every possibility."
        title="Model Gateway"
        description="Connect your favorite providers once. Use their models across your agents and any compatible app."
      />
      <section className="gateway-endpoint" aria-label="Gateway endpoint">
        <span className="gateway-endpoint-icon">
          <Network size={24} />
        </span>
        <div>
          <span className="gateway-kicker">
            Your OpenAI-compatible endpoint
          </span>
          <code>{status.data?.endpoint || "Loading gateway address…"}</code>
        </div>
        <Button
          variant="ghost"
          size="icon"
          disabled={!status.data}
          aria-label="Copy gateway endpoint"
          onClick={copyEndpoint}
        >
          {copied ? <Check size={18} /> : <Copy size={18} />}
        </Button>
        <div className="gateway-endpoint-stats">
          <strong>{status.data?.models ?? "—"}</strong>
          <span>models available</span>
        </div>
      </section>
      {copyError && (
        <p role="alert" className="gateway-error">
          {copyError}
        </p>
      )}
      <QueryState
        loading={status.isPending}
        error={status.error}
        retry={() => status.refetch()}
      />
      <Tabs
        value={tab}
        onValueChange={setTab}
        items={[
          { value: "providers", label: "Providers" },
          { value: "models", label: "Models" },
          { value: "routes", label: "Model routes" },
          { value: "access", label: "Access & usage" },
        ]}
      />
      {tab === "providers" && (
        <div className="gateway-tab-content">
          <QueryState
            loading={presets.isPending || connections.isPending}
            error={presets.error || connections.error}
            retry={() => {
              void presets.refetch();
              void connections.refetch();
            }}
          />
          {!!authProviders.data?.length && (
            <section aria-labelledby="gateway-signin-title">
              <div className="gateway-section-heading">
                <div>
                  <h2 id="gateway-signin-title">Sign in with your account</h2>
                  <p>
                    Connect a supported provider account directly. Your sign-in
                    stays in the gateway.
                  </p>
                </div>
              </div>
              <div className="gateway-signin-grid">
                {authProviders.data.map((provider) => (
                  <article className="gateway-signin" key={provider.id}>
                    <h3>{provider.name}</h3>
                    <p>{provider.description}</p>
                    <Button onClick={() => setAuthProvider(provider)}>
                      Sign in to {provider.name}
                    </Button>
                  </article>
                ))}
              </div>
              <div className="gateway-native-accounts">
                {authAccounts.data
                  ?.filter((account) => account.status === "connected")
                  .map((account) => (
                    <Button
                      key={account.id}
                      size="sm"
                      onClick={() =>
                        useAccount(
                          account.provider_id,
                          account.id,
                          account.name,
                        )
                      }
                    >
                      <Check size={15} />
                      Use {account.name}
                    </Button>
                  ))}
              </div>
            </section>
          )}
          {(authProviders.error || authAccounts.error || authError) && (
            <div className="gateway-error" role="alert">
              {authError ||
                authProviders.error?.message ||
                authAccounts.error?.message}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setAuthError("");
                  void authProviders.refetch();
                  void authAccounts.refetch();
                  void presets.refetch();
                }}
              >
                Retry account connections
              </Button>
            </div>
          )}
          {!!connections.data?.length && (
            <section aria-labelledby="gateway-connected-title">
              <div className="gateway-section-heading">
                <div>
                  <h2 id="gateway-connected-title">Your connections</h2>
                  <p>Models ready to use through your gateway.</p>
                </div>
                <Badge>{connections.data.length} connections</Badge>
              </div>
              <div className="gateway-connections-grid">
                {connections.data.map((connection) => (
                  <article
                    className="gateway-connection-card"
                    key={connection.id}
                  >
                    <header>
                      <span
                        className="gateway-provider-monogram"
                        aria-hidden="true"
                      >
                        {connection.name.slice(0, 1).toUpperCase()}
                      </span>
                      <div>
                        <h3>{connection.name}</h3>
                        <p>{connection.endpoint}</p>
                      </div>
                      <Badge
                        tone={
                          connection.status === "connected"
                            ? "success"
                            : "warning"
                        }
                      >
                        {connection.status === "connected"
                          ? "Connected"
                          : "Needs attention"}
                      </Badge>
                    </header>
                    <div className="gateway-connected-models">
                      {connection.models.map((model) => (
                        <span key={model.id}>{model.name || model.id}</span>
                      ))}
                    </div>
                    {connection.error && (
                      <p className="gateway-error" role="alert">
                        {connection.error.message}
                      </p>
                    )}
                    <footer>
                      <span className="field-hint">
                        {connection.models.length} models ·{" "}
                        {connection.credential_configured
                          ? "Key secured"
                          : "No key required"}
                      </span>
                      <div className="row">
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Refresh ${connection.name} models`}
                          disabled={action.pending}
                          onClick={() =>
                            action.run(
                              `connections/${encodeURIComponent(connection.id)}/refresh`,
                              { expected_revision: connection.revision },
                            )
                          }
                        >
                          <RefreshCw size={16} />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={!presets.data}
                          onClick={() => {
                            const preset = presets.data?.find(
                              (item) => item.id === connection.preset_id,
                            );
                            if (preset) setDrawer({ preset, connection });
                          }}
                        >
                          Edit
                        </Button>
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Disconnect ${connection.name}`}
                          onClick={() => setRemoving(connection)}
                        >
                          <Trash2 size={16} />
                        </Button>
                      </div>
                    </footer>
                  </article>
                ))}
              </div>
            </section>
          )}
          {action.error && (
            <p className="gateway-error" role="alert">
              {action.error}
            </p>
          )}
          <section aria-labelledby="gateway-catalog-title">
            <div className="gateway-section-heading">
              <div>
                <h2 id="gateway-catalog-title">
                  {connections.data?.length
                    ? "Add another provider"
                    : "Start with a provider"}
                </h2>
                <p>Pick a preset, paste a key, and choose your models.</p>
              </div>
              <div className="gateway-search">
                <Search size={17} />
                <Input
                  aria-label="Search providers"
                  placeholder="Find a provider…"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </div>
            </div>
            <div
              className="gateway-filter-chips"
              aria-label="Provider categories"
            >
              {categories.map((item) => (
                <button
                  key={item.id}
                  aria-pressed={category === item.id}
                  className={category === item.id ? "is-selected" : ""}
                  onClick={() => setCategory(item.id)}
                >
                  {item.name}
                </button>
              ))}
            </div>
            <div className="gateway-preset-grid">
              {filtered.map((preset) => (
                <button
                  className="gateway-preset"
                  key={preset.id}
                  onClick={() => {
                    const provider = authProviders.data?.find(
                      (item) => item.id === preset.id,
                    );
                    if (provider && preset.auth === "oauth")
                      setAuthProvider(provider);
                    else setDrawer({ preset });
                  }}
                  aria-label={`Connect ${preset.name}`}
                >
                  <span
                    className={`gateway-provider-monogram category-${preset.category}`}
                    aria-hidden="true"
                  >
                    {preset.category === "local" ? (
                      <Server size={21} />
                    ) : preset.category === "custom" ? (
                      <Plus size={23} />
                    ) : (
                      preset.name.slice(0, 1).toUpperCase()
                    )}
                  </span>
                  <strong>{preset.name}</strong>
                  <span className="gateway-preset-description">
                    {preset.description}
                  </span>
                  <span className="gateway-preset-category">
                    {categoryName(preset.category)}
                    <Plus size={15} />
                  </span>
                </button>
              ))}
            </div>
            {!filtered.length && !presets.isPending && (
              <EmptyState
                icon={<Search size={25} />}
                title="No providers found"
                description="Try another name or choose a different category."
              />
            )}
            <p className="gateway-catalog-note">
              Free tiers may require an account and have usage limits. Local
              models use your own hardware. Availability comes from each
              provider’s live catalog.
            </p>
          </section>
        </div>
      )}
      {tab === "models" && <ModelsPanel accountId={accountId} />}
      {tab === "routes" && <RoutesPanel accountId={accountId} />}
      {tab === "access" && <AccessPanel accountId={accountId} />}
      {authProvider && (
        <NativeAuthDialog
          accountId={accountId}
          provider={authProvider}
          onClose={() => setAuthProvider(null)}
          onConnected={(id, name) => useAccount(authProvider.id, id, name)}
        />
      )}
      {drawer && (
        <ProviderDrawer
          key={drawer.connection?.id || drawer.preset.id}
          accountId={accountId}
          {...drawer}
          onClose={() => setDrawer(null)}
        />
      )}
      <Modal
        open={!!removing}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        title="Disconnect provider?"
        description={`${removing?.name || "This provider"} and its models will no longer be available through the gateway. Apps using those models will need another connection.`}
      >
        <div className="row">
          <Button onClick={() => setRemoving(null)}>Keep connection</Button>
          <Button
            variant="danger"
            loading={action.pending}
            onClick={async () => {
              if (
                await action.run(
                  `connections/${encodeURIComponent(removing!.id)}`,
                  { expected_revision: removing!.revision },
                  "DELETE",
                )
              )
                setRemoving(null);
            }}
          >
            Disconnect provider
          </Button>
        </div>
        {action.error && (
          <p role="alert" className="gateway-error">
            {action.error}
          </p>
        )}
      </Modal>
    </div>
  );
}

function ModelsPanel({ accountId }: { accountId: string }) {
  const models = useGatewayQuery<GatewayModel[]>(accountId, "models");
  const [search, setSearch] = useState("");
  const [copied, setCopied] = useState("");
  const [error, setError] = useState("");
  const filtered = (models.data || []).filter((model) =>
    `${model.name} ${model.id} ${model.provider_name || ""}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <div className="gateway-tab-content">
      <Card
        title="Your model library"
        description="Use the exact model ID below with your gateway endpoint and an app key."
        actions={
          <div className="gateway-search">
            <Search size={16} />
            <Input
              aria-label="Search available models"
              placeholder="Find a model…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
        }
      >
        <QueryState
          loading={models.isPending}
          error={models.error}
          retry={() => models.refetch()}
        />
        {filtered.map((model) => (
          <article className="record row spread" key={model.id}>
            <div className="gateway-library-model">
              <h3>{model.name}</h3>
              <p className="record-subtitle">
                {model.provider_name}
                {model.tools ? " · Tools supported" : ""}
              </p>
              <code>{model.id}</code>
            </div>
            <Button
              size="sm"
              variant="ghost"
              aria-label={`Copy model ID ${model.id}`}
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(model.id);
                  setCopied(model.id);
                  setError("");
                } catch {
                  setError(
                    "Clipboard unavailable. Select and copy the model ID shown here.",
                  );
                }
              }}
            >
              {copied === model.id ? <Check size={15} /> : <Copy size={15} />}
              {copied === model.id ? "Copied" : "Copy ID"}
            </Button>
          </article>
        ))}
        {!filtered.length && !models.isPending && (
          <EmptyState
            icon={<Cpu size={25} />}
            title={search ? "No models found" : "Your models will live here"}
            description={
              search
                ? "Try another model or provider name."
                : "Connect a provider and import its models to make them available to your apps."
            }
          />
        )}
        {error && (
          <p className="gateway-error" role="alert">
            {error}
          </p>
        )}
      </Card>
    </div>
  );
}

function RoutesPanel({ accountId }: { accountId: string }) {
  const routes = useGatewayQuery<GatewayRoute[]>(accountId, "routes");
  const models = useGatewayQuery<GatewayModel[]>(accountId, "models");
  const [editing, setEditing] = useState<GatewayRoute | "new" | null>(null);
  return (
    <div className="gateway-tab-content">
      <Card
        title="Give your apps a dependable model name"
        description="A route combines models behind one name. Choose a fallback order or share requests across them."
        actions={
          <Button
            variant="primary"
            onClick={() => setEditing("new")}
            disabled={!models.data?.length}
          >
            <Plus size={17} />
            Create route
          </Button>
        }
      >
        <QueryState
          loading={routes.isPending || models.isPending}
          error={routes.error || models.error}
          retry={() => {
            void routes.refetch();
            void models.refetch();
          }}
        />
        {routes.data?.length
          ? routes.data.map((route) => (
              <article className="record" key={route.id}>
                <div className="row spread">
                  <div>
                    <h3>{route.name}</h3>
                    <p className="record-subtitle">
                      {route.id} ·{" "}
                      {route.strategy === "priority"
                        ? "Priority fallback"
                        : "Round robin"}
                    </p>
                  </div>
                  <Button size="sm" onClick={() => setEditing(route)}>
                    Edit route
                  </Button>
                </div>
                <ol className="gateway-route-sequence">
                  {route.models.map((model) => (
                    <li key={model}>{model}</li>
                  ))}
                </ol>
              </article>
            ))
          : !routes.isPending && (
              <EmptyState
                icon={<Layers2 size={25} />}
                title="A plan B, built in"
                description="Connect at least one provider, then create a route to choose the models your apps can fall back to."
              />
            )}
      </Card>
      {editing && (
        <RouteEditor
          accountId={accountId}
          route={editing === "new" ? undefined : editing}
          models={(models.data || []).filter((model) => !!model.connection_id)}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function RouteEditor({
  accountId,
  route,
  models,
  onClose,
}: {
  accountId: string;
  route?: GatewayRoute;
  models: GatewayModel[];
  onClose: () => void;
}) {
  const action = useGatewayAction(accountId);
  const [name, setName] = useState(route?.name || "");
  const [id, setId] = useState(route?.id || "");
  const [strategy, setStrategy] = useState(route?.strategy || "priority");
  const [selected, setSelected] = useState(route?.models || []);
  function move(index: number, delta: number) {
    setSelected((current) => {
      const result = [...current];
      [result[index], result[index + delta]] = [
        result[index + delta],
        result[index],
      ];
      return result;
    });
  }
  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={route ? "Edit model route" : "Create model route"}
      description="Apps use this route name in place of a model. Models are tried in the order below."
    >
      <form
        className="gateway-fields"
        onSubmit={async (event) => {
          event.preventDefault();
          if (
            await action.run("routes", {
              id: id.trim(),
              name: name.trim(),
              strategy,
              models: selected,
              expected_revision: route?.revision || 0,
            })
          )
            onClose();
        }}
      >
        <label>
          Route name
          <Input
            value={name}
            required
            onChange={(event) => {
              setName(event.target.value);
              if (!route)
                setId(
                  event.target.value
                    .toLowerCase()
                    .replace(/[^a-z0-9_-]+/g, "-")
                    .replace(/^-|-$/g, ""),
                );
            }}
            placeholder="Everyday assistant"
          />
        </label>
        <label>
          Model alias
          <Input
            value={id}
            required
            disabled={!!route}
            onChange={(event) => setId(event.target.value)}
            placeholder="everyday"
          />
        </label>
        <Select
          label="Routing strategy"
          value={strategy}
          onValueChange={(value) =>
            setStrategy(value as GatewayRoute["strategy"])
          }
          items={[
            { value: "priority", label: "Priority fallback" },
            { value: "round_robin", label: "Round robin" },
          ]}
        />
        <div className="gateway-route-picker">
          {models.map((model) => (
            <label className="row" key={model.id}>
              <input
                type="checkbox"
                checked={selected.includes(model.id)}
                onChange={() =>
                  setSelected((current) =>
                    current.includes(model.id)
                      ? current.filter((item) => item !== model.id)
                      : [...current, model.id],
                  )
                }
              />
              <span>
                {model.provider_name} · {model.name}
              </span>
            </label>
          ))}
        </div>
        <ol className="gateway-route-sequence">
          {selected.map((model, index) => (
            <li key={model}>
              <span>{model}</span>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Move ${model} up`}
                disabled={index === 0}
                onClick={() => move(index, -1)}
              >
                <ArrowUp size={15} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Move ${model} down`}
                disabled={index === selected.length - 1}
                onClick={() => move(index, 1)}
              >
                <ArrowDown size={15} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove ${model}`}
                onClick={() =>
                  setSelected((current) =>
                    current.filter((item) => item !== model),
                  )
                }
              >
                <X size={15} />
              </Button>
            </li>
          ))}
        </ol>
        {action.error && (
          <p role="alert" className="gateway-error">
            {action.error}
          </p>
        )}
        <Button
          type="submit"
          variant="primary"
          loading={action.pending}
          disabled={!selected.length || !id.trim() || !name.trim()}
        >
          Save route
        </Button>
      </form>
    </Modal>
  );
}

function AccessPanel({ accountId }: { accountId: string }) {
  const keys = useGatewayQuery<GatewayKey[]>(accountId, "keys");
  const usage = useGatewayQuery<GatewayUsage>(accountId, "usage");
  const action = useGatewayAction(accountId);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState("");
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");
  const [revoking, setRevoking] = useState<GatewayKey | null>(null);
  return (
    <div className="gateway-tab-content">
      <div className="gateway-usage-grid">
        {[
          { label: "Requests", value: usage.data?.requests },
          { label: "Tokens used", value: usage.data?.total_tokens },
          { label: "Failed requests", value: usage.data?.failures },
        ].map((metric) => (
          <div className="gateway-usage-metric" key={metric.label}>
            <span>{metric.label}</span>
            <strong>{metric.value?.toLocaleString() ?? "—"}</strong>
          </div>
        ))}
      </div>
      <Card
        title="Keys for your apps"
        description="Give each app its own gateway key. Your upstream provider keys stay private."
        actions={
          <Button
            variant="primary"
            onClick={() => {
              setCreating(true);
              setName("");
            }}
          >
            <Plus size={16} />
            Create API key
          </Button>
        }
      >
        <QueryState
          loading={keys.isPending}
          error={keys.error}
          retry={() => keys.refetch()}
        />
        {keys.data?.length
          ? keys.data.map((key) => (
              <article className="record" key={key.id}>
                <div className="row spread">
                  <div className="row">
                    <KeyRound size={18} />
                    <div>
                      <h3>{key.name}</h3>
                      <code>{key.prefix}…</code>
                    </div>
                  </div>
                  <div className="row">
                    <Badge tone={key.revoked ? "neutral" : "success"}>
                      {key.revoked ? "Revoked" : "Active"}
                    </Badge>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={key.revoked || action.pending}
                      onClick={() => setRevoking(key)}
                    >
                      Revoke
                    </Button>
                  </div>
                </div>
              </article>
            ))
          : !keys.isPending && (
              <EmptyState
                icon={<KeyRound size={24} />}
                title="Connect any compatible app"
                description="Create a key, then paste it into your app along with the gateway endpoint and a model name."
              />
            )}
        {action.error && (
          <p role="alert" className="gateway-error">
            {action.error}
          </p>
        )}
      </Card>
      <Card
        title="Recent requests"
        description="Gateway activity across all your connected apps."
      >
        <QueryState
          loading={usage.isPending}
          error={usage.error}
          retry={() => usage.refetch()}
        />
        {usage.data?.recent.length
          ? usage.data.recent.map((request, index) => (
              <div className="record row spread" key={index}>
                <div>
                  <strong>{request.model}</strong>
                  <p className="record-subtitle">
                    {request.tokens.toLocaleString()} tokens
                  </p>
                </div>
                <Badge
                  tone={
                    request.status === "ok" || request.status === "success"
                      ? "success"
                      : "neutral"
                  }
                >
                  {request.status}
                </Badge>
              </div>
            ))
          : !usage.isPending && (
              <EmptyState
                icon={<Cpu size={25} />}
                title="Ready when you are"
                description="Requests will appear here when an agent or app uses your gateway."
              />
            )}
      </Card>
      <Modal
        open={creating}
        onOpenChange={(open) => {
          setCreating(open);
          if (!open) setName("");
        }}
        title="Create a gateway key"
        description="Use a name that helps you recognize the app using this key."
      >
        <form
          className="gateway-fields"
          onSubmit={async (event) => {
            event.preventDefault();
            const result = await action.run<GatewayKey & { key: string }>(
              "keys",
              { name: name.trim() },
            );
            if (result) {
              setSecret(result.key);
              setCreating(false);
              setName("");
              setCopied(false);
              setCopyError("");
            }
          }}
        >
          <label>
            Key name
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="My coding app"
              required
            />
          </label>
          {action.error && (
            <p role="alert" className="gateway-error">
              {action.error}
            </p>
          )}
          <Button
            type="submit"
            variant="primary"
            loading={action.pending}
            disabled={!name.trim()}
          >
            Generate key
          </Button>
        </form>
      </Modal>
      <Modal
        open={!!secret}
        onOpenChange={(open) => {
          if (!open) setSecret("");
        }}
        title="Your key is ready"
        description="Copy it now. This is the only time the complete key is shown."
      >
        <label className="gateway-fields">
          Gateway API key
          <Input readOnly value={secret} autoComplete="off" />
        </label>
        <div className="row mt-4">
          <Button
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(secret);
                setCopied(true);
              } catch {
                setCopyError(
                  "Clipboard unavailable. Select and copy the key above.",
                );
              }
            }}
          >
            {copied ? <Check size={16} /> : <Copy size={16} />}
            {copied ? "Copied" : "Copy key"}
          </Button>
          <Button variant="primary" onClick={() => setSecret("")}>
            Done
          </Button>
        </div>
        {copyError && (
          <p role="alert" className="gateway-error">
            {copyError}
          </p>
        )}
      </Modal>
      <Modal
        open={!!revoking}
        onOpenChange={(open) => {
          if (!open) setRevoking(null);
        }}
        title="Revoke this API key?"
        description={`Apps using ${revoking?.name || "this key"} will lose gateway access immediately.`}
      >
        <div className="row">
          <Button onClick={() => setRevoking(null)}>Keep key</Button>
          <Button
            variant="danger"
            loading={action.pending}
            onClick={async () => {
              if (
                await action.run(
                  `keys/${encodeURIComponent(revoking!.id)}`,
                  {},
                  "DELETE",
                )
              )
                setRevoking(null);
            }}
          >
            Revoke key
          </Button>
        </div>
        {action.error && (
          <p role="alert" className="gateway-error">
            {action.error}
          </p>
        )}
      </Modal>
    </div>
  );
}
