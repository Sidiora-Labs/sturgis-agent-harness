import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";

/** Installation metadata is scoped to the signed-in account, never an agent. */
export function useGatewayQuery<T>(accountId: string, resource: string) {
  return useQuery({
    queryKey: ["gateway", accountId, resource],
    queryFn: ({ signal }) =>
      api<T>(`/gateway/${resource}`, undefined, { signal }),
    staleTime: 15000,
    retry: false,
  });
}

/** Direct requests keep credentials and one-time keys out of mutation caches. */
export function useGatewayAction(accountId: string) {
  const client = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function run<T>(
    path: string,
    body?: unknown,
    method?: string,
  ): Promise<T | undefined> {
    setPending(true);
    setError("");
    try {
      const result = await api<T>(`/gateway/${path}`, body, { method });
      await client.invalidateQueries({ queryKey: ["gateway", accountId] });
      return result;
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The gateway could not complete this request. Try again.",
      );
      return undefined;
    } finally {
      setPending(false);
    }
  }
  return { run, pending, error };
}
