import { useState } from "react";
import type { CSSProperties, JSX } from "react";

import { AppShell } from "../../components/AppShell";
import { Sidebar } from "../../components/Sidebar";
import { Composer } from "./Composer";
import { Thread } from "./Thread";
import { useChat, type ChatError, type LocalMessage } from "./useChat";
import "./chat.css";

export interface ChatPageProps {
  /** Bearer token from the session hook; never rendered, never logged. */
  token: string;
  /** Signed-in address, shown in the sidebar footer and in the greeting. */
  email: string | null;
  onSignOut: () => Promise<void>;
}

/** Matches the backend's validated limit for one message. */
const MAX_LENGTH = 4000;

/**
 * Suggestion chips, one per domain the registry actually exposes
 * (`get_my_policy_count`, `get_my_policies`, `support_count_my_tickets`,
 * `list_my_data_sources`). A chip may not promise a capability the backend does
 * not have.
 */
const SUGGESTIONS: readonly string[] = [
  "چند بیمه‌نامه دارم؟",
  "بیمه‌نامه‌های من را فهرست کن.",
  "چند تیکت باز دارم؟",
  "چه منابع داده‌ای در دسترس است؟",
];

const HEADER_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "0.6rem",
  padding: "0.55rem 1rem",
  borderBottom: "1px solid var(--border, #e3e6eb)",
  background: "var(--surface, #fff)",
  minHeight: "3rem",
};

const HEADER_TITLE_STYLE: CSSProperties = {
  margin: 0,
  fontSize: "0.95rem",
  fontWeight: 600,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

const TOGGLE_STYLE: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: "2rem",
  height: "2rem",
  font: "inherit",
  fontSize: "1.05rem",
  lineHeight: 1,
  color: "var(--text, #14181f)",
  background: "transparent",
  border: "1px solid var(--border, #e3e6eb)",
  borderRadius: "8px",
  cursor: "pointer",
};

const ACTIONS_ROW_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "flex-end",
  gap: "0.35rem",
  maxWidth: "46rem",
  width: "100%",
  margin: "0 auto",
  padding: "0 1rem 0.4rem",
  flexWrap: "wrap",
};

const INTERRUPTED_STYLE: CSSProperties = {
  fontSize: "0.72rem",
  color: "var(--warn, #b23c17)",
  border: "1px dashed currentColor",
  borderRadius: "999px",
  padding: "1px 8px",
};

/** `puria.vaziri@example.com` → `puria.vaziri`; the greeting uses the name, not the address. */
function localPart(email: string | null): string | null {
  if (email === null) return null;
  const at = email.indexOf("@");
  const name = (at === -1 ? email : email.slice(0, at)).trim();
  return name.length > 0 ? name : null;
}

interface MessageActionsProps {
  message: LocalMessage;
  onRetry: () => void;
}

/**
 * Actions for the newest answer: copy, retry, and the interrupted label.
 *
 * WHY here and not inside `MessageBubble`'s children slot: `Thread` renders the
 * bubbles itself and exposes no per-message hook, and this page may not edit
 * either component. This row is the composition point the page does own; when
 * the bubble grows a slot for page-supplied actions, this component moves in
 * unchanged — it takes nothing but its message and the retry callback.
 */
function MessageActions({ message, onRetry }: MessageActionsProps): JSX.Element {
  return (
    <div style={ACTIONS_ROW_STYLE} role="group" aria-label="کنش‌های پاسخ آخر">
      {message.interrupted ? <span style={INTERRUPTED_STYLE}>پاسخ ناقص ماند</span> : null}
      {/* Copy lives on the bubble itself (Thread renders it there), so it is not
          repeated here — one control per action, or the second one is a decoy. */}
      <button type="button" className="msg__action" onClick={onRetry}>
        تلاش دوباره
      </button>
    </div>
  );
}

interface EmptyStateProps {
  email: string | null;
  onPick: (prompt: string) => void;
}

function EmptyState({ email, onPick }: EmptyStateProps): JSX.Element {
  const name = localPart(email);

  return (
    <section className="chat-empty" aria-labelledby="chat-empty-title">
      <h2 id="chat-empty-title">
        {/* The local part is usually Latin: isolating it keeps the Persian
            sentence around it in reading order. */}
        {name !== null ? (
          <>
            سلام{" "}
            <span dir="ltr" style={{ unicodeBidi: "isolate" }}>
              {name}
            </span>
            ، چه کاری برایتان انجام دهم؟
          </>
        ) : (
          <>سلام، چه کاری برایتان انجام دهم؟</>
        )}
      </h2>

      <p>
        می‌توانم تعداد و فهرست بیمه‌نامه‌ها، تیکت‌های باز پشتیبانی، معاملات و منابع دادهٔ
        سازمانی شما را از سامانه‌های ثبت‌شده بخوانم و فقط بر همان داده استناد کنم؛ اگر
        داده‌ای نباشد، صریح می‌گویم.
      </p>

      <p className="chat-empty__hint">برای شروع یکی از نمونه‌ها را انتخاب کنید:</p>
      <ul className="chat-empty__chips">
        {SUGGESTIONS.map((suggestion) => (
          <li key={suggestion}>
            <button
              type="button"
              className="chat-chip"
              onClick={() => {
                onPick(suggestion);
              }}
            >
              {suggestion}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

interface InlineErrorProps {
  error: ChatError;
  onDismiss: () => void;
}

/**
 * Errors while the transcript is empty.
 *
 * `Thread` owns the in-conversation alert, but it renders nothing at all before
 * the first message — so a failed first question would otherwise leave the page
 * with no visible failure. Same contract, smaller frame.
 */
function InlineError({ error, onDismiss }: InlineErrorProps): JSX.Element {
  return (
    <div className="alert" role="alert" style={{ margin: "0 auto 1rem", maxWidth: "46rem" }}>
      <div className="alert__body">
        <p className="alert__message">{error.message}</p>
        {error.detail ? (
          <p className="alert__detail" dir="auto">
            {error.detail}
          </p>
        ) : null}
        {error.requestId ? (
          <p className="alert__trace">
            کد پیگیری: <code className="ltr">{error.requestId}</code>
          </p>
        ) : null}
      </div>
      <button type="button" className="alert__close" aria-label="بستن پیام خطا" onClick={onDismiss}>
        ✕
      </button>
    </div>
  );
}

export function ChatPage({ token, email, onSignOut }: ChatPageProps): JSX.Element {
  // The hook is the only state source on this page: conversations, transcript,
  // streaming text, tools and errors all come from it.
  const chat = useChat(token);
  const [draft, setDraft] = useState<string>("");
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSigningOut, setIsSigningOut] = useState(false);

  const activeConversation =
    chat.conversations.find((item) => item.id === chat.activeConversationId) ?? null;

  const headerTitle = activeConversation
    ? activeConversation.title?.trim()
      ? activeConversation.title
      : "گفتگوی بدون عنوان"
    : "دستیار سازمانی";

  // The greeting is shown whenever the transcript is empty — including a thread
  // that was just created, where an id already exists. Keying it on "no active
  // conversation" would hide the greeting exactly when it is needed.
  const isEmpty = chat.messages.length === 0 && !chat.isLoadingMessages;
  const lastMessage = chat.messages.length > 0 ? chat.messages[chat.messages.length - 1] : undefined;
  const showActions =
    lastMessage !== undefined &&
    lastMessage.role === "assistant" &&
    lastMessage.id !== chat.pendingReplyId &&
    lastMessage.content.length > 0;

  const closeSidebar = (): void => {
    setIsSidebarOpen(false);
  };

  const handleSend = (): void => {
    const content = draft;
    if (content.trim().length === 0) return;
    // Cleared before the request settles: the question is already on screen
    // optimistically, so leaving it in the box would show it twice.
    setDraft("");
    void chat.send(content);
  };

  const handleStop = (): void => {
    chat.stop();
  };

  const handleSignOut = (): void => {
    if (isSigningOut) return;
    setIsSigningOut(true);
    void onSignOut()
      // The session hook owns auth failures; the button must not stay stuck.
      .catch(() => undefined)
      .finally(() => {
        setIsSigningOut(false);
      });
  };

  return (
    <AppShell
      sidebar={
        <Sidebar
          conversations={chat.conversations}
          activeId={chat.activeConversationId}
          onSelect={chat.selectConversation}
          onRename={chat.renameConversation}
          onDelete={chat.deleteConversation}
          onCreate={() => {
            closeSidebar();
            void chat.startConversation();
          }}
          isCreating={chat.isCreatingConversation}
          loading={chat.isLoadingConversations}
          email={email}
          onSignOut={handleSignOut}
          open={isSidebarOpen}
          onClose={closeSidebar}
        />
      }
    >
      <header style={HEADER_STYLE}>
        <button
          type="button"
          style={TOGGLE_STYLE}
          aria-expanded={isSidebarOpen}
          aria-controls="conversations"
          aria-label={isSidebarOpen ? "بستن فهرست گفتگوها" : "نمایش فهرست گفتگوها"}
          onClick={() => {
            setIsSidebarOpen((open) => !open);
          }}
        >
          <span aria-hidden="true">☰</span>
        </button>
        <h1 style={HEADER_TITLE_STYLE} title={headerTitle}>
          {headerTitle}
        </h1>
      </header>

      {isEmpty ? (
        <div
          className="thread"
          style={{ display: "flex", flexDirection: "column", justifyContent: "center" }}
        >
          {chat.error ? <InlineError error={chat.error} onDismiss={chat.dismissError} /> : null}
          <EmptyState
            email={email}
            onPick={(prompt) => {
              // Fill, do not send: the user still gets to edit before committing.
              setDraft(prompt);
            }}
          />
        </div>
      ) : (
        <Thread
          messages={chat.messages}
          pendingReplyId={chat.pendingReplyId}
          streamingText={chat.streamingText}
          tools={chat.toolsUsed}
          error={chat.error}
          onDismissError={chat.dismissError}
        />
      )}

      {showActions && lastMessage !== undefined ? (
        <MessageActions
          message={lastMessage}
          onRetry={() => {
            void chat.retry();
          }}
        />
      ) : null}

      <Composer
        value={draft}
        onChange={setDraft}
        onSend={handleSend}
        onStop={handleStop}
        isStreaming={chat.isSending}
        disabled={chat.isSending || chat.isLoadingMessages}
        maxLength={MAX_LENGTH}
      />
    </AppShell>
  );
}
