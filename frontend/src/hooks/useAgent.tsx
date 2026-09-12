import { createContext, useContext, useState, useCallback } from "react";
import {
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { ApiError, api, command, requestId } from "../lib/api";
import type { Params, RuntimeState } from "../lib/types";
export interface AgentContextValue {
  agentId: string;
  accountId: string;
  notify: (message: string, tone?: "success" | "error") => void;
}
export const AgentContext = createContext<AgentContextValue | null>(null);
export function useAgent() {
  const value = useContext(AgentContext);
  if (!value) throw new Error("An active agent is required.");
  return value;
}
const runtimePauses = new Set<string>();
const runtimeKey = (
  accountId: string | null | undefined,
  agentId: string | null | undefined,
) => ["runtime", accountId, agentId] as const;
export function canWakeRuntime(state?: RuntimeState) {
  return (
    !!state &&
    !state.private_queries_paused &&
    ["running", "stopped"].includes(state.desired)
  );
}
export function useRuntimeState(
  accountId: string | null | undefined,
  agentId: string | null | undefined,
) {
  const client = useQueryClient();
  return useQuery({
    queryKey: runtimeKey(accountId, agentId),
    queryFn: async ({ signal }) => {
      try {
        const response = await api<{ runtime: RuntimeState }>(
          `/agents/${agentId}`,
          undefined,
          { signal },
        );
        return {
          ...response.runtime,
          private_queries_paused: runtimePauses.has(
            JSON.stringify(runtimeKey(accountId, agentId)),
          ),
        };
      } catch (error) {
        // A previously running observation cannot authorize background reads
        // after the control plane becomes unavailable.
        if (!signal.aborted)
          client.setQueryData<RuntimeState>(
            runtimeKey(accountId, agentId),
            (current) =>
              current ? { ...current, private_queries_paused: true } : current,
          );
        throw error;
      }
    },
    enabled: !!accountId && !!agentId,
    refetchInterval: 5000,
    staleTime: 1000,
    retry: false,
  });
}
export function pauseRuntimeQueries(
  client: QueryClient,
  accountId: string,
  agentId: string,
  paused: boolean,
) {
  const key = runtimeKey(accountId, agentId),
    identity = JSON.stringify(key);
  if (paused) {
    runtimePauses.add(identity);
    client.setQueryData<RuntimeState>(key, (current) =>
      current ? { ...current, private_queries_paused: true } : current,
    );
  } else {
    runtimePauses.delete(identity);
    // Keep the cache fenced until a fresh control-plane observation arrives.
    void client.invalidateQueries({ queryKey: key });
  }
}
export function useAgentQuery<T>(
  method: string,
  params: Params = {},
  options: { enabled?: boolean; interval?: number } = {},
) {
  const { agentId, accountId } = useAgent();
  const client = useQueryClient();
  const runtime = useRuntimeState(accountId, agentId);
  const enabled = canWakeRuntime(runtime.data) && options.enabled !== false;
  const query = useQuery({
    queryKey: ["agent", agentId, method, params],
    queryFn: ({ signal }) => {
      if (
        !canWakeRuntime(
          client.getQueryData<RuntimeState>(runtimeKey(accountId, agentId)),
        )
      )
        throw new ApiError(
          "Start the runtime before accessing private agent data.",
          "runtime_paused",
          409,
        );
      return command<T>(agentId, method, params, { signal });
    },
    enabled,
    refetchInterval: enabled ? options.interval : false,
    staleTime: 15000,
  });
  return {
    ...query,
    error: runtime.error || query.error,
    isPending: runtime.isPending || (enabled && query.isPending),
  };
}

// These are the consumer boundary's read operations. Unknown methods default to
// mutation recovery so a newly added effect cannot silently lose its receipt.
const reads = new Set(
  `overview conversations.list conversations.get messages.events attachments.list attachments.get attachments.read
files.list files.read knowledge.list knowledge.get memory.query memory.get providers.list providers.inspect providers.credentials.list
connections.list connections.get skills.list usage.summary artifacts.read work.overview work.list work.get goals.list commitments.list schedules.list
workflows.list workflows.get children.list children.get inbox.list permissions.list permissions.pending permissions.request devices.list devices.get
plugins.list plugins.inspect plugins.health diagnostics.get`.split(/\s+/),
);
const unresolved = new Set([
  "worker_timeout",
  "worker_indeterminate",
  "result_unavailable",
  "worker_protocol",
  "version_mismatch",
  "response_too_large",
  "internal_error",
  "request_failed",
  "cancelled",
  "control_restart",
]);
// These rejections can occur before a saved receipt is looked up. They prove
// the retry was denied, but cannot settle an earlier unknown acknowledgement.
const beforeReceipt = new Set([
  "unauthenticated",
  "csrf_failed",
  "forbidden",
  "origin_denied",
  "host_denied",
  "scope_denied",
  "not_found",
  "agent_archived",
  "runtime_archived",
  "worker_unavailable",
  "runtime_unavailable",
  "worker_draining",
  "rate_limited",
  "body_too_large",
]);
const inFlight = new Set<string>();
// Domain operation IDs belong to this recovery boundary. They are derived from
// the retained request identity, never regenerated by a retrying page form.
const operationOwned = new Set([
  "attachments.upload",
  "knowledge.add",
  "knowledge.delete",
  "memory.correct",
  "memory.retract",
]);
type Recovery = { version: 1; digest: string; requestId: string };

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([key, child]) => [key, canonical(child)]),
    );
  return value;
}
function stored(key: string): Recovery | null {
  let raw: string | null;
  try {
    raw = sessionStorage.getItem(key);
  } catch {
    throw new ApiError(
      "Request recovery storage is unavailable. Enable session storage before trying this action.",
      "recovery_unavailable",
      409,
    );
  }
  if (raw === null) return null;
  try {
    const value = JSON.parse(raw) as Recovery;
    if (
      !value ||
      value.version !== 1 ||
      Object.keys(value).sort().join(",") !== "digest,requestId,version" ||
      typeof value.digest !== "string" ||
      !/^[a-f0-9]{64}$/.test(value.digest) ||
      typeof value.requestId !== "string" ||
      !/^[A-Za-z0-9_-]{1,128}$/.test(value.requestId)
    )
      throw new Error();
    return value;
  } catch {
    throw new ApiError(
      "A saved action receipt cannot be read. Review runtime diagnostics before submitting a different action.",
      "recovery_invalid",
      409,
    );
  }
}
function persist(key: string, value: Recovery) {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    throw new ApiError(
      "Request recovery could not be saved. No action was sent.",
      "recovery_unavailable",
      409,
    );
  }
}
function clear(key: string, request: string) {
  if (stored(key)?.requestId === request) sessionStorage.removeItem(key);
}
async function fingerprint(
  params: Params,
  ownsOperationId: boolean,
): Promise<{ digest: string; snapshot: Params }> {
  let snapshot: Params;
  try {
    snapshot = JSON.parse(JSON.stringify(params)) as Params;
  } catch {
    throw new ApiError(
      "Action parameters must contain valid JSON.",
      "invalid_request",
      400,
    );
  }
  if (ownsOperationId) delete snapshot.operation_id;
  try {
    const hash = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(JSON.stringify(canonical(snapshot))),
    );
    return {
      snapshot,
      digest: Array.from(new Uint8Array(hash), (byte) =>
        byte.toString(16).padStart(2, "0"),
      ).join(""),
    };
  } catch {
    throw new ApiError(
      "Secure request recovery is unavailable. Open this installation over HTTPS or localhost.",
      "recovery_unavailable",
      409,
    );
  }
}

export function useAction() {
  const { agentId, accountId, notify } = useAgent();
  const client = useQueryClient();
  const [running, setRunning] = useState(0);
  const execute = useCallback(
    async <T,>(
      method: string,
      params: Params = {},
      message?: string,
    ): Promise<T | undefined> => {
      setRunning((count) => count + 1);
      let key: string | undefined,
        receipt: Recovery | undefined,
        ownsFlight = false,
        dispatched = false,
        acknowledged = false,
        retained = false;
      try {
        let snapshot = params;
        if (!reads.has(method)) {
          const prepared = await fingerprint(
            params,
            operationOwned.has(method),
          );
          snapshot = prepared.snapshot;
          key = `sturgis:pending-action:${encodeURIComponent(accountId)}:${encodeURIComponent(agentId)}:${encodeURIComponent(method)}`;
          const prior = stored(key);
          if (prior && prior.digest !== prepared.digest)
            throw new ApiError(
              "A previous action of this kind has an unknown outcome. Retry its original values or review runtime diagnostics before changing it.",
              "action_unresolved",
              409,
            );
          if (inFlight.has(key))
            throw new ApiError(
              "This action is already waiting for a response.",
              "action_pending",
              409,
            );
          retained = prior !== null;
          receipt = prior || {
            version: 1,
            digest: prepared.digest,
            requestId: requestId(),
          };
          persist(key, receipt);
          inFlight.add(key);
          ownsFlight = true;
          if (operationOwned.has(method))
            snapshot = { ...snapshot, operation_id: receipt.requestId };
        }
        dispatched = true;
        const result = await command<T>(agentId, method, snapshot, {
          requestId: receipt?.requestId,
        });
        acknowledged = true;
        if (key && receipt) clear(key, receipt.requestId);
        await client.invalidateQueries({ queryKey: ["agent", agentId] });
        if (message) notify(message);
        return result;
      } catch (error) {
        const uncertain =
          dispatched &&
          !acknowledged &&
          (!(error instanceof ApiError) ||
            unresolved.has(error.code) ||
            (retained && beforeReceipt.has(error.code)));
        if (dispatched && !acknowledged && !uncertain && key && receipt) {
          try {
            clear(key, receipt.requestId);
          } catch {
            /* Keep the receipt if storage became unavailable. */
          }
        }
        notify(
          uncertain && key
            ? "The action may have been accepted, but its outcome is unconfirmed. Retry the same values to check the original receipt, or review runtime diagnostics."
            : error instanceof Error
              ? error.message
              : "This action could not be completed.",
          "error",
        );
        return undefined;
      } finally {
        if (ownsFlight && key) inFlight.delete(key);
        setRunning((count) => count - 1);
      }
    },
    [agentId, accountId, client, notify],
  );
  return { execute, pending: running > 0 };
}
