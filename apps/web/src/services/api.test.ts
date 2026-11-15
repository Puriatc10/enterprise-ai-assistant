/**
 * The API client's error contract.
 *
 * Every case here is a response the backend actually produces. What matters is that the
 * client never shows a bare status code, never loses the request id, and never mistakes a
 * network failure for a rejection by the server — the three ways a Persian UI turns into a
 * useless one.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, sendMessage, listConversations } from "./api";

interface FakeResponse {
  ok: boolean;
  status: number;
  headers: { get: (name: string) => string | null };
  json: () => Promise<unknown>;
  text: () => Promise<string>;
}

function fakeResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): FakeResponse {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => headers[name.toLowerCase()] ?? null },
    json: async () => JSON.parse(text) as unknown,
    text: async () => text,
  };
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("error envelope", () => {
  it("carries the backend's message, code and request id", async () => {
    fetchMock.mockResolvedValue(
      fakeResponse(404, {
        error: {
          code: "conversation_not_found",
          message: "That conversation does not exist.",
          request_id: "req-404",
        },
      }),
    );

    const failure = await listConversations("token").catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    const error = failure as ApiError;
    expect(error.status).toBe(404);
    expect(error.code).toBe("conversation_not_found");
    expect(error.requestId).toBe("req-404");
    expect(error.message).toContain("does not exist");
  });

  it("falls back to the X-Request-ID header when the body has none", async () => {
    fetchMock.mockResolvedValue(
      fakeResponse(
        502,
        "<html>Bad gateway</html>",
        { "x-request-id": "header-id" },
      ),
    );

    const error = (await listConversations("token").catch((e: unknown) => e)) as ApiError;

    expect(error.status).toBe(502);
    expect(error.requestId).toBe("header-id");
  });

  it("describes a non-JSON failure without pretending to parse it", async () => {
    fetchMock.mockResolvedValue(fakeResponse(503, "<html>upstream down</html>"));

    const error = (await listConversations("token").catch((e: unknown) => e)) as ApiError;

    expect(error.status).toBe(503);
    expect(error.code).toBe("http_503");
    expect(error.message.length).toBeGreaterThan(0);
  });
});

describe("transport failures", () => {
  it("reports an unreachable backend as a network failure, not a rejection", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const error = (await listConversations("token").catch((e: unknown) => e)) as ApiError;

    expect(error.status).toBe(0);
    expect(error.code).toBe("network");
  });

  it("reports an abort as a timeout, with the seconds it waited", async () => {
    fetchMock.mockRejectedValue(new DOMException("aborted", "AbortError"));

    const error = (await listConversations("token").catch((e: unknown) => e)) as ApiError;

    expect(error.status).toBe(0);
    expect(error.code).toBe("timeout");
    expect(error.message).toMatch(/\d/);
  });
});

describe("requests", () => {
  it("sends the bearer token and no body on a GET", async () => {
    fetchMock.mockResolvedValue(fakeResponse(200, []));

    await listConversations("my-token");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/conversations(\?|$)/);
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer my-token");
    expect(init.method ?? "GET").toBe("GET");
    expect(init.body).toBeUndefined();
  });

  it("posts the message and returns the tools that ran", async () => {
    fetchMock.mockResolvedValue(
      fakeResponse(200, {
        conversation_id: "conv-1",
        answer: "شما سه بیمه‌نامه دارید.",
        model: "openrouter/x",
        usage: null,
        tools_used: [{ tool: "get_my_policy_count", status: "ok", latency_ms: 12 }],
      }),
    );

    const result = await sendMessage("my-token", "conv-1", "چند بیمه‌نامه دارم؟");

    expect(result.answer).toContain("سه بیمه‌نامه");
    expect(result.tools_used).toEqual([
      { tool: "get_my_policy_count", status: "ok", latency_ms: 12 },
    ]);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/conversations\/conv-1\/messages$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ content: "چند بیمه‌نامه دارم؟" });
  });
});
