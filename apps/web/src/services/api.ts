/**
 * HTTP access layer for the FastAPI backend. All URLs live here so components
 * and hooks never build a path or set a header themselves.
 *
 * Contract notes:
 * - Every authenticated call sends `Authorization: Bearer <access_token>`.
 *   The backend verifies the token against Supabase's JWKS; the frontend never
 *   validates or decodes it.
 * - Failures always arrive as `{"error": {"code", "message", "request_id"}}`.
 *   That message is surfaced as-is (the API is the source of truth for what went
 *   wrong); only transport-level failures get Persian copy, because there is no
 *   server message to quote then.
 */

import { env } from "../lib/env";
import type { HealthResponse } from "../types/api";

export const API_BASE_URL: string = env.apiBaseUrl;

const DEFAULT_TIMEOUT_MS = 10_000;

// A model answer can take ~30s (provider round-trip plus tool calls). Aborting
// at the 10s default would report a timeout for a request that was still going
// to succeed, so the send-message call gets a deliberate 60s budget.
const SEND_MESSAGE_TIMEOUT_MS = 60_000;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;

  constructor(message: string, status: number, code: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    // Assign only when present: `exactOptionalPropertyTypes` distinguishes
    // "absent" from "present and undefined", and a support ticket needs a real id.
    if (requestId !== undefined) {
      this.requestId = requestId;
    }
  }
}

export interface Conversation {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at: string;
}

export interface ToolUsage {
  tool: string;
  status: string;
  latency_ms: number;
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface SendMessageResult {
  conversation_id: string;
  answer: string;
  model: string;
  usage: Usage | null;
  tools_used: ToolUsage[];
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  /** JSON body. Never set for GET: a body on GET is rejected by some proxies. */
  body?: unknown;
  token?: string;
  timeoutMs?: number;
}

/** Parts of the backend error envelope, each narrowed independently. */
interface ErrorEnvelopeParts {
  code: string | null;
  message: string | null;
  requestId: string | null;
}

function readErrorMessage(status: number): string {
  // Persian fallback that always states the HTTP status, so a screenshot from a
  // user is enough to identify the failure class.
  switch (status) {
    case 400:
      return `درخواست نامعتبر بود (کد ${status}).`;
    case 401:
      return `نشست شما معتبر نیست یا منقضی شده است. لطفاً دوباره وارد شوید (کد ${status}).`;
    case 403:
      return `دسترسی به این بخش برای شما مجاز نیست (کد ${status}).`;
    case 404:
      return `موردی با این شناسه یافت نشد (کد ${status}).`;
    case 422:
      return `داده‌های ارسالی معتبر نیستند (کد ${status}).`;
    case 429:
      return `تعداد درخواست‌ها بیش از حد مجاز است. کمی صبر کنید و دوباره تلاش کنید (کد ${status}).`;
    case 502:
      return `سرویس هوش مصنوعی پاسخ معتبری برنگرداند. دوباره تلاش کنید (کد ${status}).`;
    case 503:
      return `سرویس هوش مصنوعی در دسترس نیست یا پیکربندی نشده است (کد ${status}).`;
    case 504:
      return `سرویس هوش مصنوعی به‌موقع پاسخ نداد. دوباره تلاش کنید (کد ${status}).`;
    default:
      return `خطای سرور (کد ${status}). لطفاً دوباره تلاش کنید.`;
  }
}

function readEnvelope(payload: unknown): ErrorEnvelopeParts | null {
  if (typeof payload !== "object" || payload === null) {
    return null;
  }

  const errorField = (payload as { error?: unknown }).error;
  if (typeof errorField !== "object" || errorField === null) {
    return null;
  }

  const candidate = errorField as {
    code?: unknown;
    message?: unknown;
    request_id?: unknown;
  };

  const code = typeof candidate.code === "string" ? candidate.code : null;
  const message = typeof candidate.message === "string" ? candidate.message : null;
  const requestId = typeof candidate.request_id === "string" ? candidate.request_id : null;

  if (code === null && message === null) {
    return null;
  }

  return { code, message, requestId };
}

async function readPayload(response: Response): Promise<unknown> {
  // Read as text first: a 502 from a proxy can be HTML, and calling
  // response.json() on it would throw before the status is ever reported.
  const text = await response.text();
  if (text.length === 0) {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function toApiError(response: Response, payload: unknown): ApiError {
  const envelope = readEnvelope(payload);
  const headerRequestId = response.headers.get("X-Request-ID");
  const requestId = envelope?.requestId ?? headerRequestId;

  const code = envelope?.code ?? `http_${response.status}`;
  const message = envelope?.message ?? readErrorMessage(response.status);

  return new ApiError(message, response.status, code, requestId ?? undefined);
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

function toSeconds(timeoutMs: number): number {
  return Math.round(timeoutMs / 1000);
}

/**
 * The only place that touches the network.
 *
 * `status: 0` marks everything that never produced an HTTP response, so the UI
 * can tell "the server said no" apart from "we never reached the server".
 */
async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }

  const init: RequestInit = {
    method,
    headers,
    signal: controller.signal,
    cache: "no-store",
  };

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, init);
    const payload = await readPayload(response);

    if (!response.ok) {
      throw toApiError(response, payload);
    }

    if (typeof payload !== "object" || payload === null) {
      // A success status with an unreadable body means a proxy or a wrong base
      // URL answered, not our API. Saying so beats a runtime crash downstream.
      throw new ApiError(
        `پاسخ سرور قابل خواندن نبود. آدرس سرویس را بررسی کنید (${API_BASE_URL}).`,
        response.status,
        "invalid_response",
      );
    }

    return payload as T;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (isAbortError(error)) {
      throw new ApiError(
        `پاسخ سرور در ${toSeconds(timeoutMs)} ثانیه نرسید. اتصال شبکه را بررسی کنید و دوباره تلاش کنید.`,
        0,
        "timeout",
      );
    }
    throw new ApiError(
      "ارتباط با سرور برقرار نشد. اتصال شبکه یا داخلی خود را بررسی کنید و مطمئن شوید سرویس در حال اجراست.",
      0,
      "network",
    );
  } finally {
    clearTimeout(timer);
  }
}

/** Liveness/readiness of the backend. Requires no credentials. */
export function fetchHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function listConversations(token: string): Promise<Conversation[]> {
  return request<Conversation[]>("/conversations?limit=20", { token });
}

export function createConversation(token: string, title?: string): Promise<Conversation> {
  const trimmed = title?.trim() ?? "";
  return request<Conversation>("/conversations", {
    method: "POST",
    token,
    body: trimmed.length > 0 ? { title: trimmed } : {},
  });
}

/** PATCH a conversation's title. The server strips it and rejects a blank one (422). */
export function renameConversation(
  token: string,
  conversationId: string,
  title: string,
): Promise<Conversation> {
  const id = encodeURIComponent(conversationId);
  return request<Conversation>(`/conversations/${id}`, {
    method: "PATCH",
    token,
    body: { title: title.trim() },
  });
}

/** DELETE a conversation. Another user's id answers 404, never 403. */
export function deleteConversation(token: string, conversationId: string): Promise<void> {
  const id = encodeURIComponent(conversationId);
  return request<void>(`/conversations/${id}`, { method: "DELETE", token });
}

export function listMessages(token: string, conversationId: string): Promise<ChatMessage[]> {
  const id = encodeURIComponent(conversationId);
  return request<ChatMessage[]>(`/conversations/${id}/messages?limit=100`, { token });
}

export function sendMessage(
  token: string,
  conversationId: string,
  content: string,
): Promise<SendMessageResult> {
  const id = encodeURIComponent(conversationId);
  return request<SendMessageResult>(`/conversations/${id}/messages`, {
    method: "POST",
    token,
    body: { content },
    timeoutMs: SEND_MESSAGE_TIMEOUT_MS,
  });
}
