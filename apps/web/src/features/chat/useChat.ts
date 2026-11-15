import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  createConversation,
  listConversations,
  listMessages,
  sendMessage,
  type ChatMessage,
  type Conversation,
  type ToolUsage,
} from "../../services/api";

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

export interface UseChatResult {
  conversations: Conversation[];
  activeConversationId: string | null;
  messages: ChatMessage[];
  /** Id of the optimistic reply bubble currently waiting on the backend. */
  pendingReplyId: string | null;
  /** `tools_used` from the most recent answer. Never merged across answers. */
  toolsUsed: ToolUsage[];
  isSending: boolean;
  isCreatingConversation: boolean;
  isLoadingConversations: boolean;
  isLoadingMessages: boolean;
  error: ChatError | null;
  selectConversation: (conversationId: string) => void;
  startConversation: () => Promise<Conversation | null>;
  send: (content: string) => Promise<void>;
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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pendingReplyId, setPendingReplyId] = useState<string | null>(null);
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
  const localSeq = useRef(0);

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

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
      activeIdRef.current = conversationId;
      setActiveConversationId(conversationId);
      setMessages([]);
      setPendingReplyId(null);
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
      setConversations((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
      activeIdRef.current = created.id;
      setActiveConversationId(created.id);
      setMessages([]);
      setPendingReplyId(null);
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

  const send = useCallback(
    async (raw: string): Promise<void> => {
      const content = raw.trim();
      if (!content || sendingRef.current) return;

      sendingRef.current = true;
      setIsSending(true);
      setError(null);

      let targetId: string | null = null;
      let placeholderId: string | null = null;

      try {
        targetId = activeIdRef.current ?? null;
        if (!targetId) {
          // First message in a fresh session: the backend owns conversation
          // ids, so one is created lazily rather than faked on the client.
          const created = await createConversation(token);
          targetId = created.id;
          activeIdRef.current = created.id;
          setActiveConversationId(created.id);
          setConversations((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
          setMessages([]);
        }

        const conversationId = targetId;
        // Allocate the id before the request settles: a failed send keeps its
        // user message on screen, so reusing this number would mint duplicate
        // React keys on the retry.
        const seq = localSeq.current++;

        const userMessage: ChatMessage = {
          id: `local-user-${seq}`,
          role: "user",
          content,
          created_at: new Date().toISOString(),
        };
        const pendingId = `local-pending-${seq}`;
        placeholderId = pendingId;
        const placeholder: ChatMessage = {
          id: pendingId,
          role: "assistant",
          content: "",
          created_at: new Date().toISOString(),
        };

        // Optimistic: the question appears before the round-trip, and the empty
        // placeholder reserves the answer's slot in the thread. When the answer
        // arrives it is swapped into that slot instead of appended — otherwise a
        // reply that took ten seconds would land below messages sent after it.
        setMessages((prev) => [...prev, userMessage, placeholder]);
        setPendingReplyId(pendingId);
        setToolsUsed([]);

        const result = await sendMessage(token, conversationId, content);

        // A reply for a thread the user has since left must not be pasted into
        // the visible one; they will get it from the server when they go back.
        if (activeIdRef.current === conversationId) {
          const answer: ChatMessage = {
            id: `local-assistant-${seq}`,
            role: "assistant",
            content: result.answer,
            created_at: new Date().toISOString(),
          };
          setMessages((prev) => {
            // Normally the placeholder is still there and gets swapped. If the
            // thread was reloaded mid-flight (user switched away and back) the
            // answer has nowhere to land: show it rather than swallow a reply the
            // user waited for — unless the reloaded history already has it.
            if (prev.some((item) => item.id === pendingId)) {
              return prev.map((item) => (item.id === pendingId ? answer : item));
            }
            if (prev.some((item) => item.role === "assistant" && item.content === result.answer)) {
              return prev;
            }
            return [...prev, answer];
          });
          setToolsUsed(result.tools_used);
        }

        // The backend derives a conversation title from its first exchange, so a
        // still-unnamed conversation is re-read once. A stale title is cosmetic:
        // it must never surface as an error to the user.
        const knownTitle = conversationsRef.current.find((item) => item.id === conversationId)?.title;
        if (!knownTitle) {
          void refreshConversations().catch(() => undefined);
        }
      } catch (caught) {
        // Keep the user's own message on screen: it was accepted by the UI, and
        // removing it would make the failure look like a typo. Only the
        // unfulfilled reply is withdrawn.
        if (targetId !== null && activeIdRef.current === targetId && placeholderId !== null) {
          const doomedId = placeholderId;
          setMessages((prev) => prev.filter((item) => item.id !== doomedId));
        }
        setError(describeError(caught, "پاسخ به سؤال شما دریافت نشد."));
      } finally {
        setPendingReplyId(null);
        sendingRef.current = false;
        setIsSending(false);
      }
    },
    [token, refreshConversations],
  );

  return {
    conversations,
    activeConversationId,
    messages,
    pendingReplyId,
    toolsUsed,
    isSending,
    isCreatingConversation,
    isLoadingConversations,
    isLoadingMessages,
    error,
    selectConversation,
    startConversation,
    send,
    dismissError,
  };
}
