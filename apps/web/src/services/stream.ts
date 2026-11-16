/**
 * Server-sent events reader for `POST /conversations/{id}/messages/stream`.
 *
 * The stream is the browser's only view of a turn in progress, so this module has
 * three jobs and no others: put the question on the wire, turn bytes into events
 * as they arrive, and translate every failure into the same `ApiError` the JSON
 * endpoints already produce — the hook above it has one error shape to handle.
 *
 * Deliberately *not* buffered: `response.text()` would wait for the whole answer
 * and throw away the only reason this endpoint exists. `TextDecoder` with
 * `{stream: true}` is used instead, because a multi-byte Persian character can
 * be split across two chunks and decoding each chunk alone would corrupt it.
 */

import { API_BASE_URL, ApiError, type ToolUsage } from "./api";

export interface StreamHandlers {
  /** Append `text` to the answer the user is reading. Called 0..n times. */
  onToken: (text: string) => void;
  /** One tool ran while composing this answer. */
  onTool: (tool: ToolUsage) => void;
  /**
   * The authoritative final answer, sent once per successful turn. Never called
   * after an `error` event and never called at all if the turn was aborted.
   */
  onDone: (answer: string, conversationId: string, title: string | null) => void;
  /**
   * The turn named the conversation. Arrives *after* `onDone`, which is why the stream is
   * read to its end rather than stopped at `done`.
   */
  onTitle?: (title: string) => void;
}

/** Persian copy for a failure that carried no readable envelope. */
const HTTP_FALLBACK: Record<number, string> = {
  400: "درخواست نامعتبر بود.",
  401: "نشست شما معتبر نیست یا منقضی شده است. لطفاً دوباره وارد شوید.",
  403: "دسترسی به این بخش برای شما مجاز نیست.",
  404: "این گفتگو یافت نشد یا به شما تعلق ندارد.",
  422: "داده‌های ارسالی معتبر نیستند.",
  429: "تعداد درخواست‌ها بیش از حد مجاز است. کمی صبر کنید و دوباره تلاش کنید.",
  502: "سرویس هوش مصنوعی پاسخ معتبری برنگرداند. دوباره تلاش کنید.",
  503: "سرویس هوش مصنوعی در دسترس نیست یا پیکربندی نشده است.",
  504: "سرویس هوش مصنوعی به‌موقع پاسخ نداد. دوباره تلاش کنید.",
};

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

interface EnvelopeParts {
  code: string | null;
  message: string | null;
  requestId: string | null;
}

/** Same envelope as the JSON endpoints; each field narrowed independently. */
function readEnvelope(payload: unknown): EnvelopeParts | null {
  if (!isRecord(payload)) return null;
  const error = payload.error;
  if (!isRecord(error)) return null;

  const code = typeof error.code === "string" ? error.code : null;
  const message = typeof error.message === "string" ? error.message : null;
  const requestId = typeof error.request_id === "string" ? error.request_id : null;

  if (code === null && message === null) return null;
  return { code, message, requestId };
}

/**
 * Everything that failed before the first byte of the stream: 401/404/422/503
 * arrive here as the normal JSON envelope, with `X-Request-ID` on the response.
 */
async function toHttpError(response: Response): Promise<ApiError> {
  let payload: unknown = null;
  try {
    const text = await response.text();
    if (text.length > 0) {
      try {
        payload = JSON.parse(text) as unknown;
      } catch {
        // An HTML error page from a proxy, not our API. The status still tells
        // the story, so the raw text is not carried any further.
        payload = text;
      }
    }
  } catch {
    payload = null;
  }

  const envelope = readEnvelope(payload);
  const requestId = envelope?.requestId ?? response.headers.get("X-Request-ID") ?? undefined;
  const code = envelope?.code ?? `http_${response.status}`;
  const message =
    envelope?.message ??
    HTTP_FALLBACK[response.status] ??
    `خطای سرور (کد ${response.status}). لطفاً دوباره تلاش کنید.`;

  return new ApiError(message, response.status, code, requestId);
}

function readToolUsage(value: unknown): ToolUsage | null {
  if (!isRecord(value)) return null;
  const tool = typeof value.tool === "string" ? value.tool : null;
  if (tool === null) return null;
  return {
    tool,
    status: typeof value.status === "string" ? value.status : "ok",
    latency_ms: typeof value.latency_ms === "number" ? value.latency_ms : 0,
  };
}

interface SseFrame {
  /** Value of the `event:` line, when the server sent one. */
  event: string | null;
  /** All `data:` lines of the frame, joined with newlines per the SSE spec. */
  data: string;
}

/**
 * One frame of the stream.
 *
 * Field parsing follows the spec loosely on purpose: unknown fields (`id`,
 * `retry`) are ignored, comment lines (`:keepalive`) are dropped, and a single
 * leading space after the colon is stripped. Being strict here would break on a
 * proxy that rewrites whitespace, for no gain — the payload is JSON.
 */
function parseFrame(raw: string): SseFrame | null {
  const lines = raw.split("\n");
  let event: string | null = null;
  const data: string[] = [];

  for (const line of lines) {
    if (line.length === 0 || line.startsWith(":")) continue;

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  if (event === null && data.length === 0) return null;
  return { event, data: data.join("\n") };
}

interface FrameState {
  /** True once a `done` event was handled: later frames are not expected. */
  done: boolean;
}

/**
 * Dispatch one event.
 *
 * A malformed payload is skipped rather than thrown: one corrupt frame must not
 * destroy an answer that is otherwise arriving fine. An `error` event is the
 * exception — the server said the turn failed, so the caller has to know.
 */
function handleFrame(frame: SseFrame, handlers: StreamHandlers, state: FrameState): void {
  if (frame.data.length === 0) return;

  let payload: unknown;
  try {
    payload = JSON.parse(frame.data) as unknown;
  } catch {
    return;
  }
  if (!isRecord(payload)) return;

  // `event:` and the payload's own `kind` say the same thing; trust whichever
  // is present so a proxy that drops the event name still works.
  const kind = typeof payload.kind === "string" ? payload.kind : frame.event;

  switch (kind) {
    case "token": {
      // Tokens after `done` belong to nothing the user is reading: the answer is settled.
      if (state.done) return;
      const text = typeof payload.text === "string" ? payload.text : "";
      if (text.length > 0) handlers.onToken(text);
      return;
    }
    case "tool": {
      if (state.done) return;
      const tool = readToolUsage(payload.tool);
      if (tool !== null) handlers.onTool(tool);
      return;
    }
    case "title": {
      const title = typeof payload.text === "string" ? payload.text.trim() : "";
      if (title.length > 0) handlers.onTitle?.(title);
      return;
    }
    case "done": {
      if (state.done) return;
      state.done = true;
      const answer = typeof payload.answer === "string" ? payload.answer : "";
      const conversationId =
        typeof payload.conversation_id === "string" ? payload.conversation_id : "";
      // The title the turn gave the thread, when it named one. Applying it locally is the
      // only race-free way to update the sidebar: a refetch issued here can still read the
      // row as it was before the write landed.
      const title = typeof payload.title === "string" ? payload.title : null;
      handlers.onDone(answer, conversationId, title);
      return;
    }
    case "error": {
      const code = typeof payload.code === "string" ? payload.code : "stream_error";
      const message =
        typeof payload.message === "string" ? payload.message : "خطا در دریافت پاسخ رخ داد.";
      // Status 200: the HTTP response itself succeeded, the failure arrived as
      // data. Only the code and the server's own sentence matter to the caller.
      throw new ApiError(message, 200, code);
    }
    default:
      return;
  }
}

/**
 * Consume every complete frame in `buffer` and return what is left.
 *
 * CRLF is normalised first: a server or proxy may terminate lines either way,
 * and `\r\n\r\n` must count as one frame boundary. The remainder always ends
 * mid-frame, so it goes back to the caller to be completed by the next chunk.
 */
function drainFrames(buffer: string, handlers: StreamHandlers, state: FrameState): string {
  let rest = buffer.replace(/\r\n/g, "\n");
  let boundary = rest.indexOf("\n\n");

  while (boundary !== -1) {
    const raw = rest.slice(0, boundary);
    rest = rest.slice(boundary + 2);

    const frame = parseFrame(raw);
    if (frame !== null) handleFrame(frame, handlers, state);
    if (state.done) return rest;

    boundary = rest.indexOf("\n\n");
  }

  return rest;
}

/**
 * Send one message and stream the answer back.
 *
 * Resolves once the `done` event has been delivered. Rejects with `ApiError` on
 * a pre-stream HTTP failure, on an `error` event, or when the connection ends
 * without a `done` — in which case whatever tokens arrived have already been
 * handed to `onToken`, so the caller can keep them.
 *
 * Aborting resolves quietly and never throws: a stop is a decision the user
 * made, not a failure to report, and the partial answer is theirs to keep.
 */
export async function streamMessage(
  token: string,
  conversationId: string,
  content: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  if (signal?.aborted) return;

  const url = `${API_BASE_URL}/conversations/${encodeURIComponent(conversationId)}/messages/stream`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ content }),
      cache: "no-store",
      // `?? null` rather than `undefined`: RequestInit.signal is
      // `AbortSignal | null`, and exactOptionalPropertyTypes rejects undefined.
      signal: signal ?? null,
    });
  } catch (error) {
    if (isAbortError(error) || signal?.aborted) return;
    throw new ApiError(
      "ارتباط با سرور برقرار نشد. اتصال شبکه یا داخلی خود را بررسی کنید و مطمئن شوید سرویس در حال اجراست.",
      0,
      "network",
    );
  }

  if (!response.ok) {
    throw await toHttpError(response);
  }

  const contentType = response.headers.get("Content-Type") ?? "";
  if (contentType.includes("application/json")) {
    // A 200 that is not a stream means something in front of the API answered
    // with an error document. Its envelope is still worth reading.
    throw await toHttpError(response);
  }

  const body = response.body;
  if (body === null) {
    throw new ApiError(
      "مرورگر شما از پاسخ جریانی پشتیبانی نمی‌کند. مرورگر را به‌روز کنید یا از نسخهٔ دیگری استفاده کنید.",
      response.status,
      "stream_unsupported",
    );
  }

  const reader = body.getReader();
  const decoder = new TextDecoder();
  const state: FrameState = { done: false };
  let buffer = "";

  try {
    // Read until the server closes the stream rather than stopping at `done`: a thread's
    // title arrives as its own event just after the answer, and stopping early would drop
    // it. Late tokens are ignored by `handleFrame`, so this cannot corrupt the answer.
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = drainFrames(buffer, handlers, state);
    }

    // Flush the decoder's tail, then treat a leftover frame with no trailing blank line as
    // a real event: the socket can close immediately after the last byte, and dropping that
    // frame would lose the answer itself.
    buffer += decoder.decode();
    buffer = drainFrames(buffer, handlers, state);

    const tail = buffer.trim();
    if (tail.length > 0) {
      const frame = parseFrame(tail);
      if (frame !== null) handleFrame(frame, handlers, state);
    }
  } catch (error) {
    if (isAbortError(error) || signal?.aborted) return;
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      "دریافت پاسخ قطع شد. اتصال شبکه را بررسی کنید و دوباره تلاش کنید.",
      0,
      "stream_interrupted",
    );
  } finally {
    // Release the socket on every exit path, abort included; an already-closed
    // stream makes `cancel` throw in some browsers, hence the swallow.
    void reader.cancel().catch(() => undefined);
  }

  if (!state.done) {
    throw new ApiError(
      "پاسخ کامل دریافت نشد؛ ادامهٔ پاسخ نرسید. دوباره تلاش کنید.",
      0,
      "stream_incomplete",
    );
  }
}
