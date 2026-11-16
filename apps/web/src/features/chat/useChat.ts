import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  createConversation,
  deleteConversation as deleteConversationRequest,
  listConversations,
  listMessages,
  renameConversation as renameConversationRequest,
  type ChatMessage,
  type Conversation,
  type ToolUsage,
} from "../../services/api";
import { streamMessage } from "../../services/stream";

/**
 * A failure the user must be able to act on.
 *
 * `detail` keeps the server's own sentence verbatim (it is usually English) so a
 * reviewer never has to guess what the Persian headline replaced. `requestId` is
 * the only handle that ties a user's screenshot to a backend log line, so it is
 * carried up to the UI instead of being swallowed here.
 */
export interface ChatError {
  message: string;
  detail: string | null;
  requestId: string | null;
  sessionExpired: boolean;
}

/**
 * A message plus the one piece of state the backend history does not carry.
 *
 * The server stores a complete answer or nothing; it has no notion of "the user
 * stopped reading". `interrupted` is therefore local-only and describes how the
 * text got here: `true` means the bubble holds whatever had arrived when the
 * stream stopped or broke, not a finished answer. It lives on the message rather
 * than in a side map so it survives re-renders and reordering and disappears
 * with the bubble.
 *
 * The field is optional, so `LocalMessage[]` still satisfies every consumer that
 * only knows about `ChatMessage` — including the components that render it.
 */
export interface LocalMessage extends ChatMessage {
  interrupted?: boolean;
}

export interface UseChatResult {
  conversations: Conversation[];
  activeConversationId: string | null;
  messages: LocalMessage[];
  /** Id of the optimistic reply bubble currently waiting on the backend. */
  pendingReplyId: string | null;
  /**
   * Text received for the in-flight answer, or `null` when nothing is in flight.
   *
   * This is the live buffer, and the *only* place in-flight text lives: the
   * reserved bubble in `messages` stays empty until the turn settles, so a
   * renderer must never concatenate the two. `messages` holds settled content
   * only — the server's answer on success, the partial text on stop or failure.
   */
  streamingText: string | null;
  /** Tools used by the most recent answer. Never merged across answers. */
  toolsUsed: ToolUsage[];
  isSending: boolean;
  isCreatingConversation: boolean;
  isLoadingConversations: boolean;
  isLoadingMessages: boolean;
  error: ChatError | null;
  selectConversation: (conversationId: string) => void;
  startConversation: () => Promise<Conversation | null>;
  send: (content: string) => Promise<void>;
  /** Abort the in-flight answer, keeping the text that already arrived. */
  stop: () => void;
  /** Ask the same question again, replacing the last answer instead of repeating it. */
  retry: () => Promise<void>;
  /** Rename a thread. Resolves when the server has stored it. */
  renameConversation: (conversationId: string, title: string) => Promise<void>;
  /** Delete a thread and, if it was open, leave the empty state behind. */
  deleteConversation: (conversationId: string) => Promise<void>;
  dismissError: () => void;
}

function describeError(error: unknown, headline: string): ChatError {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      // An expired token is not a failed message; the only useful action is to
      // sign in again, so it gets its own sentence rather than the generic one.
      return {
        message: "نشست شما منقضی شده است.",
        detail: error.message,
        requestId: error.requestId ?? null,
        sessionExpired: true,
      };
    }
    return {
      message: headline,
      detail: error.message,
      requestId: error.requestId ?? null,
      sessionExpired: false,
    };
  }

  return { message: headline, detail: null, requestId: null, sessionExpired: false };
}

function timestamp(value: string): number {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

/** Newest by `updated_at`, independent of the order the API happens to return. */
function pickMostRecent(conversations: Conversation[]): Conversation | null {
  let best: Conversation | null = null;
  for (const conversation of conversations) {
    if (!best || timestamp(conversation.updated_at) > timestamp(best.updated_at)) {
      best = conversation;
    }
  }
  return best;
}

export function useChat(token: string): UseChatResult {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [pendingReplyId, setPendingReplyId] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [toolsUsed, setToolsUsed] = useState<ToolUsage[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [isCreatingConversation, setIsCreatingConversation] = useState(false);
  const [isLoadingConversations, setIsLoadingConversations] = useState(true);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [error, setError] = useState<ChatError | null>(null);

  // React state updates are async, so a guard that lives only in state can let a
  // second Enter slip through before the first render lands. These refs are the
  // authoritative in-flight flags; the state copies are for rendering.
  const sendingRef = useRef(false);
  const creatingRef = useRef(false);
  const activeIdRef = useRef<string | null>(null);
  const conversationsRef = useRef<Conversation[]>([]);
  const messagesRef = useRef<LocalMessage[]>([]);
  const localSeq = useRef(0);

  // The in-flight request, so `stop()` and a conversation switch can abort it.
  const runRef = useRef<AbortController | null>(null);
  // Whether the current turn ended because the user pressed stop. A ref because
  // the abort resolves the awaited call in another microtask, long after the
  // click handler returned.
  const stoppedRef = useRef(false);
  // Token text and tools accumulate in refs as well as state: events fire many
  // times per frame, and the settle path needs the final value synchronously.
  const streamTextRef = useRef("");
  const toolBufferRef = useRef<ToolUsage[]>([]);

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // A stream that outlives the component would keep writing into dropped state
  // and hold the socket open; abort it on unmount.
  useEffect(
    () => () => {
      runRef.current?.abort();
    },
    [],
  );

  const dismissError = useCallback(() => {
    setError(null);
  }, []);

  const refreshConversations = useCallback(async (): Promise<Conversation[]> => {
    const list = await listConversations(token);
    setConversations(list);
    return list;
  }, [token]);

  const loadMessages = useCallback(
    async (conversationId: string): Promise<void> => {
      setIsLoadingMessages(true);
      try {
        const history = await listMessages(token, conversationId);
        // The user may have opened another thread while this was in flight.
        // Writing the result anyway would show one conversation's history under
        // another conversation's title.
        if (activeIdRef.current !== conversationId) return;
        setMessages(history);
      } catch (caught) {
        if (activeIdRef.current !== conversationId) return;
        setError(describeError(caught, "بارگذاری این گفتگو انجام نشد."));
      } finally {
        if (activeIdRef.current === conversationId) setIsLoadingMessages(false);
      }
    },
    [token],
  );

  const selectConversation = useCallback(
    (conversationId: string): void => {
      if (activeIdRef.current === conversationId) return;
      // Leaving a thread mid-answer: drop the request rather than let it keep the
      // composer locked, and do not paste its answer into the thread now shown.
      if (runRef.current !== null) {
        stoppedRef.current = true;
        runRef.current.abort();
      }
      activeIdRef.current = conversationId;
      setActiveConversationId(conversationId);
      setMessages([]);
      setPendingReplyId(null);
      setStreamingText(null);
      setToolsUsed([]);
      setError(null);
      void loadMessages(conversationId);
    },
    [loadMessages],
  );

  useEffect(() => {
    let cancelled = false;
    setIsLoadingConversations(true);

    refreshConversations()
      .then((list) => {
        if (cancelled) return;
        const mostRecent = pickMostRecent(list);
        if (mostRecent) {
          selectConversation(mostRecent.id);
        }
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setError(describeError(caught, "فهرست گفتگوها بارگذاری نشد."));
      })
      .finally(() => {
        if (cancelled) return;
        setIsLoadingConversations(false);
      });

    return () => {
      cancelled = true;
    };
  }, [refreshConversations, selectConversation]);

  const startConversation = useCallback(async (): Promise<Conversation | null> => {
    if (creatingRef.current) return null;
    creatingRef.current = true;
    setIsCreatingConversation(true);
    setError(null);

    try {
      const created = await createConversation(token);
      if (runRef.current !== null) {
        stoppedRef.current = true;
        runRef.current.abort();
      }
      setConversations((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
      activeIdRef.current = created.id;
      setActiveConversationId(created.id);
      setMessages([]);
      setPendingReplyId(null);
      setStreamingText(null);
      setToolsUsed([]);
      return created;
    } catch (caught) {
      setError(describeError(caught, "ساخت گفتگوی جدید انجام نشد."));
      return null;
    } finally {
      creatingRef.current = false;
      setIsCreatingConversation(false);
    }
  }, [token]);

  /**
   * One turn: reserve a slot, stream into it, settle it.
   *
   * `mode: "retry"` re-asks the question already on screen, so the user bubble is
   * not repeated and the previous answer is replaced rather than appended —
   * otherwise a retry would read as two questions in a row.
   */
  const runTurn = useCallback(
    async (raw: string, mode: "send" | "retry"): Promise<void> => {
      const content = raw.trim();
      if (content.length === 0 || sendingRef.current) return;

      sendingRef.current = true;
      stoppedRef.current = false;
      streamTextRef.current = "";
      toolBufferRef.current = [];
      setIsSending(true);
      setError(null);
      setStreamingText(null);
      setToolsUsed([]);

      let targetId: string | null = null;
      let placeholderId: string | null = null;
      let assistantId: string | null = null;

      /**
       * Move the reserved slot to a settled message.
       *
       * Text reaches `messages` only here, never from the token handler: tokens
       * feed `streamingText` alone. One source of truth per phase, and the
       * "server answer wins" rule becomes the single branch below.
       */
      const settle = (text: string, interrupted: boolean): void => {
        if (targetId === null || activeIdRef.current !== targetId) return;

        if (text.length === 0) {
          // Nothing arrived and nothing is owed: withdraw the empty slot but
          // leave the question exactly where it is.
          if (placeholderId !== null) {
            const doomed = placeholderId;
            setMessages((prev) => prev.filter((item) => item.id !== doomed));
          }
          return;
        }

        const settled: LocalMessage = interrupted
          ? {
              id: assistantId ?? `local-assistant-${localSeq.current}`,
              role: "assistant",
              content: text,
              created_at: new Date().toISOString(),
              interrupted: true,
            }
          : {
              id: assistantId ?? `local-assistant-${localSeq.current}`,
              role: "assistant",
              content: text,
              created_at: new Date().toISOString(),
            };

        const pending = placeholderId;
        setMessages((prev) => {
          if (pending !== null && prev.some((item) => item.id === pending)) {
            return prev.map((item) => (item.id === pending ? settled : item));
          }
          // The thread was reloaded mid-flight (switched away and back): show the
          // answer rather than swallow a reply the user waited for — unless the
          // reloaded history already contains it.
          if (prev.some((item) => item.role === "assistant" && item.content === text)) {
            return prev;
          }
          return [...prev, settled];
        });
      };

      try {
        targetId = activeIdRef.current;
        if (targetId === null) {
          // First message in a fresh session: the backend owns conversation ids,
          // so one is created lazily rather than faked on the client.
          const created = await createConversation(token);
          targetId = created.id;
          activeIdRef.current = created.id;
          setActiveConversationId(created.id);
          setConversations((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
          setMessages([]);
        }

        const conversationId: string = targetId;
        // Allocate the id before the request settles: a failed send keeps its
        // user message on screen, so reusing this number would mint duplicate
        // React keys on the retry.
        const seq = localSeq.current++;
        const pendingId = `local-pending-${seq}`;
        placeholderId = pendingId;
        assistantId = `local-assistant-${seq}`;

        const userMessage: LocalMessage = {
          id: `local-user-${seq}`,
          role: "user",
          content,
          created_at: new Date().toISOString(),
        };
        const placeholder: LocalMessage = {
          id: pendingId,
          role: "assistant",
          content: "",
          created_at: new Date().toISOString(),
        };

        // Optimistic: the question appears before the round-trip, and the empty
        // placeholder reserves the answer's slot in the thread. When the answer
        // settles it is swapped into that slot instead of appended — otherwise a
        // reply that took ten seconds would land below messages sent after it.
        setMessages((prev) => {
          let base: LocalMessage[] = [...prev];
          if (mode === "retry") {
            while (base.length > 0 && base[base.length - 1]?.role === "assistant") {
              base = base.slice(0, -1);
            }
          } else {
            base = [...base, userMessage];
          }
          return [...base, placeholder];
        });
        setPendingReplyId(pendingId);

        const controller = new AbortController();
        runRef.current = controller;

        // `answer` is written by the stream callback, which TypeScript cannot
        // track; the explicit annotation keeps the null check meaningful.
        const outcome: { answer: string | null; title: string | null } = {
          answer: null,
          title: null,
        };

        await streamMessage(
          token,
          conversationId,
          content,
          {
            onToken: (text) => {
              if (text.length === 0) return;
              streamTextRef.current += text;
              setStreamingText(streamTextRef.current);
            },
            onTool: (tool) => {
              toolBufferRef.current = [...toolBufferRef.current, tool];
              setToolsUsed(toolBufferRef.current);
            },
            onDone: (finalAnswer, _conversationId, title) => {
              outcome.answer = finalAnswer;
              outcome.title = title;
            },
            onTitle: (title) => {
              // Arrives after the answer; the sidebar picks it up without a refetch.
              if (targetId !== null) {
                setConversations((prev) =>
                  prev.map((item) => (item.id === targetId ? { ...item, title } : item)),
                );
              }
            },
          },
          controller.signal,
        );

        const settledAnswer: string | null = outcome.answer;
        const partial = streamTextRef.current;

        if (stoppedRef.current) {
          // A stop is not a failure: keep exactly what arrived and label it.
          settle(partial, true);
        } else if (settledAnswer !== null) {
          // The server is the record. Its final answer replaces the concatenated
          // tokens, which can legitimately differ (tool-round text, trimming).
          settle(settledAnswer, false);
        } else {
          settle(partial, true);
        }

        // The turn may have named the thread. Applied locally and immediately: the server
        // sends it on `done`, so no refetch is needed and none can race the write.
        const titledByServer: string | null = outcome.title;
        if (titledByServer !== null && targetId !== null) {
          setConversations((prev) =>
            prev.map((item) =>
              item.id === targetId ? { ...item, title: titledByServer } : item,
            ),
          );
        }

        // The backend derives a conversation title from its first exchange, so a
        // still-unnamed conversation is re-read once. A stale title is cosmetic:
        // it must never surface as an error to the user.
        const knownTitle = conversationsRef.current.find((item) => item.id === conversationId)?.title;
        if (titledByServer === null && !knownTitle) {
          void refreshConversations().catch(() => undefined);
        }
      } catch (caught) {
        // Keep what arrived: a broken stream is not a reason to throw away an
        // answer the user has already started reading.
        const partial = streamTextRef.current;
        if (partial.length > 0) {
          settle(partial, true);
        } else if (
          targetId !== null &&
          activeIdRef.current === targetId &&
          placeholderId !== null
        ) {
          // Keep the user's own message on screen: it was accepted by the UI, and
          // removing it would make the failure look like a typo. Only the
          // unfulfilled reply is withdrawn.
          const doomed = placeholderId;
          setMessages((prev) => prev.filter((item) => item.id !== doomed));
        }
        setError(describeError(caught, "پاسخ به سؤال شما دریافت نشد."));
      } finally {
        runRef.current = null;
        stoppedRef.current = false;
        streamTextRef.current = "";
        toolBufferRef.current = [];
        setStreamingText(null);
        setPendingReplyId(null);
        sendingRef.current = false;
        setIsSending(false);
      }
    },
    [token, refreshConversations],
  );

  const send = useCallback(
    async (raw: string): Promise<void> => {
      await runTurn(raw, "send");
    },
    [runTurn],
  );

  const stop = useCallback((): void => {
    const controller = runRef.current;
    if (controller === null) return;
    // Flag first: the abort resolves `await streamMessage` in a later microtask,
    // and that is where the partial text is committed as an interrupted answer.
    stoppedRef.current = true;
    controller.abort();
  }, []);

  const retry = useCallback(async (): Promise<void> => {
    if (sendingRef.current) return;
    // Reverse scan rather than `findLast`: the lib target here predates it.
    let lastUser: LocalMessage | undefined;
    for (let index = messagesRef.current.length - 1; index >= 0; index -= 1) {
      const candidate = messagesRef.current[index];
      if (candidate?.role === "user") {
        lastUser = candidate;
        break;
      }
    }
    if (lastUser === undefined) return;
    await runTurn(lastUser.content, "retry");
  }, [runTurn]);

  // Renaming is optimistic with a per-row rollback: the sidebar must not jump, and a
  // failed rename must not leave a title the server does not have. Reverting only the
  // edited row matters — restoring a snapshot of the whole list can drop a thread that
  // appeared since it was taken.
  const renameConversation = useCallback(
    async (conversationId: string, title: string): Promise<void> => {
      const trimmed = title.trim();
      if (trimmed.length === 0) return;

      const previousTitle =
        conversationsRef.current.find((item) => item.id === conversationId)?.title ?? null;

      setConversations((prev) =>
        prev.map((item) => (item.id === conversationId ? { ...item, title: trimmed } : item)),
      );
      try {
        const updated = await renameConversationRequest(token, conversationId, trimmed);
        setConversations((prev) =>
          prev.map((item) => (item.id === conversationId ? updated : item)),
        );
      } catch (caught) {
        setConversations((prev) =>
          prev.map((item) =>
            item.id === conversationId ? { ...item, title: previousTitle } : item,
          ),
        );
        setError(describeError(caught, "تغییر نام گفتگو انجام نشد."));
      }
    },
    [token],
  );

  const deleteConversation = useCallback(
    async (conversationId: string): Promise<void> => {
      const removedIndex = conversationsRef.current.findIndex(
        (item) => item.id === conversationId,
      );
      const removed =
        removedIndex >= 0 ? conversationsRef.current[removedIndex] : undefined;

      setConversations((prev) => prev.filter((item) => item.id !== conversationId));
      try {
        await deleteConversationRequest(token, conversationId);
        if (activeIdRef.current === conversationId) {
          // Nothing to select: the next message starts a fresh thread, which is what
          // the empty state already offers.
          activeIdRef.current = null;
          setActiveConversationId(null);
          setMessages([]);
          setToolsUsed([]);
        }
      } catch (caught) {
        // Put the row back where it was, not at the top: the order is the user's sense of
        // recency, and a failed delete should be invisible.
        if (removed !== undefined) {
          setConversations((prev) => {
            const next = [...prev];
            next.splice(Math.min(removedIndex, next.length), 0, removed);
            return next;
          });
        }
        setError(describeError(caught, "حذف گفتگو انجام نشد."));
      }
    },
    [token],
  );

  return {
    conversations,
    activeConversationId,
    messages,
    pendingReplyId,
    streamingText,
    toolsUsed,
    isSending,
    isCreatingConversation,
    isLoadingConversations,
    isLoadingMessages,
    error,
    selectConversation,
    startConversation,
    send,
    stop,
    retry,
    renameConversation,
    deleteConversation,
    dismissError,
  };
}
