import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  KeyRound,
  LogOut,
  Network,
  ShieldCheck,
} from "lucide-react";
import { BrandMark } from "../../components/Shell";
import { Button, Input } from "../../components/ui";
import { api } from "../../lib/api";
import { QueryState } from "../shared";
import GatewayPage from "./GatewayPage";
import "./gateway.css";

/** The gateway can serve its own dashboard without loading the agent app. */
export default function StandaloneGateway() {
  const client = useQueryClient();
  const session = useQuery({
    queryKey: ["gateway-session"],
    queryFn: ({ signal }) =>
      api<{ authenticated: boolean }>("/gateway/session", undefined, {
        signal,
      }),
    retry: false,
    staleTime: Infinity,
  });
  const [key, setKey] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function login() {
    setPending(true);
    setError("");
    try {
      const result = await api<{ authenticated: boolean }>("/gateway/session", {
        admin_key: key,
      });
      setKey("");
      client.setQueryData(["gateway-session"], result);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Sign-in failed. Check your gateway administrator key.",
      );
    } finally {
      setPending(false);
    }
  }
  async function logout() {
    setPending(true);
    setError("");
    try {
      await api("/gateway/session", {}, { method: "DELETE" });
      client.clear();
      client.setQueryData(["gateway-session"], { authenticated: false });
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not sign out. Try again.",
      );
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="gateway-standalone">
      <a className="st-skip-link" href="#gateway-main">
        Skip to content
      </a>
      <header className="gateway-standalone-header">
        <div className="row">
          <BrandMark />
          <strong>sturgis.</strong>
          <span>Model Gateway</span>
        </div>
        {session.data?.authenticated && (
          <Button variant="ghost" loading={pending} onClick={logout}>
            <LogOut size={16} />
            Sign out
          </Button>
        )}
      </header>
      <main id="gateway-main" tabIndex={-1}>
        <QueryState
          loading={session.isPending}
          error={session.error}
          retry={() => session.refetch()}
        />
        {session.data?.authenticated ? (
          <>
            <GatewayPage accountId="gateway-admin" />
            {error && (
              <p className="gateway-error" role="alert">
                {error}
              </p>
            )}
          </>
        ) : (
          session.data && (
            <div className="gateway-login">
              <section className="gateway-login-story">
                <Network size={38} />
                <p className="eyebrow">An open door to your models</p>
                <h1>
                  One gateway.
                  <br />
                  Your choice of AI.
                </h1>
                <p>
                  Connect providers, bring your local models, and give every app
                  a single place to connect.
                </p>
                <div className="row">
                  <ShieldCheck size={17} />
                  <span>Standalone. Private. Yours.</span>
                </div>
              </section>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void login();
                }}
              >
                <span className="gateway-provider-monogram">
                  <KeyRound size={22} />
                </span>
                <h2>Welcome to your gateway</h2>
                <p>
                  Enter the administrator key created when you started the
                  gateway.
                </p>
                <label className="gateway-fields">
                  Administrator key
                  <Input
                    type="password"
                    name="gateway-admin-secret"
                    autoComplete="new-password"
                    value={key}
                    required
                    disabled={pending}
                    onChange={(event) => setKey(event.target.value)}
                    placeholder="Paste your administrator key"
                  />
                </label>
                {error && (
                  <p className="gateway-error" role="alert">
                    {error}
                  </p>
                )}
                <Button
                  type="submit"
                  variant="primary"
                  loading={pending}
                  disabled={!key.trim()}
                >
                  <ArrowRight size={17} />
                  Open gateway
                </Button>
                <p className="field-hint">
                  The key stays on this installation. Your browser receives a
                  private session cookie.
                </p>
              </form>
            </div>
          )
        )}
      </main>
    </div>
  );
}
