/**
 * Sending behaviour, which is where a chat UI is judged.
 *
 * Two claims are tested: the user's question appears before the backend answers, and a
 * failure never eats what they typed. Both are easy to break with a tidy refactor, and
 * both are invisible in a build that only typechecks.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
    listConversations: vi.fn(),
    listMessages: vi.fn(),
    createConversation: vi.fn(),
    sendMessage: vi.fn(),
  };
});

import {
  ApiError,
  createConversation,
  listConversations,
  listMessages,
  sendMessage,
} from "../../services/api";
import { useChat } from "./useChat";

const CONVERSATION = {
  id: "conv-1",
  title: null,
  created_at: "2026-11-15T10:00:00Z",
  updated_at: "2026-11-15T10:00:00Z",
};

beforeEach(() => {
  (listConversations as Mock).mockResolvedValue([]);
  (listMessages as Mock).mockResolvedValue([]);
  (createConversation as Mock).mockResolvedValue(CONVERSATION);
});

describe("optimistic send", () => {
  it("shows the question immediately and swaps the answer into its slot", async () => {
    let resolveAnswer: (value: unknown) => void = () => undefined;
    const pending = new Promise((resolve) => {
      resolveAnswer = resolve;
    });
    (sendMessage as Mock).mockReturnValue(pending);

    const { result } = renderHook(() => useChat("token"));
    await waitFor(() => expect(result.current.isLoadingConversations).toBe(false));

    act(() => {
      void result.current.send("چند بیمه‌نامه دارم؟");
    });

    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(result.current.messages[0]).toMatchObject({
      role: "user",
      content: "چند بیمه‌نامه دارم؟",
    });
    // The reply slot is reserved, not yet filled.
    const reserved = result.current.messages[1];
    expect(reserved?.content).toBe("");
    expect(result.current.pendingReplyId).toBe(reserved?.id);

    await act(async () => {
      resolveAnswer({
        conversation_id: CONVERSATION.id,
        answer: "شما سه بیمه‌نامه دارید.",
        model: "openrouter/x",
        usage: null,
        tools_used: [{ tool: "get_my_policy_count", status: "ok", latency_ms: 12 }],
      });
      await pending;
    });

    await waitFor(() => expect(result.current.isSending).toBe(false));
    expect(result.current.messages).toHaveLength(2);
    const answered = result.current.messages[1];
    expect(answered?.role).toBe("assistant");
    expect(answered?.content).toBe("شما سه بیمه‌نامه دارید.");
    expect(result.current.toolsUsed).toHaveLength(1);
    expect(result.current.pendingReplyId).toBeNull();
  });

  it("keeps the user's message when the turn fails", async () => {
    (sendMessage as Mock).mockRejectedValue(
      new ApiError("Authentication failed", 401, "http_401", "req-1"),
    );

    const { result } = renderHook(() => useChat("token"));
    await waitFor(() => expect(result.current.isLoadingConversations).toBe(false));

    await act(async () => {
      await result.current.send("چند بیمه‌نامه دارم؟");
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.role).toBe("user");
    expect(result.current.isSending).toBe(false);
  });

  it("treats 401 as an expired session, not a failed message", async () => {
    (sendMessage as Mock).mockRejectedValue(
      new ApiError("Authentication failed", 401, "http_401", "req-1"),
    );

    const { result } = renderHook(() => useChat("token"));
    await waitFor(() => expect(result.current.isLoadingConversations).toBe(false));

    await act(async () => {
      await result.current.send("سلام");
    });

    await waitFor(() => expect(result.current.error?.sessionExpired).toBe(true));
    expect(result.current.error?.requestId).toBe("req-1");
  });

  it("ignores a second send while one is in flight", async () => {
    (sendMessage as Mock).mockReturnValue(new Promise(() => undefined));

    const { result } = renderHook(() => useChat("token"));
    await waitFor(() => expect(result.current.isLoadingConversations).toBe(false));

    act(() => {
      void result.current.send("اول");
      void result.current.send("دوم");
    });

    await waitFor(() => expect(result.current.isSending).toBe(true));
    // The state update lands a microtask after the guard trips, so wait for it rather
    // than reading messages in the same tick (this raced on slower CI runners).
    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect((sendMessage as Mock).mock.calls).toHaveLength(1);
  });
});
