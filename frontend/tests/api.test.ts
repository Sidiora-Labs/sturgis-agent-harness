import { afterEach, describe, expect, it, vi } from "vitest";
import {
  api,
  ApiError,
  command,
  configureApiSession,
  decodeText,
  downloadBody,
  fileBase64,
  requestId,
} from "../src/lib/api";
import { Blob as NodeBlob } from "node:buffer";
const response = (
  data: unknown,
  status = 200,
  version = "consumer/1",
  error: unknown = null,
) => new Response(JSON.stringify({ version, data, error }), { status });
afterEach(() => {
  document.cookie = "sturgis_csrf=; Max-Age=0; path=/";
  document.cookie = "sturgis_gateway_csrf=; Max-Age=0; path=/";
  configureApiSession("consumer");
});

describe("consumer API requests", () => {
  it("keeps standalone and consumer CSRF cookies independent when both share a host", async () => {
    document.cookie = "sturgis_csrf=consumer%20token; path=/";
    document.cookie = "sturgis_gateway_csrf=gateway%20token; path=/";
    const fetcher = vi
      .fn()
      .mockImplementation(async () => response({ ok: true }));
    vi.stubGlobal("fetch", fetcher);
    // Gateway paths embedded in the consumer still use the consumer session.
    await api("/gateway/settings", { expected_revision: 0 });
    expect(fetcher.mock.calls[0][1].headers["X-CSRF-Token"]).toBe(
      "consumer token",
    );
    configureApiSession("gateway");
    await api("/gateway/settings", { expected_revision: 0 });
    expect(fetcher.mock.calls[1][1].headers["X-CSRF-Token"]).toBe(
      "gateway token",
    );
    document.cookie = "sturgis_gateway_csrf=; Max-Age=0; path=/";
    await api("/gateway/session", {}, { method: "DELETE" });
    expect(fetcher.mock.calls[2][1].headers["X-CSRF-Token"]).toBe("");
    configureApiSession("consumer");
    await api("/agents/id", { name: "still signed in" }, { method: "PATCH" });
    expect(fetcher.mock.calls[3][1].headers["X-CSRF-Token"]).toBe(
      "consumer token",
    );
  });

  it("accepts the standalone envelope only within the gateway API boundary", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(async () =>
          response({ ok: true }, 200, "gateway/1"),
        ),
    );
    expect(await api("/gateway/status")).toEqual({ ok: true });
    await expect(api("/agents")).rejects.toMatchObject({
      code: "version_mismatch",
    });
  });
  it("uses cookie auth, CSRF on writes and stable explicit request IDs without caller authority", async () => {
    document.cookie = "sturgis_csrf=encoded%20token; path=/";
    const fetcher = vi
      .fn()
      .mockImplementation(async () => response({ ok: true }));
    vi.stubGlobal("fetch", fetcher);
    const controller = new AbortController();
    expect(await api("/health")).toEqual({ ok: true });
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/health");
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: "GET",
      headers: {},
      credentials: "same-origin",
      body: undefined,
    });
    await command(
      "owned",
      "files.get",
      { path: "notes.txt" },
      { requestId: "retry-stable", signal: controller.signal },
    );
    expect(fetcher.mock.calls[1][1]).toMatchObject({
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": "encoded token",
      },
      signal: controller.signal,
    });
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
      params: { path: "notes.txt" },
      request_id: "retry-stable",
    });
    await api("/agents/id", { name: "renamed" }, { method: "PATCH" });
    expect(fetcher.mock.calls[2][1].method).toBe("PATCH");
    await command("owned", "overview.get");
    expect(JSON.parse(fetcher.mock.calls[3][1].body).request_id).toMatch(
      /^[0-9a-f-]{36}$/,
    );
    expect(requestId()).not.toBe(requestId());
  });
  it("returns typed service/version errors and propagates transport failure", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        response(null, 403, "consumer/1", {
          code: "forbidden",
          message: "Access denied",
        }),
      )
      .mockResolvedValueOnce(response(null, 503))
      .mockResolvedValueOnce(response({}, 200, "obsolete"))
      .mockRejectedValueOnce(new TypeError("Network unavailable"));
    vi.stubGlobal("fetch", fetcher);
    await expect(api("/x", {})).rejects.toMatchObject({
      name: "ApiError",
      code: "forbidden",
      status: 403,
      message: "Access denied",
    });
    expect(fetcher.mock.calls[0][1].headers["X-CSRF-Token"]).toBe("");
    await expect(api("/x")).rejects.toMatchObject({
      code: "request_failed",
      status: 503,
    });
    await expect(api("/x")).rejects.toMatchObject({
      code: "version_mismatch",
      status: 409,
    });
    await expect(api("/x")).rejects.toThrow("Network unavailable");
  });
});
it("converts exact bounded binary attachments and rejects oversized or invalid UTF-8 data", async () => {
  const bytes = new Uint8Array(20000).map((_, index) => index % 256);
  expect(await fileBase64(new NodeBlob([bytes]) as unknown as Blob)).toBe(
    Buffer.from(bytes).toString("base64"),
  );
  expect(await fileBase64(new NodeBlob([]) as unknown as Blob)).toBe("");
  await expect(
    fileBase64(
      new NodeBlob([new Uint8Array(4 * 1024 * 1024 + 1)]) as unknown as Blob,
    ),
  ).rejects.toBeInstanceOf(ApiError);
  expect(decodeText(Buffer.from("Cedar — evidence").toString("base64"))).toBe(
    "Cedar — evidence",
  );
  expect(() => decodeText("/w==")).toThrow();
});
it("downloads through a short-lived object URL and releases it", () => {
  vi.useFakeTimers();
  const create = vi.fn().mockReturnValue("blob:test"),
    revoke = vi.fn();
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: create,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: revoke,
  });
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe("evidence.txt");
      expect(this.href).toBe("blob:test");
    });
  const body = new Blob(["evidence"]);
  downloadBody(body, "evidence.txt");
  expect(create).toHaveBeenCalledWith(body);
  expect(click).toHaveBeenCalledOnce();
  expect(revoke).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1000);
  expect(revoke).toHaveBeenCalledWith("blob:test");
});
