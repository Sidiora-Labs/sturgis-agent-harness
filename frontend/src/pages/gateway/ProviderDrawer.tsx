import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Dialog } from "radix-ui";
import {
  ArrowRight,
  Check,
  Download,
  ExternalLink,
  KeyRound,
  Plus,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { Button, Input } from "../../components/ui";
import { api } from "../../lib/api";
import type {
  GatewayConnection,
  GatewayModel,
  GatewayPreset,
} from "../../lib/types";

/** One ephemeral form owns credentials through discovery and connection. */
export default function ProviderDrawer({
  accountId,
  preset,
  connection,
  authAccount,
  onClose,
}: {
  accountId: string;
  preset: GatewayPreset;
  connection?: GatewayConnection;
  authAccount?: { id: string; name: string };
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [name, setName] = useState(
    connection?.name || authAccount?.name || preset.name,
  );
  const [endpoint, setEndpoint] = useState(
    connection?.endpoint || preset.endpoint,
  );
  const [key, setKey] = useState("");
  const [models, setModels] = useState<GatewayModel[]>(
    connection?.models || [],
  );
  const [selected, setSelected] = useState<string[]>(
    connection?.models.map((model) => model.id) || [],
  );
  const [search, setSearch] = useState("");
  const [manual, setManual] = useState("");
  const [pending, setPending] = useState<"discover" | "connect" | null>(null);
  const [error, setError] = useState("");
  const focus = useRef<HTMLElement | null>(null);
  const keyRequired =
    preset.auth === "api_key" &&
    !connection?.credential_configured &&
    !authAccount;
  const canLoad = !!endpoint.trim() && (!keyRequired || !!key.trim());
  const visible = models.filter((model) =>
    `${model.name} ${model.id}`.toLowerCase().includes(search.toLowerCase()),
  );
  function close() {
    setKey("");
    onClose();
  }
  function resetModels() {
    setModels([]);
    setSelected([]);
    setError("");
  }
  async function discover() {
    setPending("discover");
    setError("");
    try {
      const result = await api<{ models: GatewayModel[]; source: string }>(
        "/gateway/discover",
        {
          preset_id: preset.id,
          endpoint: endpoint.trim(),
          ...(key ? { api_key: key } : {}),
          ...(authAccount ? { auth_account_id: authAccount.id } : {}),
          ...(connection ? { connection_id: connection.id } : {}),
        },
      );
      setModels(result.models);
      setSelected((current) =>
        current.filter((id) => result.models.some((model) => model.id === id)),
      );
      if (!result.models.length)
        setError(
          "This provider returned no models. Check your account access, or add a model ID in Advanced.",
        );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not load models. Check the endpoint and API key, then try again.",
      );
    } finally {
      setPending(null);
    }
  }
  async function connect() {
    setPending("connect");
    setError("");
    try {
      await api(
        `/gateway/connections${connection ? `/${encodeURIComponent(connection.id)}` : ""}`,
        {
          preset_id: preset.id,
          name: name.trim(),
          endpoint: endpoint.trim(),
          ...(key ? { api_key: key } : {}),
          ...(authAccount ? { auth_account_id: authAccount.id } : {}),
          models: selected,
          ...(connection ? { expected_revision: connection.revision } : {}),
        },
        { method: connection ? "PATCH" : "POST" },
      );
      setKey("");
      await client.invalidateQueries({ queryKey: ["gateway", accountId] });
      close();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not connect. Check your details and try again.",
      );
    } finally {
      setPending(null);
    }
  }
  function toggle(id: string) {
    setSelected((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id],
    );
  }
  function addManual() {
    const id = manual.trim();
    if (!id) return;
    if (!models.some((model) => model.id === id))
      setModels((current) => [
        ...current,
        { id, name: id, context_limit: 0, output_limit: 0, tools: false },
      ]);
    setSelected((current) =>
      current.includes(id) ? current : [...current, id],
    );
    setManual("");
    setSearch("");
  }
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open && !pending) close();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="st-overlay gateway-overlay" />
        <Dialog.Content
          className="gateway-drawer"
          onOpenAutoFocus={() => {
            focus.current = document.activeElement as HTMLElement;
          }}
          onCloseAutoFocus={(event) => {
            if (focus.current?.isConnected) {
              event.preventDefault();
              focus.current.focus();
            }
          }}
        >
          <header className="gateway-drawer-header">
            <div>
              <p className="eyebrow">Model gateway</p>
              <Dialog.Title>
                {connection ? "Edit connection" : `Connect ${preset.name}`}
              </Dialog.Title>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Close connection drawer"
              disabled={!!pending}
              onClick={close}
            >
              <X size={20} />
            </Button>
          </header>
          <Dialog.Description className="gateway-drawer-intro">
            {preset.description}
          </Dialog.Description>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void connect();
            }}
            className="gateway-drawer-form"
          >
            <div className="gateway-drawer-body">
              <div className="gateway-fields">
                <label>
                  Provider name
                  <Input
                    value={name}
                    required
                    disabled={!!pending}
                    onChange={(event) => setName(event.target.value)}
                    autoComplete="off"
                  />
                </label>
                <label>
                  Endpoint
                  <Input
                    aria-label="Endpoint"
                    aria-describedby="gateway-endpoint-hint"
                    value={endpoint}
                    type="url"
                    required
                    disabled={!!pending}
                    placeholder="https://api.example.com/v1"
                    onChange={(event) => {
                      setEndpoint(event.target.value);
                      resetModels();
                    }}
                  />
                  <span className="field-hint" id="gateway-endpoint-hint">
                    The API base URL is filled in for you. Change it for a proxy
                    or your own server.
                  </span>
                </label>
                {!authAccount && (
                  <label>
                    <span>
                      API key{" "}
                      {preset.auth === "none" && (
                        <small className="field-hint">(optional)</small>
                      )}
                    </span>
                    <Input
                      type="password"
                      name="gateway-provider-secret"
                      value={key}
                      required={keyRequired}
                      autoComplete="new-password"
                      spellCheck={false}
                      disabled={!!pending}
                      placeholder={
                        connection?.credential_configured
                          ? "Saved securely · leave blank to keep"
                          : preset.auth === "none"
                            ? "No key needed for this provider"
                            : "Paste your provider API key"
                      }
                      onChange={(event) => {
                        setKey(event.target.value);
                        resetModels();
                      }}
                    />
                  </label>
                )}
                {authAccount && (
                  <p className="gateway-active-model">
                    <ShieldCheck size={16} />
                    Signed in as {authAccount.name}
                  </p>
                )}
                {preset.key_url && !authAccount && (
                  <a
                    className="gateway-key-link"
                    href={preset.key_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <KeyRound size={14} />
                    {preset.auth === "none"
                      ? `Open ${preset.name} setup guide`
                      : `Get a ${preset.name} API key`}
                    <ExternalLink size={13} />
                  </a>
                )}
              </div>
              <section
                className="gateway-import"
                aria-labelledby="gateway-import-title"
              >
                <div className="gateway-section-heading">
                  <div>
                    <h3 id="gateway-import-title">Choose your models</h3>
                    <p>
                      Load the live catalog, then choose what to make available.
                    </p>
                  </div>
                  <Button
                    onClick={discover}
                    disabled={!canLoad || !!pending}
                    loading={pending === "discover"}
                  >
                    <Download size={16} />
                    Load models
                  </Button>
                </div>
                {models.length > 0 ? (
                  <>
                    <div className="gateway-model-toolbar">
                      <div className="gateway-search">
                        <Search size={16} />
                        <Input
                          aria-label="Search models"
                          placeholder="Search models"
                          value={search}
                          onChange={(event) => setSearch(event.target.value)}
                        />
                      </div>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={!!pending}
                        onClick={() =>
                          setSelected((current) => [
                            ...new Set([
                              ...current,
                              ...visible.map((model) => model.id),
                            ]),
                          ])
                        }
                      >
                        Select visible
                      </Button>
                    </div>
                    <div className="gateway-model-list">
                      {visible.map((model) => (
                        <label
                          className={`gateway-model-option ${selected.includes(model.id) ? "is-selected" : ""}`}
                          key={model.id}
                        >
                          <input
                            type="checkbox"
                            checked={selected.includes(model.id)}
                            disabled={!!pending}
                            onChange={() => toggle(model.id)}
                          />
                          <span>
                            <strong>{model.name || model.id}</strong>
                            <small>{model.id}</small>
                          </span>
                          {model.tools && (
                            <span className="gateway-capability">Tools</span>
                          )}
                        </label>
                      ))}
                      {!visible.length && (
                        <p className="gateway-muted">
                          No models match your search.
                        </p>
                      )}
                    </div>
                    <p className="field-hint" aria-live="polite">
                      {selected.length} of {models.length} models selected
                    </p>
                  </>
                ) : (
                  <div className="gateway-import-empty">
                    <Download size={23} />
                    <p>Your available models will appear here.</p>
                  </div>
                )}
                <details className="gateway-advanced">
                  <summary>Advanced: add a model ID</summary>
                  <p className="field-hint">
                    For servers that don’t offer a model catalog. Use the exact
                    model ID from your provider.
                  </p>
                  <div className="row">
                    <Input
                      aria-label="Model ID"
                      value={manual}
                      disabled={!!pending}
                      onChange={(event) => setManual(event.target.value)}
                      placeholder="e.g. my-local-model"
                    />
                    <Button
                      aria-label="Add model ID"
                      disabled={!manual.trim() || !!pending}
                      onClick={addManual}
                    >
                      <Plus size={17} />
                    </Button>
                  </div>
                </details>
              </section>
              {error && (
                <div className="gateway-error" role="alert">
                  {error}
                </div>
              )}
            </div>
            <footer className="gateway-drawer-footer">
              <p>
                <ShieldCheck size={16} />
                Keys are stored securely by your gateway.
              </p>
              <div className="row">
                <Button variant="ghost" disabled={!!pending} onClick={close}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  disabled={
                    !name.trim() || !canLoad || !selected.length || !!pending
                  }
                  loading={pending === "connect"}
                >
                  {connection ? <Check size={17} /> : <ArrowRight size={17} />}
                  {connection ? "Save connection" : "Connect provider"}
                </Button>
              </div>
            </footer>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
