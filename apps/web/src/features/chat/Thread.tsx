import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";

import type { ChatMessage, ToolUsage } from "../../services/api";
import { MessageBubble } from "./MessageBubble";
import { ToolActivity } from "./ToolActivity";

interface ThreadError {
  message: string;
  detail: string | null;
  requestId: string | null;
  sessionExpired: boolean;
}

interface ThreadProps {
  messages: ChatMessage[];
  /** Id of the reserved assistant slot waiting on the backend. */
  pendingReplyId: string | null;
  /** Text arriving right now, or null when nothing is streaming. */
  streamingText: string | null;
  /** Provenance of the last answer. */
  tools: ToolUsage[];
  error: ThreadError | null;
  onDismissError: () => void;
}

/** How close to the bottom still counts as "following the conversation". */
const STICK_THRESHOLD_PX = 96;

function AlertIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" aria-hidden="true">
      <path d="M10 3.2 17.4 16.2H2.6L10 3.2Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M10 8v3.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="10" cy="13.9" r="0.9" fill="currentColor" />
    </svg>
  );
}

function CloseIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" aria-hidden="true">
      <path d="M5.5 5.5l9 9m0-9l-9 9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function CopyIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 20 20" width="15" height="15" fill="none" aria-hidden="true">
      <rect x="7.2" y="7.2" width="8.6" height="8.6" rx="1.8" stroke="currentColor" strokeWidth="1.4" />
      <path d="M12.6 4.4H5.9c-.8 0-1.5.7-1.5 1.5v6.7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function CheckIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 20 20" width="15" height="15" fill="none" aria-hidden="true">
      <path d="M4.8 10.4l3.3 3.2 7-7.2" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Copy is the one action that needs no server round-trip, so it is offered on
 * every finished answer instead of leaving the hover row empty.
 */
function CopyAnswer({ text }: { text: string }): JSX.Element {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => {
      setCopied(false);
    }, 1600);
    return () => {
      window.clearTimeout(timer);
    };
  }, [copied]);

  return (
    <button
      type="button"
      className="msg__action"
      onClick={() => {
        // A denied clipboard (insecure origin, no permission) must not throw
        // into the render path; the label simply stays «کپی».
        void navigator.clipboard
          ?.writeText(text)
          .then(() => {
            setCopied(true);
          })
          .catch(() => undefined);
      }}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
      <span>{copied ? "کپی شد" : "کپی"}</span>
    </button>
  );
}

function ErrorBanner({ error, onDismiss }: { error: ThreadError; onDismiss: () => void }): JSX.Element {
  return (
    <div className="alert" role="alert">
      <span className="alert__icon" aria-hidden="true">
        <AlertIcon />
      </span>
      <div className="alert__body">
        <p className="alert__message">{error.message}</p>
        {/* The server's own sentence is kept verbatim: the Persian line is a
            headline, not a paraphrase that could hide what broke. */}
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
        <CloseIcon />
      </button>
    </div>
  );
}

export function Thread({
  messages,
  pendingReplyId,
  streamingText,
  tools,
  error,
  onDismissError,
}: ThreadProps): JSX.Element {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Follow new text only while the reader is already at the bottom. Yanking the
  // viewport away from someone re-reading an earlier answer is worse than not
  // auto-scrolling at all.
  const stickToBottom = useRef(true);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container || !stickToBottom.current) return;
    container.scrollTop = container.scrollHeight;
  }, [messages, pendingReplyId, streamingText]);

  if (messages.length === 0) {
    // The empty state (greeting, explanation, suggestion chips) belongs to the
    // page, not to the transcript. Nothing to show here yet.
    return (
      <div className="thread-view">
        {error ? <ErrorBanner error={error} onDismiss={onDismissError} /> : null}
        <div className="thread" ref={scrollRef} />
      </div>
    );
  }

  const lastIndex = messages.length - 1;
  const lastMessage = messages[lastIndex];

  return (
    <div className="thread-view">
      {/* Outside the scroller on purpose: an error raised by a failed send would
          otherwise sit above the last message and be scrolled out of sight on a
          long thread — exactly when the user needs to read it. */}
      {error ? <ErrorBanner error={error} onDismiss={onDismissError} /> : null}

      <div
        className="thread"
        ref={scrollRef}
        onScroll={() => {
          const container = scrollRef.current;
          if (!container) return;
          const distance = container.scrollHeight - container.scrollTop - container.clientHeight;
          stickToBottom.current = distance < STICK_THRESHOLD_PX;
        }}
      >
        <div className="thread__inner">
          {/* role="log" + polite: an answer is announced once, not token by token.
              The list is not conditionally mounted in a way that would create the
              live region together with its first child. */}
          <ol className="thread__list" role="log" aria-live="polite" aria-relevant="additions text">
            {messages.map((message, index) => {
              const isPending = message.id === pendingReplyId;
              const isStreaming = isPending && streamingText !== null;
              const isWaiting = isPending && streamingText === null;
              const isLast = index === lastIndex;

              // The reserved slot has no text of its own; while streaming, its
              // content is replaced by what has arrived so far.
              const displayed = isStreaming ? { ...message, content: streamingText } : message;

              return (
                <li key={message.id} className="thread__item">
                  <MessageBubble message={displayed} streaming={isStreaming}>
                    {message.role === "assistant" && !isPending && message.content.length > 0 ? (
                      <CopyAnswer text={message.content} />
                    ) : null}
                  </MessageBubble>

                  {isWaiting ? (
                    <p className="thread__waiting">
                      <span className="thread__dots" aria-hidden="true">
                        <span />
                        <span />
                        <span />
                      </span>
                      در حال آماده‌سازی پاسخ…
                    </p>
                  ) : null}

                  {isLast && lastMessage?.role === "assistant" && !isPending && tools.length > 0 ? (
                    <div className="thread__tools">
                      <ToolActivity tools={tools} />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </div>
      </div>
    </div>
  );
}
