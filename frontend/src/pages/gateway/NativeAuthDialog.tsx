import { useEffect, useState } from "react";
import { Check, Copy, ExternalLink, LogIn, RefreshCw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Input, Modal } from "../../components/ui";
import { api } from "../../lib/api";
import type { GatewayAuthProvider, GatewayAuthSession } from "../../lib/types";

/** Device and browser sign-in use server-owned sessions; tokens never reach JS. */
export default function NativeAuthDialog({
  accountId,
  provider,
  onClose,
  onConnected,
}: {
  accountId: string;
  provider: GatewayAuthProvider;
  onClose: () => void;
  onConnected: (id: string, name: string) => void;
}) {
  const client = useQueryClient();
  const [name, setName] = useState(provider.name);
  const [options, setOptions] = useState<Record<string, string>>({});
  const [session, setSession] = useState<GatewayAuthSession | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [callback, setCallback] = useState("");
  const [copied, setCopied] = useState(false);
  const waiting =
    session?.status === "pending" || session?.status === "exchanging";
  async function accept(next: GatewayAuthSession) {
    setSession(next);
    if (next.status === "connected" && next.account_id) {
      setCallback("");
      await client.invalidateQueries({ queryKey: ["gateway", accountId] });
      onConnected(next.account_id, next.name);
    } else if (["failed", "expired", "cancelled"].includes(next.status)) {
      setError(
        next.status === "expired"
          ? "This sign-in expired. Start again to get a new link or code."
          : next.status === "cancelled"
            ? "Sign-in was cancelled. You can start again."
            : `Sign-in could not finish${next.error_code ? ` (${next.error_code})` : ""}. Start again or check your provider account.`,
      );
    }
  }
  async function request(path: string, body: unknown) {
    setPending(true);
    setError("");
    try {
      await accept(
        await api<GatewayAuthSession>(`/gateway/auth/${path}`, body),
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not contact the sign-in service. Try again.",
      );
    } finally {
      setPending(false);
    }
  }
  useEffect(() => {
    if (!session || !waiting || pending || error) return;
    const timer = setTimeout(
      () => {
        void request(`${encodeURIComponent(session.id)}/poll`, {});
      },
      Math.max(1, session.poll_after || 5) * 1000,
    );
    return () => clearTimeout(timer);
  }, [session, waiting, pending, error]);
  async function close() {
    if (session && waiting) {
      setPending(true);
      try {
        await api(
          `/gateway/auth/${encodeURIComponent(session.id)}`,
          {},
          { method: "DELETE" },
        );
      } catch (caught) {
        setError(
          caught instanceof Error
            ? caught.message
            : "Could not cancel sign-in. Try again.",
        );
        setPending(false);
        return;
      }
    }
    setCallback("");
    onClose();
  }
  async function complete() {
    try {
      const parsed = new URL(callback);
      const code = parsed.searchParams.get("code"),
        state = parsed.searchParams.get("state");
      if (!code || !state) throw new Error();
      await request(`${encodeURIComponent(session!.id)}/complete`, {
        code,
        state,
      });
    } catch {
      setError(
        "Paste the complete callback URL, including its code and state.",
      );
    }
  }
  const url =
    session?.verification_uri_complete ||
    session?.verification_uri ||
    session?.authorization_url;
  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open && !pending) void close();
      }}
      title={`Sign in to ${provider.name}`}
      description={provider.description}
    >
      <div className="gateway-fields">
        {!session || !waiting ? (
          <>
            <label>
              Connection name
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            {!!provider.options.length && (
              <details className="gateway-advanced">
                <summary>Advanced account options</summary>
                {provider.options
                  .filter(
                    (option): option is string =>
                      typeof option === "string" &&
                      ["region", "start_url", "project_id"].includes(option),
                  )
                  .map((option) => (
                    <label key={option}>
                      {
                        {
                          region: "AWS region",
                          start_url: "AWS access portal URL",
                          project_id: "Google Cloud project ID",
                        }[option]
                      }
                      <Input
                        value={options[option] || ""}
                        placeholder="Use provider default"
                        onChange={(event) =>
                          setOptions((current) => ({
                            ...current,
                            [option]: event.target.value,
                          }))
                        }
                      />
                    </label>
                  ))}
              </details>
            )}
            <Button
              variant="primary"
              loading={pending}
              disabled={!name.trim()}
              onClick={() =>
                request("start", {
                  provider_id: provider.id,
                  name: name.trim(),
                  ...(Object.values(options).some((value) => value.trim())
                    ? {
                        options: Object.fromEntries(
                          Object.entries(options)
                            .filter(([, value]) => value.trim())
                            .map(([key, value]) => [key, value.trim()]),
                        ),
                      }
                    : {}),
                  ...(provider.default_redirect_uri
                    ? { redirect_uri: provider.default_redirect_uri }
                    : {}),
                })
              }
            >
              <LogIn size={17} />
              {session ? "Start again" : `Continue with ${provider.name}`}
            </Button>
          </>
        ) : (
          <>
            {session.user_code && (
              <div className="gateway-device-code">
                <span>Enter this code on your provider’s page</span>
                <strong>{session.user_code}</strong>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(session.user_code!);
                      setCopied(true);
                    } catch {
                      setError(
                        "Clipboard unavailable. Select and copy the code above.",
                      );
                    }
                  }}
                >
                  {copied ? <Check size={15} /> : <Copy size={15} />}
                  {copied ? "Copied" : "Copy code"}
                </Button>
              </div>
            )}
            {url && (
              <a
                className="st-button st-button--primary"
                href={url}
                target="_blank"
                rel="noreferrer"
              >
                Open {provider.name} sign-in
                <ExternalLink size={16} />
              </a>
            )}
            <p className="field-hint" role="status">
              {session.callback_mode === "manual"
                ? "Automatic return is unavailable. Finish signing in, then use the callback URL option below."
                : "Finish signing in on the provider’s page. This window will update when your account is connected."}
            </p>
            <Button
              loading={pending}
              onClick={() =>
                request(`${encodeURIComponent(session.id)}/poll`, {})
              }
            >
              <RefreshCw size={16} />
              Check sign-in
            </Button>
            {session.flow === "authorization_code_pkce" && (
              <details className="gateway-advanced">
                <summary>
                  {session.callback_mode === "manual"
                    ? "Complete with callback URL"
                    : "Sign-in did not return automatically?"}
                </summary>
                <p className="field-hint">
                  Copy the final callback URL from your browser and paste it
                  here. The code is sent directly to your gateway.
                </p>
                <label>
                  Callback URL
                  <Input
                    type="password"
                    value={callback}
                    autoComplete="off"
                    onChange={(event) => setCallback(event.target.value)}
                  />
                </label>
                <Button disabled={!callback || pending} onClick={complete}>
                  Complete sign-in
                </Button>
              </details>
            )}
          </>
        )}
        {error && (
          <p className="gateway-error" role="alert">
            {error}
          </p>
        )}
        <Button variant="ghost" disabled={pending} onClick={close}>
          Cancel
        </Button>
      </div>
    </Modal>
  );
}
