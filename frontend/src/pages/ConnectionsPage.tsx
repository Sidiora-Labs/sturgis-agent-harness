import { useState, type FormEvent } from "react";
import {
  AudioLines,
  Check,
  Copy,
  Globe2,
  Image,
  KeyRound,
  Link2,
  Mic,
  Pencil,
  Plug,
  Plus,
  Radio,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Smartphone,
  Unplug,
} from "lucide-react";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Modal,
  Select,
  Skeleton,
  Tabs,
  Textarea,
  Tooltip,
  type BadgeTone,
} from "../components/ui";
import { useAction, useAgent, useAgentQuery } from "../hooks/useAgent";
import type { Connection, Credential, Params } from "../lib/types";
import "./work-connections.css";

type ConnectionRow = Connection & {
  probe?: {
    ok?: boolean;
    kind?: string;
    error?: { code?: string; message?: string };
    actuator_verified?: boolean;
  } | null;
};
type Pairing = {
  connection_id: string;
  pairing_token: string;
  expires_at: number;
  revision: number;
};
const kinds = [
  {
    value: "http",
    label: "Web fetch",
    icon: Globe2,
    operation: "fetch",
    access: "Fetch web content",
  },
  {
    value: "search",
    label: "Web search",
    icon: Search,
    operation: "query",
    access: "Search for information",
  },
  {
    value: "mcp",
    label: "App server (MCP)",
    icon: Plug,
    operation: "call",
    access: "Call the allowed app tools",
  },
  {
    value: "browser",
    label: "Managed browser",
    icon: Globe2,
    operation: "inspect",
    access: "Inspect pages in the isolated browser",
  },
  {
    value: "stt",
    label: "Speech to text",
    icon: Mic,
    operation: "transcribe",
    access: "Transcribe audio attachments",
  },
  {
    value: "tts",
    label: "Text to speech",
    icon: AudioLines,
    operation: "speak",
    access: "Generate spoken audio",
  },
  {
    value: "image",
    label: "Image generation",
    icon: Image,
    operation: "generate",
    access: "Generate images",
  },
  {
    value: "webhook",
    label: "Incoming webhook",
    icon: Radio,
    operation: "receive",
    access: "Receive authenticated external events",
  },
  {
    value: "device",
    label: "Paired device",
    icon: Smartphone,
    operation: "request",
    access: "Request the declared device operations",
  },
];
const labels: Record<string, string> = {
  ready: "Ready",
  ungranted: "Choose access",
  unready: "Needs test",
  unavailable: "Unavailable",
  revoked: "Revoked",
  stale_credential: "Credential changed",
};
const statusTone = (status: string): BadgeTone =>
  status === "ready"
    ? "success"
    : status === "revoked"
      ? "neutral"
      : status === "unavailable" || status === "stale_credential"
        ? "danger"
        : "warning";
const textOption = (value: unknown) => (typeof value === "string" ? value : "");

export default function ConnectionsPage() {
  const { agentId, notify } = useAgent();
  const query = useAgentQuery<ConnectionRow[]>("connections.list");
  const credentials = useAgentQuery<Credential[]>("providers.credentials.list");
  const { execute, pending } = useAction();
  const [tab, setTab] = useState("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<ConnectionRow | "new" | null>(null);
  const [kind, setKind] = useState("http");
  const [credentialRef, setCredentialRef] = useState("none");
  const [error, setError] = useState("");
  const [grant, setGrant] = useState<ConnectionRow | null>(null);
  const [grantSelected, setGrantSelected] = useState(false);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [copied, setCopied] = useState(false);
  const existing = editing && editing !== "new" ? editing : null;
  function edit(record: ConnectionRow | "new") {
    setKind(record === "new" ? "http" : record.kind);
    setCredentialRef(
      record === "new" ? "none" : record.credential_ref || "none",
    );
    setError("");
    setEditing(record);
  }
  async function configure(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = new FormData(event.currentTarget);
    setError("");
    let options: Params;
    try {
      const parsed: unknown = JSON.parse(String(form.get("options") || "{}"));
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        throw new Error();
      options = parsed as Params;
    } catch {
      setError("Advanced options must be a JSON object.");
      return;
    }
    if (kind === "mcp") {
      options.allowed_tools = String(form.get("allowed_tools"))
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean);
      if (!(options.allowed_tools as string[]).length) {
        setError("Name at least one app tool this connection may call.");
        return;
      }
    }
    if (["stt", "tts", "image"].includes(kind)) {
      const model = String(form.get("model") || "").trim();
      const voice = String(form.get("voice") || "").trim();
      if (model) options.model = model;
      else delete options.model;
      if (voice) options.voice = voice;
      else delete options.voice;
    }
    if (kind === "device") {
      options.device_id = String(form.get("device_id"));
      const names = String(form.get("device_operations"))
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean);
      if (!names.length) {
        setError("Name at least one operation your device supports.");
        return;
      }
      const schemas =
        options.operations &&
        typeof options.operations === "object" &&
        !Array.isArray(options.operations)
          ? (options.operations as Params)
          : {};
      options.operations = Object.fromEntries(
        names.map((name) => [
          name,
          schemas[name] || { type: "object", additionalProperties: false },
        ]),
      );
    }
    const params = {
      id: String(form.get("id")),
      kind,
      endpoint: String(form.get("endpoint")),
      credential_ref: credentialRef === "none" ? null : credentialRef,
      options,
      expected_revision: existing?.revision || 0,
    };
    const result = await execute<ConnectionRow>(
      "connections.configure",
      params,
      existing
        ? "Connection updated. Review access and test its new configuration."
        : "Connection configured. Choose access, then test readiness.",
    );
    if (result) setEditing(null);
  }
  async function testConnection(connection: ConnectionRow) {
    const result = await execute<ConnectionRow>("connections.probe", {
      id: connection.id,
      expected_revision: connection.revision,
    });
    if (result)
      notify(
        result.status === "ready"
          ? `${connection.id} is ready.`
          : result.probe?.error?.code === "browser_unavailable"
            ? "A managed browser is not available in this runtime."
            : `${connection.id} needs attention. Inspect its readiness evidence.`,
        result.status === "ready" ? "success" : "error",
      );
  }
  async function pairDevice(connection: ConnectionRow) {
    const result = await execute<Pairing>("devices.pair", {
      connection_id: connection.id,
      expected_revision: connection.revision,
    });
    if (result) {
      setCopied(false);
      setPairing(result);
    }
  }
  const all = query.data || [];
  const visible = all.filter(
    (connection) =>
      (tab === "all" ||
        (tab === "ready"
          ? connection.status === "ready"
          : connection.status !== "ready")) &&
      (connection.id + " " + connection.kind + " " + connection.endpoint)
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const credentialItems = [
    { value: "none", label: "No credential" },
    ...(credentials.data || [])
      .filter((item) => item.active)
      .map((item) => ({ value: item.reference, label: item.name })),
  ];
  if (
    credentialRef !== "none" &&
    !credentialItems.some((item) => item.value === credentialRef)
  )
    credentialItems.push({
      value: credentialRef,
      label: "Previous credential · needs review",
    });
  let content;
  if (query.isPending)
    content = (
      <div className="stack">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  else if (query.error)
    content = (
      <div className="page-error">
        <h2>Connections could not be loaded</h2>
        <p>{query.error.message}</p>
        <Button onClick={() => query.refetch()}>Try again</Button>
      </div>
    );
  else
    content = (
      <Card
        title="Your connections"
        description="Configuration, granted access, and a successful readiness test each have their own place."
      >
        {!all.length ? (
          <EmptyState
            icon={<Plug />}
            title="Open a thoughtful connection"
            description="Bring in a source, an app, or a device. You choose the access your agent receives."
            action={
              <Button variant="primary" onClick={() => edit("new")}>
                <Plus size={16} />
                Add connection
              </Button>
            }
          />
        ) : !visible.length ? (
          <EmptyState
            icon={<Search />}
            title="No matching connections"
            description="Try another search or change the status filter."
          />
        ) : (
          <div className="wc-connections">
            {visible.map((connection) => {
              const catalog = kinds.find(
                (item) => item.value === connection.kind,
              );
              const Icon = catalog?.icon || Link2;
              return (
                <article key={connection.id} className="wc-connection">
                  <div className="wc-connection-main">
                    <span className="wc-connection-icon" aria-hidden="true">
                      <Icon size={22} strokeWidth={1.6} />
                    </span>
                    <div className="wc-connection-name">
                      <div className="wc-record-heading">
                        <h3>{connection.id}</h3>
                        <Badge tone={statusTone(connection.status)}>
                          {labels[connection.status] || connection.status}
                        </Badge>
                      </div>
                      <p>
                        {catalog?.label || connection.kind} ·{" "}
                        {connection.endpoint}
                      </p>
                    </div>
                    <Tooltip label="Edit connection">
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Edit connection ${connection.id}`}
                        onClick={() => edit(connection)}
                      >
                        <Pencil size={17} />
                      </Button>
                    </Tooltip>
                  </div>
                  <div className="wc-connection-access">
                    <ShieldCheck size={15} aria-hidden="true" />
                    <span>
                      {connection.grants.length
                        ? connection.grants.join(", ")
                        : "No access granted"}
                    </span>
                    {connection.credential_ref && (
                      <span className="wc-credential-note">
                        <KeyRound size={13} />
                        Credential attached
                      </span>
                    )}
                  </div>
                  {connection.kind === "browser" &&
                    connection.probe?.error?.code === "browser_unavailable" && (
                      <p className="wc-availability">
                        The managed browser is unavailable in this runtime. Your
                        operator can enable an isolated browser backend.
                      </p>
                    )}
                  {connection.kind === "device" && connection.probe?.ok && (
                    <p className="wc-availability">
                      Pairing is authenticated. Device-reported results retain
                      their external evidence label.
                    </p>
                  )}
                  <div className="record-actions">
                    <Button
                      size="sm"
                      disabled={pending}
                      onClick={() => {
                        setGrant(connection);
                        setGrantSelected(
                          connection.grants.includes(catalog?.operation || ""),
                        );
                      }}
                    >
                      <SlidersHorizontal size={15} />
                      Manage grants
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={
                        pending ||
                        !connection.active ||
                        !connection.grants.length
                      }
                      onClick={() => testConnection(connection)}
                    >
                      <RefreshCw size={15} />
                      Test connection
                    </Button>
                    {connection.kind === "device" && connection.active && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={pending}
                        onClick={() => pairDevice(connection)}
                      >
                        <Smartphone size={15} />
                        Pair device
                      </Button>
                    )}
                    {connection.active && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={pending}
                        onClick={() =>
                          execute(
                            "connections.revoke",
                            {
                              id: connection.id,
                              expected_revision: connection.revision,
                            },
                            "Connection revoked. New execution is fenced.",
                          )
                        }
                      >
                        <Unplug size={15} />
                        Revoke connection
                      </Button>
                    )}
                  </div>
                  <details className="details">
                    <summary>Inspect readiness and access</summary>
                    {connection.kind === "webhook" && (
                      <>
                        <p className="field-hint">Incoming webhook endpoint</p>
                        <pre>{`${window.location.origin}/api/v1/external/${agentId}/webhook/${connection.id}/receive`}</pre>
                      </>
                    )}
                    <pre>{JSON.stringify(connection, null, 2)}</pre>
                  </details>
                </article>
              );
            })}
          </div>
        )}
      </Card>
    );
  return (
    <div className="page wc-page">
      <header className="page-heading">
        <div>
          <div className="eyebrow">A deliberate connection</div>
          <h1>Your agent, connected.</h1>
          <p>Useful access to the world. Always yours to grant and revoke.</p>
        </div>
        <Button variant="primary" onClick={() => edit("new")}>
          <Plus size={17} />
          Add connection
        </Button>
      </header>
      <div className="wc-connection-toolbar">
        <Tabs
          value={tab}
          onValueChange={setTab}
          items={[
            { value: "all", label: "All connections" },
            { value: "ready", label: "Ready" },
            { value: "attention", label: "Needs attention" },
          ]}
        />
        <div className="wc-search">
          <Search size={17} aria-hidden="true" />
          <Input
            aria-label="Search connections"
            placeholder="Find a connection"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
      </div>
      {content}
      <Modal
        open={editing !== null}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
        title={existing ? "Edit connection" : "Add connection"}
        description="Connect a service first. Choose its access and test readiness next."
      >
        {editing && (
          <form
            key={existing?.id || "new"}
            className="stack"
            onSubmit={configure}
          >
            <label className="field">
              Connection ID
              <Input
                name="id"
                defaultValue={existing?.id}
                placeholder="research-source"
                pattern="[A-Za-z0-9_-]+"
                maxLength={64}
                required
                readOnly={Boolean(existing)}
              />
            </label>
            <Select
              label="Connection type"
              value={kind}
              onValueChange={(value) => {
                setKind(value);
                setError("");
              }}
              items={kinds.map(({ value, label }) => ({ value, label }))}
              disabled={Boolean(existing)}
            />
            <label className="field">
              Endpoint
              <Input
                name="endpoint"
                aria-label="Endpoint"
                aria-describedby="connection-endpoint-help"
                type="url"
                defaultValue={existing?.endpoint}
                placeholder="https://service.example/api"
                required
              />
              <span className="field-hint" id="connection-endpoint-help">
                Use the service’s HTTP endpoint. Private network access follows
                your installation’s settings.
              </span>
            </label>
            <Select
              label="Credential"
              value={credentialRef}
              onValueChange={setCredentialRef}
              items={credentialItems}
            />
            <p className="field-hint">
              Credentials are stored privately in Settings. This form uses their
              references.
            </p>
            {kind === "mcp" && (
              <label className="field">
                Allowed app tools
                <Input
                  name="allowed_tools"
                  defaultValue={
                    Array.isArray(existing?.options.allowed_tools)
                      ? existing.options.allowed_tools.join(", ")
                      : ""
                  }
                  placeholder="search_notes, read_document"
                  required
                />
                <span className="field-hint">
                  Exact tool names, separated by commas.
                </span>
              </label>
            )}
            {["stt", "tts", "image"].includes(kind) && (
              <label className="field">
                Model (optional)
                <Input
                  name="model"
                  defaultValue={textOption(existing?.options.model)}
                  placeholder="Service model name"
                />
              </label>
            )}
            {kind === "tts" && (
              <label className="field">
                Voice (optional)
                <Input
                  name="voice"
                  defaultValue={textOption(existing?.options.voice)}
                  placeholder="Service voice name"
                />
              </label>
            )}
            {kind === "device" && (
              <>
                <label className="field">
                  Device identity
                  <Input
                    name="device_id"
                    defaultValue={textOption(existing?.options.device_id)}
                    placeholder="desk-device"
                    pattern="[A-Za-z0-9_-]+"
                    required
                  />
                </label>
                <label className="field">
                  Allowed device operations
                  <Input
                    name="device_operations"
                    defaultValue={
                      existing?.options.operations &&
                      typeof existing.options.operations === "object"
                        ? Object.keys(existing.options.operations).join(", ")
                        : ""
                    }
                    placeholder="read_status"
                    required
                  />
                  <span className="field-hint">
                    Name only operations your device supports. Define their
                    input schemas under Advanced options.
                  </span>
                </label>
              </>
            )}
            {kind === "webhook" && (
              <p className="wc-availability">
                Attach a signing credential. After configuration, the external
                endpoint appears with the connection’s access details.
              </p>
            )}
            {kind === "browser" && (
              <p className="wc-availability">
                Readiness checks whether this runtime has an operator-managed
                isolated browser.
              </p>
            )}
            <details className="wc-budget">
              <summary>Advanced options</summary>
              <p className="field-hint">
                Optional origin restrictions and adapter settings. Device
                operation schemas use the “operations” field.
              </p>
              <label className="field">
                Options (JSON)
                <Textarea
                  className="wc-json"
                  name="options"
                  defaultValue={JSON.stringify(
                    existing?.options || {},
                    null,
                    2,
                  )}
                  rows={5}
                  spellCheck={false}
                />
              </label>
            </details>
            {error && (
              <p role="alert" className="wc-form-error">
                {error}
              </p>
            )}
            <div className="form-actions">
              <Button variant="ghost" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" loading={pending}>
                {existing ? "Save connection" : "Configure connection"}
              </Button>
            </div>
          </form>
        )}
      </Modal>
      <Modal
        open={grant !== null}
        onOpenChange={(open) => {
          if (!open) setGrant(null);
        }}
        title="Manage connection access"
        description={
          grant
            ? `Choose what ${grant.id} may provide to your agent.`
            : undefined
        }
      >
        {grant && (
          <form
            className="stack"
            onSubmit={async (event) => {
              event.preventDefault();
              if (pending) return;
              const operation = kinds.find(
                (item) => item.value === grant.kind,
              )?.operation;
              if (!operation) return;
              const result = grantSelected
                ? await execute(
                    "connections.grant",
                    {
                      id: grant.id,
                      operations: [operation],
                      expected_revision: grant.revision,
                    },
                    "Access granted. Test this configuration before use.",
                  )
                : await execute(
                    "connections.revoke",
                    { id: grant.id, expected_revision: grant.revision },
                    "Connection access revoked.",
                  );
              if (result !== undefined) setGrant(null);
            }}
          >
            <label className="wc-grant">
              <Input
                type="checkbox"
                checked={grantSelected}
                onChange={(event) => setGrantSelected(event.target.checked)}
              />
              <span>
                <strong>
                  {kinds.find((item) => item.value === grant.kind)?.access ||
                    "Connection access"}
                </strong>
                <small>
                  Model action permissions remain separate and can require your
                  approval.
                </small>
              </span>
            </label>
            <div className="form-actions">
              <Button variant="ghost" onClick={() => setGrant(null)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" loading={pending}>
                Save grants
              </Button>
            </div>
          </form>
        )}
      </Modal>
      <Modal
        open={pairing !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPairing(null);
            setCopied(false);
          }
        }}
        title="Pair your device"
        description="Use this one-time proof in your device client. It is only shown during this pairing attempt."
      >
        {pairing && (
          <div className="stack">
            <div className="wc-pair-proof">
              <span className="field-hint">Pairing token</span>
              <code>{pairing.pairing_token}</code>
              <Button
                variant="secondary"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(pairing.pairing_token);
                    setCopied(true);
                  } catch {
                    notify(
                      "Copy is unavailable in this browser. Select the displayed token to copy it manually.",
                      "error",
                    );
                  }
                }}
              >
                {copied ? <Check size={16} /> : <Copy size={16} />}
                {copied ? "Copied" : "Copy token"}
              </Button>
            </div>
            <p className="field-hint">
              Expires {new Date(pairing.expires_at * 1000).toLocaleTimeString()}
              . Pairing verifies identity; grant access and test the connection
              after the device claims it.
            </p>
            <details className="details">
              <summary>Device client endpoint</summary>
              <pre>{`${window.location.origin}/api/v1/external/${agentId}/device/${pairing.connection_id}/claim`}</pre>
            </details>
            <div className="form-actions">
              <Button
                variant="primary"
                onClick={() => {
                  setPairing(null);
                  setCopied(false);
                }}
              >
                Done
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
