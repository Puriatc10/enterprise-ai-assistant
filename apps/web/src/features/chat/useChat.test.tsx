/**
 * The behaviour a chat UI is judged on, now that answers arrive as a stream.
 *
 * Two claims matter most: the question appears before the backend answers, and a
 * failure — or a stop — never eats what the user typed or already read. Both are
 * easy to break with a tidy refactor and invisible in a build that only
 * typechecks. The parser is tested against real byte chunks for the same reason:
 * a frame split across two reads is the normal case, not an edge case.
 */

import { act, cleanup, render, renderHook, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../services/api", () => {
  class ApiError extends Error {
    readonly status: number;
    readonly code: string;
    readonly requestId: string | undefined;

    constructor(message: string, status: number, code: string, requestId?: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.code = code;
      this.requestId = requestId;
    }
  }

  return {
    ApiError,
    API_BASE_URL: "http://api.test",
    listConversations: vi.fn(),
    listMessages: vi.fn(),
    createConversation: vi.fn(),
    sendMessage: vi.fn(),
  };
});

vi.mock("../../services/stream", () => ({
  streamMessage: vi.fn(),
}));

import { ApiError, createConversation, listConversations, listMessages } from "../../services/api";
import type { ToolUsage } from "../../services/api";
import { streamMessage } from "../../services/stream";
import { useChat } from "./useChat";

const CONVERSATION = {
  id: "conv-1",
  title: null,
  created_at: "2026-11-15T10:00:00Z",
  updated_at: "2026-11-15T10:00:00Z",
};

interface Handlers {
  onToken: (text: string) => void;
  onTool: (tool: ToolUsage) => void;
  onDone: (answer: string, conversationId: string) => void;
}

beforeEach(() => {
  (listConversations as Mock).mockResolvedValue([]);
  (listMessages as Mock).mockResolvedValue([]);
  (createConversation as Mock).mockResolvedValue(CONVERSATION);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function renderChat() {
  const rendered = renderHook(() => useChat("token"));
  await waitFor(() => expect(rendered.result.current.isLoadingConversations).toBe(false));
  return rendered;
}

describe("streaming send", () => {
  it("shows the question at once and streams tokens into the reserved slot", async () => {
    const captured: { handlers: Handlers | null } = { handlers: null };
    let release: () => void = () => undefined;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });

    (streamMessage as Mock).mockImplementation(
      (_token: string, _id: string, _content: string, handlers: Handlers) => {
        captured.handlers = handlers;
        return pending;
      },
    );

    const { result } = await renderChat();

    act(() => {
      void result.current.send("چند بیمه‌نامه دارم؟");
    });

    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(result.current.messages[0]).toMatchObject({
      role: "user",
      content: "چند بیمه‌نامه دارم؟",
    });

    // The reply slot is reserved and empty; the text arriving lives beside it,
    // in `streamingText`, so the two are never concatenated.
    const reserved = result.current.messages[1];
    expect(reserved?.content).toBe("");
    expect(result.current.pendingReplyId).toBe(reserved?.id);
    expect(result.current.streamingText).toBeNull();

    act(() => {
      captured.handlers?.onToken("شما ");
      captured.handlers?.onToken("سه بیمه‌نامه دارید.");
    });

    expect(result.current.streamingText).toBe("شما سه بیمه‌نامه دارید.");
    // Still empty: nothing is written into the transcript until the turn settles.
    expect(result.current.messages[1]?.content).toBe("");

    act(() => {
      captured.handlers?.onDone("شما سه بیمه‌نامه دارید.", CONVERSATION.id);
    });
    await act(async () => {
      release();
      await pending;
    });

    await waitFor(() => expect(result.current.isSending).toBe(false));
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1]).toMatchObject({
      role: "assistant",
      content: "شما سه بیمه‌نامه دارید.",
    });
    expect(result.current.messages[1]?.interrupted).toBeUndefined();
    expect(result.current.streamingText).toBeNull();
    expect(result.current.pendingReplyId).toBeNull();
  });

  it("replaces the slot with the server's answer, not with the tokens", async () => {
    (streamMessage as Mock).mockImplementation(
      async (_token: string, _id: string, _content: string, handlers: Handlers) => {
        handlers.onToken("متن موقت");
        // The server trims/regenerates: its `done` payload is the record.
        handlers.onDone("پاسخ نهایی سرور", CONVERSATION.id);
      },
    );

    const { result } = await renderChat();

    await act(async () => {
      await result.current.send("سلام");
    });

    await waitFor(() => expect(result.current.isSending).toBe(false));
    expect(result.current.messages[1]?.content).toBe("پاسخ نهایی سرور");
  });

  it("adds a tool event to toolsUsed", async () => {
    (streamMessage as Mock).mockImplementation(
      async (_token: string, _id: string, _content: string, handlers: Handlers) => {
        handlers.onTool({ tool: "support_count_my_tickets", status: "ok", latency_ms: 12 });
        handlers.onToken("یک تیکت باز دارید.");
        handlers.onDone("یک تیکت باز دارید.", CONVERSATION.id);
      },
    );

    const { result } = await renderChat();

    await act(async () => {
      await result.current.send("چند تیکت باز دارم؟");
    });

    expect(result.current.toolsUsed).toEqual([
      { tool: "support_count_my_tickets", status: "ok", latency_ms: 12 },
    ]);
  });

  it("ignores a second send while one is in flight", async () => {
    (streamMessage as Mock).mockImplementation(() => new Promise<void>(() => undefined));

    const { result } = await renderChat();

    act(() => {
      void result.current.send("اول");
      void result.current.send("دوم");
    });

    await waitFor(() => expect(result.current.isSending).toBe(true));
    // The state update lands a microtask after the guard trips, so wait for it
    // rather than reading messages in the same tick (this raced on slower CI).
    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect((streamMessage as Mock).mock.calls).toHaveLength(1);
  });
});

describe("stop", () => {
  it("keeps the partial text and marks the answer interrupted", async () => {
    (streamMessage as Mock).mockImplementation(
      (
        _token: string,
        _id: string,
        _content: string,
        handlers: Handlers,
        signal?: AbortSignal,
      ) =>
        new Promise<void>((resolve) => {
          handlers.onToken("نیمی از پاسخ");
          signal?.addEventListener("abort", () => {
            resolve();
          });
        }),
    );

    const { result } = await renderChat();

    act(() => {
      void result.current.send("سؤال طولانی");
    });

    await waitFor(() => expect(result.current.streamingText).toBe("نیمی از پاسخ"));

    act(() => {
      result.current.stop();
    });

    await waitFor(() => expect(result.current.isSending).toBe(false));

    const answer = result.current.messages[1];
    expect(answer?.role).toBe("assistant");
    expect(answer?.content).toBe("نیمی از پاسخ");
    expect(answer?.interrupted).toBe(true);
    expect(result.current.streamingText).toBeNull();
    // A stop is not a failure: no error banner, and the question is still there.
    expect(result.current.error).toBeNull();
    expect(result.current.messages[0]?.role).toBe("user");
  });
});

describe("failure", () => {
  it("keeps the user's message when the turn fails", async () => {
    (streamMessage as Mock).mockRejectedValue(
      new ApiError("Model unavailable", 503, "llm_not_configured", "req-2"),
    );

    const { result } = await renderChat();

    await act(async () => {
      await result.current.send("چند بیمه‌نامه دارم؟");
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    // The unfulfilled reply is withdrawn; the question is not.
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.role).toBe("user");
    expect(result.current.error?.requestId).toBe("req-2");
    expect(result.current.isSending).toBe(false);
  });

  it("treats 401 as an expired session, not a failed message", async () => {
    (streamMessage as Mock).mockRejectedValue(
      new ApiError("Authentication failed", 401, "http_401", "req-1"),
    );

    const { result } = await renderChat();

    await act(async () => {
      await result.current.send("سلام");
    });

    await waitFor(() => expect(result.current.error?.sessionExpired).toBe(true));
    expect(result.current.error?.requestId).toBe("req-1");
    expect(result.current.messages[0]?.role).toBe("user");
  });
});

describe("retry", () => {
  it("re-asks the last question without repeating the user's bubble", async () => {
    let turn = 0;
    (streamMessage as Mock).mockImplementation(
      async (_token: string, _id: string, _content: string, handlers: Handlers) => {
        const answer = turn === 0 ? "پاسخ اول" : "پاسخ دوم";
        turn += 1;
        handlers.onDone(answer, CONVERSATION.id);
      },
    );

    const { result } = await renderChat();

    await act(async () => {
      await result.current.send("سؤال من");
    });
    await waitFor(() => expect(result.current.messages[1]?.content).toBe("پاسخ اول"));

    await act(async () => {
      await result.current.retry();
    });
    await waitFor(() => expect(result.current.messages[1]?.content).toBe("پاسخ دوم"));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[0]?.content).toBe("سؤال من");
    expect((streamMessage as Mock).mock.calls).toHaveLength(2);
    // The retry re-sent the same text, not an empty string.
    expect((streamMessage as Mock).mock.calls[1]?.[2]).toBe("سؤال من");
  });
});

/** A Response whose body emits `chunks` one at a time, like a real socket. */
function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

async function realStreamMessage(): Promise<typeof import("../../services/stream").streamMessage> {
  const actual = await vi.importActual<typeof import("../../services/stream")>(
    "../../services/stream",
  );
  return actual.streamMessage;
}

describe("streamMessage parser", () => {
  it("handles CRLF, a frame split across chunks, multi-line data and a final frame with no blank line", async () => {
    const document = [
      'event: token\r\ndata: {"kind":"token","text":"یک "}\r\n\r\n',
      // `data:` split over two lines is legal SSE; JSON tolerates the newline.
      'event: token\r\ndata: {"kind":"token",\r\ndata: "text":"دو"}\r\n\r\n',
      'event: tool\r\ndata: {"kind":"tool","tool":{"tool":"support_count_my_tickets","status":"ok","latency_ms":12}}\r\n\r\n',
      // No trailing blank line: the socket closed right after the last byte.
      'event: done\r\ndata: {"kind":"done","answer":"یک دو","conversation_id":"conv-1"}',
    ].join("");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(sseResponse([document.slice(0, 9), document.slice(9, 40), document.slice(40)])),
    );

    const tokens: string[] = [];
    const tools: ToolUsage[] = [];
    const done: { answer: string; conversationId: string }[] = [];

    const stream = await realStreamMessage();
    await stream("token", "conv-1", "سؤال", {
      onToken: (text) => tokens.push(text),
      onTool: (tool) => tools.push(tool),
      onDone: (answer, conversationId) => done.push({ answer, conversationId }),
    });

    expect(tokens.join("")).toBe("یک دو");
    expect(tools).toEqual([{ tool: "support_count_my_tickets", status: "ok", latency_ms: 12 }]);
    expect(done).toEqual([{ answer: "یک دو", conversationId: "conv-1" }]);
  });

  it("throws the server's Persian ApiError when an error event arrives", async () => {
    const frame =
      'event: error\ndata: {"kind":"error","code":"llm_rate_limited","message":"مدل در حال حاضر پاسخ‌گو نیست."}\n\n';

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([frame])));

    const stream = await realStreamMessage();
    const failure = stream("token", "conv-1", "سؤال", {
      onToken: () => undefined,
      onTool: () => undefined,
      onDone: () => undefined,
    });

    await expect(failure).rejects.toMatchObject({
      code: "llm_rate_limited",
      message: "مدل در حال حاضر پاسخ‌گو نیست.",
    });
    await expect(failure).rejects.toBeInstanceOf(ApiError);
  });

  it("throws ApiError for a pre-stream HTTP envelope, keeping the request id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: { code: "unauthorized", message: "Authentication failed", request_id: "req-9" },
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const stream = await realStreamMessage();
    await expect(
      stream("token", "conv-1", "سؤال", {
        onToken: () => undefined,
        onTool: () => undefined,
        onDone: () => undefined,
      }),
    ).rejects.toMatchObject({ status: 401, code: "unauthorized", requestId: "req-9" });
  });

  it("returns quietly when the request is aborted", async () => {
    const aborted = new Error("aborted");
    aborted.name = "AbortError";
    const fetchMock = vi.fn().mockRejectedValue(aborted);
    vi.stubGlobal("fetch", fetchMock);

    const controller = new AbortController();
    controller.abort();

    const stream = await realStreamMessage();
    await expect(
      stream(
        "token",
        "conv-1",
        "سؤال",
        { onToken: () => undefined, onTool: () => undefined, onDone: () => undefined },
        controller.signal,
      ),
    ).resolves.toBeUndefined();
    // An already-aborted signal must not even reach the network.
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

/**
 * The answer is model output, so the renderer is part of the security surface —
 * `rehype-sanitize` with its default schema, and no `rehype-raw`.
 */
describe("Markdown", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders GFM, strips raw HTML and keeps code LTR", async () => {
    const { Markdown } = await import("./Markdown");
    const { container } = render(
      <Markdown>
        {"# عنوان\n\nمتن با `POL-۱۲۳` در میان فارسی.\n\n<script>alert(1)</script>\n\n```ts\nconst a = 1;\n```\n\n| a | b |\n| - | - |\n| 1 | 2 |"}
      </Markdown>,
    );

    expect(container.querySelector("h1")?.textContent).toBe("عنوان");
    // Raw HTML is dropped, not parsed: this is the claim the sanitiser exists for.
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("table")).not.toBeNull();

    const inline = container.querySelector("p code");
    expect(inline?.getAttribute("dir")).toBe("ltr");
    expect(inline?.textContent).toBe("POL-۱۲۳");

    // A fenced block is framed and offers its own copy button.
    expect(container.textContent).toContain("const a = 1;");
    expect(container.querySelector("button")?.textContent).toBe("کپی");
  });

  it("opens links in a new tab without handing over window.opener", async () => {
    const { Markdown } = await import("./Markdown");
    const { container } = render(<Markdown>{"[سند](https://example.com/doc)"}</Markdown>);
    const anchor = container.querySelector("a");
    expect(anchor?.getAttribute("target")).toBe("_blank");
    expect(anchor?.getAttribute("rel")).toBe("noopener noreferrer");
  });
});
