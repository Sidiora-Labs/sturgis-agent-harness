import type { Params } from "./types";
export class ApiError extends Error {
  constructor(
    message: string,
    public code: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export const requestId = () => crypto.randomUUID();
let csrfCookie = "sturgis_csrf";
/** Separate independently hosted dashboards even when they share a hostname. */
export function configureApiSession(audience: "consumer" | "gateway") {
  csrfCookie = audience === "gateway" ? "sturgis_gateway_csrf" : "sturgis_csrf";
}
function csrf(): string {
  const prefix = csrfCookie + "=";
  const value = document.cookie
    .split("; ")
    .find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}
export async function api<T>(
  path: string,
  body?: unknown,
  options: { method?: string; signal?: AbortSignal } = {},
): Promise<T> {
  const response = await fetch("/api/v1" + path, {
    method: options.method || (body === undefined ? "GET" : "POST"),
    credentials: "same-origin",
    signal: options.signal,
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const envelope = (await response.json()) as {
    version: string;
    data: T;
    error: null | { code: string; message: string };
  };
  if (!response.ok || envelope.error)
    throw new ApiError(
      envelope.error?.message || "The service could not complete this request.",
      envelope.error?.code || "request_failed",
      response.status,
    );
  if (envelope.version !== "consumer/1" && !(path.startsWith("/gateway/") && envelope.version === "gateway/1"))
    throw new ApiError(
      "This client needs a compatible consumer service.",
      "version_mismatch",
      409,
    );
  return envelope.data;
}
export function command<T>(
  agent: string,
  method: string,
  params: Params = {},
  options: { requestId?: string; signal?: AbortSignal } = {},
): Promise<T> {
  return api<T>(
    `/agents/${agent}/commands/${method}`,
    { params, request_id: options.requestId || requestId() },
    { signal: options.signal },
  );
}
export async function fileBase64(file: Blob): Promise<string> {
  if (file.size > 4 * 1024 * 1024)
    throw new ApiError("Choose a file up to 4 MiB.", "content_limit", 413);
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 8192)
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  return btoa(binary);
}
export const decodeText = (base64: string) =>
  new TextDecoder("utf-8", { fatal: true }).decode(
    Uint8Array.from(atob(base64), (char) => char.charCodeAt(0)),
  );
export function downloadBody(body: Blob, name: string) {
  const url = URL.createObjectURL(body);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
