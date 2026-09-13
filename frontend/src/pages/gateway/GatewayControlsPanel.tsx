import { useState } from "react";
import { Download, Upload } from "lucide-react";
import { Button, Input, Modal } from "../../components/ui";
import { api } from "../../lib/api";
import type { GatewaySettings } from "../../lib/types";
import { QueryState } from "../shared";
import { useGatewayAction, useGatewayQuery } from "./useGateway";

/** Operational limits and metadata transfer never carry provider credentials. */
export default function GatewayControlsPanel({
  accountId,
}: {
  accountId: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="gateway-advanced"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Advanced: limits & configuration transfer</summary>
      {open && <Controls accountId={accountId} />}
    </details>
  );
}

function Controls({ accountId }: { accountId: string }) {
  const settings = useGatewayQuery<GatewaySettings>(accountId, "settings");
  const action = useGatewayAction(accountId);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [archive, setArchive] = useState<{
    filename: string;
    value: { connections: unknown[]; routes: unknown[] };
  } | null>(null);
  async function download() {
    setPending(true);
    setError("");
    setMessage("");
    try {
      const value = await api<unknown>("/gateway/export");
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(value, null, 2)], {
          type: "application/json",
        }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = "sturgis-gateway-config.json";
      link.click();
      URL.revokeObjectURL(url);
      setMessage(
        "Configuration downloaded. Provider credentials and app keys are excluded.",
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not download configuration. Try again.",
      );
    } finally {
      setPending(false);
    }
  }
  async function read(file: File) {
    setError("");
    setMessage("");
    setArchive(null);
    setPending(true);
    try {
      if (file.size > 2 * 1024 * 1024)
        throw new Error("Choose a gateway configuration file up to 2 MiB.");
      const value = JSON.parse(await file.text());
      if (
        !value ||
        value.format !== "sturgis-gateway-config/1" ||
        value.credentials_included !== false ||
        !Array.isArray(value.connections) ||
        !Array.isArray(value.routes)
      )
        throw new Error(
          "Choose a credential-free Sturgis gateway configuration export.",
        );
      setArchive({ filename: file.name, value });
    } catch (caught) {
      setError(
        caught instanceof SyntaxError
          ? "This file is not valid JSON. Choose a gateway configuration export."
          : caught instanceof Error
            ? caught.message
            : "Could not read this configuration file.",
      );
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="gateway-fields">
      <QueryState
        loading={settings.isPending}
        error={settings.error}
        retry={() => settings.refetch()}
      />
      {settings.data && (
        <SettingsForm
          key={settings.data.revision}
          accountId={accountId}
          settings={settings.data}
          onSaved={() => setMessage("Gateway limits saved.")}
        />
      )}
      <div className="gateway-transfer">
        <h3>Move your configuration</h3>
        <p className="field-hint">
          Download connection details, model lists, routes, and settings.
          Credentials, signed-in accounts, app keys, and usage stay here.
        </p>
        <Button onClick={download} loading={pending}>
          <Download size={16} />
          Download configuration
        </Button>
        <label>
          Import configuration file
          <Input
            type="file"
            accept="application/json,.json"
            disabled={pending || action.pending}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void read(file);
            }}
          />
        </label>
        <p className="field-hint">
          Import adds new connections and routes. Reconnect each provider and
          create new app keys before use. Existing configuration stays in place;
          exported limits are not applied.
        </p>
      </div>
      {error && (
        <p role="alert" className="gateway-error">
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      <Modal
        open={!!archive}
        onOpenChange={(next) => {
          if (!next && !action.pending) setArchive(null);
        }}
        title="Import gateway configuration?"
        description={`${archive?.filename || "This export"} will add ${archive?.value.connections.length || 0} connections and ${archive?.value.routes.length || 0} routes with new IDs. Reconnect providers before use.`}
      >
        <div className="row">
          <Button disabled={action.pending} onClick={() => setArchive(null)}>
            Cancel import
          </Button>
          <Button
            variant="primary"
            loading={action.pending}
            onClick={async () => {
              const result = await action.run<{
                imported_connections: number;
                imported_routes: number;
              }>("import", { archive: archive!.value });
              if (result) {
                setArchive(null);
                setMessage(
                  `Imported ${result.imported_connections} connections and ${result.imported_routes} routes. Reconnect each provider, copy its new model IDs, and create app keys. Gateway limits were not changed.`,
                );
              }
            }}
          >
            <Upload size={16} />
            Import configuration
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

function SettingsForm({
  accountId,
  settings,
  onSaved,
}: {
  accountId: string;
  settings: GatewaySettings;
  onSaved: () => void;
}) {
  const action = useGatewayAction(accountId);
  const [timeout, setTimeoutValue] = useState(String(settings.request_timeout));
  const [concurrency, setConcurrency] = useState(
    String(settings.max_concurrency),
  );
  const [rpm, setRpm] = useState(String(settings.requests_per_minute));
  return (
    <form
      className="gateway-fields"
      onSubmit={async (event) => {
        event.preventDefault();
        if (
          await action.run("settings", {
            expected_revision: settings.revision,
            request_timeout: Number(timeout),
            max_concurrency: Number(concurrency),
            requests_per_minute: Number(rpm),
          })
        )
          onSaved();
      }}
    >
      <h3>Gateway limits</h3>
      <p className="field-hint">
        These limits apply to this gateway account. The operator sets the
        maximum timeout and concurrency.
      </p>
      <div className="gateway-limit-fields">
        <label>
          Request timeout (seconds)
          <Input
            type="number"
            min="0.1"
            step="0.1"
            max={settings.operator_limits.request_timeout}
            value={timeout}
            required
            onChange={(event) => setTimeoutValue(event.target.value)}
          />
        </label>
        <label>
          Concurrent requests
          <Input
            type="number"
            min="1"
            max={settings.operator_limits.max_concurrency}
            value={concurrency}
            required
            onChange={(event) => setConcurrency(event.target.value)}
          />
        </label>
        <label>
          Requests per minute
          <Input
            type="number"
            min="1"
            max="10000"
            value={rpm}
            required
            onChange={(event) => setRpm(event.target.value)}
          />
        </label>
      </div>
      <Button type="submit" loading={action.pending}>
        Save gateway limits
      </Button>
      {action.error && (
        <p role="alert" className="gateway-error">
          {action.error}
        </p>
      )}
    </form>
  );
}
