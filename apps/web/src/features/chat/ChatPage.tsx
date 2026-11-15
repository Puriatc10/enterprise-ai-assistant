import { useState } from "react";

import { Composer } from "./Composer";
import { ConversationSidebar } from "./ConversationSidebar";
import { MessageList } from "./MessageList";
import { ToolActivity } from "./ToolActivity";
import { useChat, type ChatError } from "./useChat";
import "./chat.css";

export interface ChatPageProps {
  /** Bearer token from the session hook; never rendered, never logged. */
  token: string;
  /** Signed-in address, shown in the sidebar footer so the account is never ambiguous. */
  email: string | null;
  onSignOut: () => Promise<void>;
}

interface ErrorBannerProps {
  error: ChatError;
  onDismiss: () => void;
  onSignOut: () => void;
}

function ErrorBanner({ error, onDismiss, onSignOut }: ErrorBannerProps) {
  return (
    <div className="chat-error" role="alert">
      <div className="chat-error__body">
        <p className="chat-error__title">{error.message}</p>
        {/* The server's own sentence is kept verbatim: the Persian line is a
            headline, not a paraphrase that could hide what actually broke. */}
        {error.detail ? (
          <p className="chat-error__detail" dir="auto">
            {error.detail}
          </p>
        ) : null}
        {error.requestId ? (
          <p className="chat-error__trace">
            کد پیگیری: <code className="chat-ltr">{error.requestId}</code>
          </p>
        ) : null}
      </div>
      <div className="chat-error__actions">
        {error.sessionExpired ? (
          <button type="button" className="chat-error__action" onClick={onSignOut}>
            خروج و ورود دوباره
          </button>
        ) : null}
        <button type="button" className="chat-error__action chat-error__action--ghost" onClick={onDismiss}>
          بستن
        </button>
      </div>
    </div>
  );
}

export function ChatPage({ token, email, onSignOut }: ChatPageProps) {
  const chat = useChat(token);
  const [draft, setDraft] = useState("");
  const [focusSignal, setFocusSignal] = useState(0);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSigningOut, setIsSigningOut] = useState(false);

  const activeConversation =
    chat.conversations.find((item) => item.id === chat.activeConversationId) ?? null;

  const headerTitle = activeConversation
    ? activeConversation.title?.trim()
      ? activeConversation.title
      : "گفتگوی بدون عنوان"
    : "دستیار سازمانی";

  const handleSignOut = () => {
    if (isSigningOut) return;
    setIsSigningOut(true);
    void onSignOut()
      // The session hook owns auth failures; the button must not stay stuck.
      .catch(() => undefined)
      .finally(() => {
        setIsSigningOut(false);
      });
  };

  const handleNewConversation = () => {
    void chat.startConversation().then((created) => {
      setIsSidebarOpen(false);
      if (created) setFocusSignal((value) => value + 1);
    });
  };

  const handleSignOutFromBanner = () => {
    handleSignOut();
  };

  return (
    <div className="chat-shell" dir="rtl" lang="fa">
      <aside
        id="chat-sidebar"
        className={isSidebarOpen ? "chat-sidebar is-open" : "chat-sidebar"}
        aria-label="نوار کناری گفتگوها"
      >
        <ConversationSidebar
          conversations={chat.conversations}
          activeConversationId={chat.activeConversationId}
          onSelect={(conversationId) => {
            chat.selectConversation(conversationId);
            setIsSidebarOpen(false);
          }}
          onNewConversation={handleNewConversation}
          isCreating={chat.isCreatingConversation}
          isLoading={chat.isLoadingConversations}
          email={email}
          onSignOut={handleSignOut}
          isSigningOut={isSigningOut}
        />
      </aside>

      {/* A real button, not a div: the backdrop has to be reachable by keyboard
          and by screen readers on touch layouts, where it is the only way back. */}
      <button
        type="button"
        className={isSidebarOpen ? "chat-scrim is-open" : "chat-scrim"}
        aria-label="بستن فهرست گفتگوها"
        onClick={() => {
          setIsSidebarOpen(false);
        }}
      />

      <main className="chat-main">
        <header className="chat-topbar">
          <button
            type="button"
            className="chat-topbar__toggle"
            aria-expanded={isSidebarOpen}
            aria-controls="chat-sidebar"
            onClick={() => {
              setIsSidebarOpen((open) => !open);
            }}
          >
            <span aria-hidden="true">☰</span>
            <span className="chat-sr-only">
              {isSidebarOpen ? "بستن فهرست گفتگوها" : "نمایش فهرست گفتگوها"}
            </span>
          </button>
          <h1 className="chat-topbar__title" title={headerTitle}>
            {headerTitle}
          </h1>
        </header>

        {chat.error ? (
          <ErrorBanner error={chat.error} onDismiss={chat.dismissError} onSignOut={handleSignOutFromBanner} />
        ) : null}

        <MessageList
          messages={chat.messages}
          pendingReplyId={chat.pendingReplyId}
          isLoading={chat.isLoadingMessages}
          onPickExample={(prompt) => {
            setDraft(prompt);
            setFocusSignal((value) => value + 1);
          }}
        />

        <div className="chat-footer">
          {/* Only the last answer's tools are shown: older turns have no tool
              metadata in the history contract, and inventing it is not an option. */}
          <ToolActivity tools={chat.toolsUsed} />
          <Composer
            value={draft}
            onChange={setDraft}
            onSubmit={chat.send}
            disabled={chat.isSending || chat.isLoadingMessages}
            focusSignal={focusSignal}
          />
        </div>
      </main>
    </div>
  );
}
