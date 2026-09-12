import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  Check,
  Download,
  KeyRound,
  Plus,
  Power,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Upload,
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
} from "../components/ui";
import {
  canWakeRuntime,
  pauseRuntimeQueries,
  useAction,
  useAgent,
  useAgentQuery,
  useRuntimeState,
} from "../hooks/useAgent";
import { ApiError, api, downloadBody, fileBase64, requestId } from "../lib/api";
import type {
  Agent,
  Approval,
  Credential,
  Overview,
  Plugin,
  Provider,
  RuntimeState,
  Skill,
  ToolPolicy,
} from "../lib/types";
import { Evidence, FormDialog, Metric, PageHeader, QueryState } from "./shared";
import AgentGatewayModel from "./gateway/AgentGatewayModel";
type Backup = {
  format: string;
  archive_base64: string;
  sha256: string;
  bytes: number;
};
async function sha256(bytes: BufferSource) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}
async function verifiedBackup(value: Backup) {
  try {
    if (
      !value ||
      Object.keys(value).sort().join(",") !==
        "archive_base64,bytes,format,sha256" ||
      value.format !== "sturgis-backup/1" ||
      !Number.isInteger(value.bytes) ||
      value.bytes < 1 ||
      value.bytes > 4194304 ||
      typeof value.archive_base64 !== "string" ||
      value.archive_base64.length > 5592408 ||
      value.archive_base64.length % 4 !== 0 ||
      /[^A-Za-z0-9+/=]/.test(value.archive_base64) ||
      typeof value.sha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(value.sha256)
    )
      throw new Error();
    const bytes = Uint8Array.from(atob(value.archive_base64), (char) =>
      char.charCodeAt(0),
    );
    if (bytes.length !== value.bytes || (await sha256(bytes)) !== value.sha256)
      throw new Error();
    return bytes;
  } catch {
    throw new Error(
      "Backup response failed its format, size or digest verification.",
    );
  }
}
async function restoreBackup(accountId: string, name: string, file: File) {
  if (!file.size) throw new Error("Choose a nonempty Sturgis backup archive.");
  const archive_base64 = await fileBase64(file);
  const digest = await sha256(await file.arrayBuffer());
  const identity = await sha256(
    new TextEncoder().encode(JSON.stringify([name, digest])),
  );
  const key = `sturgis:pending-restore:${encodeURIComponent(accountId)}`;
  let receipt: { digest: string; request_id: string } | undefined,
    retained = false,
    dispatched = false;
  try {
    const raw = sessionStorage.getItem(key);
    if (raw !== null) {
      let prior;
      try {
        prior = JSON.parse(raw);
      } catch {
        throw new Error(
          "Saved restore recovery cannot be read. Review your agent list before starting another restore.",
        );
      }
      if (
        !prior ||
        Object.keys(prior).sort().join(",") !== "digest,request_id" ||
        typeof prior.digest !== "string" ||
        !/^[a-f0-9]{64}$/.test(prior.digest) ||
        typeof prior.request_id !== "string" ||
        !/^[A-Za-z0-9_-]{1,128}$/.test(prior.request_id)
      )
        throw new Error(
          "Saved restore recovery cannot be read. Review your agent list before starting another restore.",
        );
      if (prior.digest !== identity)
        throw new Error(
          "A previous restore has an unknown outcome. Retry its original name and backup file before starting a different restore.",
        );
      receipt = prior;
      retained = true;
    } else receipt = { digest: identity, request_id: requestId() };
    try {
      sessionStorage.setItem(key, JSON.stringify(receipt));
    } catch {
      throw new Error(
        "Restore recovery could not be saved. No restore was sent.",
      );
    }
    dispatched = true;
    const result = await api<Agent>("/agents/restore", {
      name,
      archive_base64,
      sha256: digest,
      request_id: receipt!.request_id,
    });
    sessionStorage.removeItem(key);
    return result;
  } catch (error) {
    const uncertain =
      dispatched &&
      (!(error instanceof ApiError) ||
        [
          "worker_timeout",
          "worker_indeterminate",
          "restore_indeterminate",
          "restore_in_progress",
          "internal_error",
          "request_failed",
          "version_mismatch",
        ].includes(error.code) ||
        (retained && error.code !== "restore_interrupted"));
    if (dispatched && !uncertain) {
      try {
        sessionStorage.removeItem(key);
      } catch {
        /* A retained ID remains safe to reconcile. */
      }
    }
    if (uncertain)
      throw new Error(
        "Restore may have completed. Retry the same name and backup file to check the original receipt, or review your agent list.",
      );
    throw error;
  }
}
export default function SettingsPage({
  agent,
  onChanged,
  onCreate,
  onArchived,
  onOpenGateway,
}: {
  agent: Agent;
  onChanged: () => Promise<unknown>;
  onCreate: () => void;
  onArchived: () => Promise<void>;
  onOpenGateway?: () => void;
}) {
  const { agentId, accountId, notify } = useAgent();
  const client = useQueryClient();
  const runtime = useRuntimeState(accountId, agentId);
  const { execute, pending } = useAction();
  const [tab, setTab] = useState("model");
  const [provider, setProvider] = useState<Provider | "new" | null>(null);
  const [credential, setCredential] = useState<Credential | "new" | null>(null);
  const [revoking, setRevoking] = useState<Credential | null>(null);
  const [skill, setSkill] = useState(false);
  const [rename, setRename] = useState(false);
  const [archive, setArchive] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [backupFile, setBackupFile] = useState<File | null>(null);
  const [operatorPending, setOperatorPending] = useState(false);
  const [diagnostics, setDiagnostics] = useState<unknown>(null);
  const overview = useAgentQuery<Overview>("overview");
  const credentials = useAgentQuery<Credential[]>("providers.credentials.list");
  const policies = useAgentQuery<ToolPolicy[]>("permissions.list");
  const approvals = useAgentQuery<Approval[]>(
    "permissions.pending",
    {},
    { interval: 15000 },
  );
  const skills = useAgentQuery<Skill[]>("skills.list");
  const plugins = useAgentQuery<Plugin[]>("plugins.list");
  const editProvider = provider && provider !== "new" ? provider : null;
  const editCredential = credential && credential !== "new" ? credential : null;
  async function operator<T>(fn: () => Promise<T>, message?: string) {
    setOperatorPending(true);
    try {
      const result = await fn();
      if (message) notify(message);
      return result;
    } catch (error) {
      notify((error as Error).message, "error");
      return undefined;
    } finally {
      setOperatorPending(false);
    }
  }
  async function runtimeAction(action: "start" | "suspend" | "recover") {
    await operator(async () => {
      pauseRuntimeQueries(client, accountId, agentId, true);
      try {
        await client.cancelQueries({ queryKey: ["agent", agentId] });
        const result = await api<RuntimeState>(
          `/agents/${agentId}/lifecycle/${action}`,
          {},
        );
        client.setQueryData(["runtime", accountId, agentId], {
          ...result,
          private_queries_paused: true,
        });
        notify("Runtime: " + result.observed);
      } finally {
        pauseRuntimeQueries(client, accountId, agentId, false);
      }
    });
  }
  const tabs = [
    { value: "model", label: "Agent model" },
    { value: "permissions", label: "Permissions" },
    { value: "procedures", label: "Procedures" },
    { value: "runtime", label: "Agent & data" },
  ];
  return (
    <div className="page">
      <PageHeader
        eyebrow="Make it yours"
        title="Settings"
        description="Choose how your agent thinks, what it can do, and where its data lives."
      />
      <Tabs value={tab} onValueChange={setTab} items={tabs} />
      <div className="mt-7">
        <QueryState
          loading={
            runtime.isPending || (tab !== "runtime" && overview.isPending)
          }
          error={runtime.error || (tab !== "runtime" && overview.error) || null}
          retry={() => runtime.refetch()}
        />
        {runtime.data && !canWakeRuntime(runtime.data) && tab !== "runtime" && (
          <EmptyState
            title={
              runtime.data.private_queries_paused
                ? "Updating runtime status"
                : "Runtime is " + (runtime.data.observed || "unavailable")
            }
            description="Start the runtime to view or change private agent settings. Any scheduled wake remains active."
            action={
              <Button
                loading={operatorPending}
                onClick={() => runtimeAction("start")}
              >
                Start runtime
              </Button>
            }
          />
        )}
        {((overview.data && canWakeRuntime(runtime.data)) ||
          tab === "runtime") && (
          <>
            {tab === "model" && overview.data && (
              <>
                <AgentGatewayModel
                  providers={overview.data.provider_settings}
                  onOpenGateway={onOpenGateway}
                />
                <details className="gateway-legacy">
                  <summary>
                    Advanced: legacy model connections, credentials & usage
                  </summary>
                  <div className="stats">
                    <Metric
                      label="Recorded tokens"
                      value={(
                        overview.data.usage.actual.tokens || 0
                      ).toLocaleString()}
                      footnote="Actual provider usage receipts"
                    />
                    <Metric
                      label="Open reservations"
                      value={overview.data.usage.unresolved_claims.length}
                      footnote="Still awaiting complete usage evidence"
                    />
                    <Metric
                      label="Memory"
                      value={
                        overview.data.memory.available
                          ? "Available"
                          : "Unavailable"
                      }
                      footnote="Private native memory engine"
                    />
                  </div>
                  <div className="page-grid">
                    <Card
                      title="Your models"
                      description="An actual probe checks the current configuration before you select it."
                      actions={
                        <Button size="sm" onClick={() => setProvider("new")}>
                          <Plus size={16} />
                          Add model
                        </Button>
                      }
                    >
                      {overview.data.provider_settings.length ? (
                        overview.data.provider_settings.map((model) => (
                          <article className="record" key={model.id}>
                            <div className="row spread">
                              <div>
                                <h3 className="record-title">{model.model}</h3>
                                <p className="record-subtitle">
                                  {model.id} ·{" "}
                                  {model.context_limit.toLocaleString()} context
                                  · {model.output_limit.toLocaleString()} output
                                </p>
                              </div>
                              <Badge
                                tone={
                                  model.active
                                    ? "accent"
                                    : model.ready
                                      ? "success"
                                      : "warning"
                                }
                              >
                                {model.active
                                  ? "Selected"
                                  : model.ready
                                    ? "Ready"
                                    : "Needs probe"}
                              </Badge>
                            </div>
                            <div className="record-actions">
                              <Button
                                size="sm"
                                loading={pending}
                                onClick={() =>
                                  execute("providers.probe", {
                                    provider_id: model.id,
                                    expected_revision: model.revision,
                                  })
                                }
                              >
                                <RefreshCw size={14} />
                                Probe model
                              </Button>
                              <Button
                                size="sm"
                                variant={model.active ? "ghost" : "primary"}
                                disabled={
                                  !model.ready || model.active || pending
                                }
                                onClick={() =>
                                  execute(
                                    "providers.activate",
                                    {
                                      provider_id: model.id,
                                      expected_revision: model.revision,
                                    },
                                    "Model selected.",
                                  )
                                }
                              >
                                <Check size={14} />
                                {model.active ? "In use" : "Use model"}
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => setProvider(model)}
                              >
                                Edit
                              </Button>
                            </div>
                            <Evidence data={model} />
                          </article>
                        ))
                      ) : (
                        <EmptyState
                          icon={<Sparkles size={25} />}
                          title="Choose a mind for your agent"
                          description="Connect an OpenAI-compatible streaming endpoint. You control the model, context limits, and credentials."
                          action={
                            <Button
                              variant="primary"
                              onClick={() => setProvider("new")}
                            >
                              Connect your first model
                            </Button>
                          }
                        />
                      )}
                    </Card>
                    <Card
                      title="Private credentials"
                      description="Encrypted secrets stay outside the agent’s writable workspace."
                      actions={
                        <Button size="sm" onClick={() => setCredential("new")}>
                          <Plus size={16} />
                          Add credential
                        </Button>
                      }
                    >
                      <QueryState
                        loading={credentials.isPending}
                        error={credentials.error}
                        retry={() => credentials.refetch()}
                      />
                      {credentials.data?.length
                        ? credentials.data.map((item) => (
                            <article className="record" key={item.name}>
                              <div className="row spread">
                                <div className="row">
                                  <KeyRound size={18} />
                                  <h3 className="record-title">{item.name}</h3>
                                </div>
                                <Badge
                                  tone={item.active ? "success" : "neutral"}
                                >
                                  {item.active ? "Active" : "Revoked"}
                                </Badge>
                              </div>
                              <p className="record-subtitle break-all">
                                {item.reference}
                              </p>
                              <div className="record-actions">
                                <Button
                                  size="sm"
                                  onClick={() => setCredential(item)}
                                >
                                  Rotate secret
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  disabled={!item.active}
                                  onClick={() => setRevoking(item)}
                                >
                                  Revoke
                                </Button>
                              </div>
                            </article>
                          ))
                        : !credentials.isPending && (
                            <EmptyState
                              icon={<KeyRound size={25} />}
                              title="Secrets, kept private"
                              description="Store a key once, then select its reference in a model or connection. Inspection never reveals the stored value."
                            />
                          )}
                    </Card>
                    <Card
                      title="Usage evidence"
                      description="Consumption, overages, and outstanding reservations stay distinct from billing."
                    >
                      <Evidence data={overview.data.usage} />
                      <p className="field-hint mt-4">
                        Payment settlement is not configured for this
                        installation.
                      </p>
                    </Card>
                  </div>
                </details>
              </>
            )}
            {tab === "permissions" && (
              <div className="page-grid">
                <Card
                  title="Tool policies"
                  description="Ask creates a request tied to an exact invocation. Allow and deny apply to the current tool version."
                >
                  <QueryState
                    loading={policies.isPending}
                    error={policies.error}
                    retry={() => policies.refetch()}
                  />
                  {policies.data?.map((policy) => (
                    <article className="record" key={policy.id}>
                      <div className="row spread">
                        <div>
                          <h3 className="record-title">{policy.id}</h3>
                          <p className="record-subtitle">
                            {policy.stale
                              ? "Tool changed — review this policy"
                              : `${policy.tool.read_only ? "Reads data" : "Can change data or external systems"}${policy.tool.required_capabilities.length ? ". Requires " + policy.tool.required_capabilities.join(", ") : ""}.`}
                          </p>
                        </div>
                        <Select
                          label={"Policy for " + policy.id}
                          value={policy.mode}
                          disabled={pending}
                          onValueChange={(mode) =>
                            execute(
                              "permissions.set",
                              {
                                tool: policy.id,
                                mode,
                                expected_revision: policy.revision,
                              },
                              "Permission policy updated.",
                            )
                          }
                          items={[
                            { value: "ask", label: "Ask first" },
                            { value: "allow", label: "Allow" },
                            { value: "deny", label: "Deny" },
                          ]}
                        />
                      </div>
                    </article>
                  ))}
                </Card>
                <Card
                  title="Action requests"
                  description="Approvals are single use, expire, and remain bound to the original workspace and tool version."
                >
                  {approvals.data?.length ? (
                    approvals.data.map((approval) => (
                      <article className="record" key={approval.id}>
                        <h3 className="record-title">
                          {approval.call?.tool || "Action request"}
                        </h3>
                        <Evidence data={approval} />
                        <div className="record-actions">
                          <Button
                            variant="primary"
                            size="sm"
                            loading={pending}
                            onClick={() =>
                              execute(
                                "permissions.approve",
                                { event_id: approval.id },
                                "Approved once. The action must still execute under a valid grant.",
                              )
                            }
                          >
                            <ShieldCheck size={15} />
                            Approve once
                          </Button>
                          <Button
                            size="sm"
                            onClick={() =>
                              execute(
                                "permissions.deny",
                                { event_id: approval.id },
                                "Request denied.",
                              )
                            }
                          >
                            Deny
                          </Button>
                        </div>
                      </article>
                    ))
                  ) : (
                    <EmptyState
                      icon={<ShieldCheck size={26} />}
                      title="Nothing waiting on you"
                      description="When your agent needs permission for an exact action, its evidence will appear here."
                    />
                  )}
                </Card>
              </div>
            )}
            {tab === "procedures" && (
              <div className="page-grid">
                <Card
                  title="Reusable procedures"
                  description="Versioned steps your agent can select when relevant."
                  actions={
                    <Button size="sm" onClick={() => setSkill(true)}>
                      <Plus size={15} />
                      Add procedure
                    </Button>
                  }
                >
                  {skills.data?.length ? (
                    skills.data.map((item) => (
                      <article className="record" key={item.id}>
                        <div className="row spread">
                          <div>
                            <h3 className="record-title">
                              {item.manifest.description}
                            </h3>
                            <p className="record-subtitle">
                              {item.id} · version {item.manifest.version}
                            </p>
                          </div>
                          <Badge
                            tone={
                              item.state === "available" ? "success" : "neutral"
                            }
                          >
                            {item.state === "available"
                              ? "Enabled"
                              : "Disabled"}
                          </Badge>
                        </div>
                        <div className="record-actions">
                          <Button
                            size="sm"
                            onClick={() =>
                              execute("skills.enable", {
                                id: item.id,
                                enabled: item.state !== "available",
                                expected_revision: item.revision,
                              })
                            }
                          >
                            {item.state === "available"
                              ? "Disable procedure"
                              : "Enable procedure"}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() =>
                              execute("skills.rollback", {
                                id: item.id,
                                expected_revision: item.revision,
                              })
                            }
                          >
                            Previous version
                          </Button>
                        </div>
                        <Evidence data={item} />
                      </article>
                    ))
                  ) : (
                    <EmptyState
                      icon={<BookIcon />}
                      title="Build on what works"
                      description="Turn a useful procedure into reusable steps, with explicit triggers and required tools."
                    />
                  )}
                </Card>
                <Card
                  title="Installed extensions"
                  description="Your operator installs extension packages; you control their active state."
                >
                  {plugins.data?.length ? (
                    plugins.data.map((item) => (
                      <article className="record" key={item.id}>
                        <div className="row spread">
                          <h3 className="record-title">{item.id}</h3>
                          <Badge>{item.status}</Badge>
                        </div>
                        <div className="record-actions">
                          <Button
                            size="sm"
                            onClick={() =>
                              execute(
                                item.status === "active"
                                  ? "plugins.disable"
                                  : "plugins.activate",
                                {
                                  id: item.id,
                                  expected_revision: item.revision,
                                },
                              )
                            }
                          >
                            {item.status === "active"
                              ? "Disable extension"
                              : "Activate extension"}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() =>
                              execute("plugins.rollback", {
                                id: item.id,
                                expected_revision: item.revision,
                              })
                            }
                          >
                            Roll back
                          </Button>
                        </div>
                        <Evidence data={item} />
                      </article>
                    ))
                  ) : (
                    <EmptyState
                      title="A considered set of tools"
                      description="Installed extensions appear here with their lifecycle and health information."
                    />
                  )}
                </Card>
              </div>
            )}
            {tab === "runtime" && (
              <div className="page-grid">
                <Card
                  title={agent.name}
                  description="A private runtime with its own conversations, knowledge, and work."
                  actions={
                    <Button size="sm" onClick={() => setRename(true)}>
                      Rename
                    </Button>
                  }
                >
                  <div className="row">
                    <Badge tone="accent">
                      {overview.data?.mode === "hosted"
                        ? "Hosted"
                        : overview.data?.mode === "self_hosted"
                          ? "Self-hosted"
                          : "Private runtime"}
                    </Badge>
                    <Badge
                      tone={
                        runtime.data?.observed === "running"
                          ? "success"
                          : "warning"
                      }
                    >
                      {runtime.data?.observed || "Checking runtime status"}
                    </Badge>
                  </div>
                  <div className="record-actions">
                    {(["start", "suspend", "recover"] as const).map(
                      (action) => (
                        <Button
                          key={action}
                          size="sm"
                          loading={operatorPending}
                          onClick={() => runtimeAction(action)}
                        >
                          {action === "start" ? (
                            <Power size={15} />
                          ) : (
                            <RefreshCw size={15} />
                          )}{" "}
                          {action === "start"
                            ? "Start runtime"
                            : action === "suspend"
                              ? "Suspend runtime"
                              : "Recover runtime"}
                        </Button>
                      ),
                    )}
                  </div>
                  <p className="field-hint mt-4">
                    Suspension requires idle work and retains scheduled wake.
                    Recovery interrupts the current process; uncertain effects
                    remain recorded.
                  </p>
                  {runtime.data?.next_wake_at != null && (
                    <p className="field-hint">
                      Next scheduled wake:{" "}
                      {new Date(
                        runtime.data.next_wake_at * 1000,
                      ).toLocaleString()}
                    </p>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={!canWakeRuntime(runtime.data)}
                    onClick={async () => {
                      const result = await execute("diagnostics.get");
                      if (result) setDiagnostics(result);
                    }}
                  >
                    Runtime diagnostics
                  </Button>
                  <div className="divider" />
                  <Button onClick={onCreate}>
                    <Plus size={16} />
                    Create another agent
                  </Button>
                </Card>
                <Card
                  title="Take your space with you"
                  description="Export consistent source data and historical evidence. Restore creates a fresh private agent with execution authority disabled."
                >
                  <div className="record-actions">
                    <Button
                      variant="primary"
                      loading={operatorPending}
                      onClick={() =>
                        operator(async () => {
                          const backup = await api<Backup>(
                            `/agents/${agentId}/backup`,
                            {},
                          );
                          const bytes = await verifiedBackup(backup);
                          downloadBody(
                            new Blob([bytes], {
                              type: "application/zip",
                            }),
                            agent.name.replace(/[^a-zA-Z0-9_-]/g, "_") +
                              ".sturgis",
                          );
                        }, "Consistent backup created.")
                      }
                    >
                      <Download size={16} />
                      Export backup
                    </Button>
                    <Button onClick={() => setRestoring(true)}>
                      <Upload size={16} />
                      Restore backup
                    </Button>
                  </div>
                  <p className="field-hint mt-4">
                    Credentials, live permissions, and scheduled execution must
                    be configured again after restore. Backup size is bounded;
                    oversized exports return an explicit error.
                  </p>
                </Card>
                <Card
                  title="Archive this agent"
                  description="Archival is a durable execution fence. Records remain private, and scheduled work cannot wake the archived agent."
                >
                  <Button variant="danger" onClick={() => setArchive(true)}>
                    <Archive size={16} />
                    Archive agent
                  </Button>
                </Card>
              </div>
            )}
          </>
        )}
      </div>
      <FormDialog
        open={!!provider}
        onOpenChange={(open) => {
          if (!open) setProvider(null);
        }}
        title={editProvider ? "Edit model provider" : "Connect a model"}
        description="Use a complete OpenAI-compatible streaming endpoint URL. Limits are your declared configuration until probed."
        fields={[
          {
            name: "provider_id",
            label: "Provider ID",
            value: editProvider?.id || "primary",
            required: true,
          },
          {
            name: "endpoint",
            label: "Endpoint",
            type: "url",
            value: editProvider?.endpoint,
            required: true,
          },
          {
            name: "model",
            label: "Model",
            value: editProvider?.model,
            required: true,
          },
          {
            name: "context_limit",
            label: "Context limit",
            type: "number",
            value: editProvider?.context_limit || 64000,
            min: 1,
            required: true,
          },
          {
            name: "output_limit",
            label: "Output limit",
            type: "number",
            value: editProvider?.output_limit || 2048,
            min: 1,
            required: true,
          },
          {
            name: "credential_ref",
            label: "Credential reference (optional)",
            value: editProvider?.credential_ref || "",
          },
        ]}
        pending={pending}
        submitLabel="Save provider"
        onSubmit={async (data) => {
          const result = await execute("providers.configure", {
            provider_id: data.provider_id,
            endpoint: data.endpoint,
            model: data.model,
            context_limit: Number(data.context_limit),
            output_limit: Number(data.output_limit),
            credential_ref: data.credential_ref || null,
            tools: data.tools === "on",
            expected_revision:
              overview.data?.provider_settings.find(
                (item) => item.id === data.provider_id,
              )?.revision || 0,
          });
          if (result) setProvider(null);
        }}
      >
        <label className="row text-sm">
          <input
            type="checkbox"
            name="tools"
            defaultChecked={editProvider?.tools}
          />
          Model supports canonical tool calls
        </label>
      </FormDialog>
      <FormDialog
        open={!!credential}
        onOpenChange={(open) => {
          if (!open) setCredential(null);
        }}
        title={editCredential ? "Rotate credential" : "Store a credential"}
        fields={[
          {
            name: "name",
            label: "Credential name",
            value: editCredential?.name,
            required: true,
          },
          { name: "secret", label: "Secret", type: "password", required: true },
        ]}
        pending={pending}
        submitLabel="Store credential"
        onSubmit={async (data, form) => {
          const result = await execute<Credential>(
            "providers.credentials.put",
            {
              ...data,
              expected_revision:
                credentials.data?.find((item) => item.name === data.name)
                  ?.revision || 0,
            },
          );
          if (result) {
            (form.elements.namedItem("secret") as HTMLInputElement).value = "";
            setCredential(null);
            notify(
              "Credential stored. Select its reference in a model or connection.",
            );
          }
        }}
      />
      <Modal
        open={!!revoking}
        onOpenChange={(open) => {
          if (!open) setRevoking(null);
        }}
        title="Revoke this credential?"
        description="Any provider or connection using this revision will need a new credential and readiness check."
      >
        <div className="form-actions">
          <Button variant="ghost" onClick={() => setRevoking(null)}>
            Keep credential
          </Button>
          <Button
            variant="danger"
            loading={pending}
            onClick={async () => {
              if (
                await execute("providers.credentials.revoke", {
                  name: revoking!.name,
                  expected_revision: revoking!.revision,
                })
              )
                setRevoking(null);
            }}
          >
            Revoke credential
          </Button>
        </div>
      </Modal>
      <FormDialog
        open={skill}
        onOpenChange={setSkill}
        title="Add a reusable procedure"
        fields={[
          { name: "id", label: "Procedure ID", required: true },
          { name: "version", label: "Version", value: "1", required: true },
          { name: "description", label: "Description", required: true },
          {
            name: "triggers",
            label: "Trigger words",
            hint: "Separate phrases with commas.",
            required: true,
          },
          {
            name: "steps",
            label: "Procedure steps",
            type: "textarea",
            hint: "One step per line.",
            required: true,
          },
        ]}
        pending={pending}
        submitLabel="Install procedure"
        onSubmit={async (data) => {
          if (
            await execute("skills.install", {
              manifest: {
                id: data.id,
                version: data.version,
                description: data.description,
                triggers: data.triggers
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
                steps: data.steps.split("\n").filter((s) => s.trim()),
                required_tools: [],
              },
            })
          )
            setSkill(false);
        }}
      />
      <FormDialog
        open={rename}
        onOpenChange={setRename}
        title="Rename agent"
        fields={[
          {
            name: "name",
            label: "Display name",
            value: agent.name,
            required: true,
          },
        ]}
        pending={operatorPending}
        submitLabel="Rename agent"
        onSubmit={async (data) => {
          if (
            await operator(() =>
              api(
                `/agents/${agentId}`,
                { name: data.name, expected_revision: agent.revision },
                { method: "PATCH" },
              ),
            )
          ) {
            setRename(false);
            await onChanged();
          }
        }}
      />
      <Modal
        open={archive}
        onOpenChange={setArchive}
        title="Archive this agent?"
        description="Accepted work must drain first. Once archived, the runtime cannot restart or execute scheduled work."
      >
        <div className="form-actions">
          <Button variant="ghost" onClick={() => setArchive(false)}>
            Keep agent
          </Button>
          <Button
            variant="danger"
            loading={operatorPending}
            onClick={async () => {
              if (
                await operator(() =>
                  api(
                    `/agents/${agentId}`,
                    { archived: true, expected_revision: agent.revision },
                    { method: "PATCH" },
                  ),
                )
              ) {
                setArchive(false);
                await onArchived();
              }
            }}
          >
            Archive agent
          </Button>
        </div>
      </Modal>
      <FormDialog
        open={restoring}
        onOpenChange={setRestoring}
        title="Restore a backup"
        description="Restore creates a new owned agent. Imported events remain labeled history and cannot replay old actions."
        fields={[
          {
            name: "name",
            label: "Restored agent name",
            value: agent.name + " restored",
            required: true,
          },
        ]}
        pending={operatorPending}
        submitLabel="Restore backup"
        onSubmit={async (data) => {
          if (!backupFile) {
            notify("Choose a Sturgis backup file.", "error");
            return;
          }
          const result = await operator(
            () => restoreBackup(accountId, data.name, backupFile),
            "Backup restored into a fresh agent. Reconnect credentials before execution.",
          );
          if (result) {
            setRestoring(false);
            setBackupFile(null);
            await onChanged();
          }
        }}
      >
        <label className="field">
          Backup file
          <Input
            type="file"
            accept=".sturgis,.zip,application/zip"
            onChange={(event) => setBackupFile(event.target.files?.[0] || null)}
          />
        </label>
      </FormDialog>
      <Modal
        open={!!diagnostics}
        onOpenChange={(open) => {
          if (!open) setDiagnostics(null);
        }}
        title="Runtime diagnostics"
        description="Observed state for your current private worker."
      >
        <pre className="code">{JSON.stringify(diagnostics, null, 2)}</pre>
      </Modal>
    </div>
  );
}
function BookIcon() {
  return <Sparkles size={25} />;
}
