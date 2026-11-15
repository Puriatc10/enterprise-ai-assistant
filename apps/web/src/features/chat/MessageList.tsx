import { useEffect, useRef } from "react";

import type { ChatMessage } from "../../services/api";

interface MessageListProps {
  messages: ChatMessage[];
  /** Id of the optimistic reply bubble; rendered as a thinking line. */
  pendingReplyId: string | null;
  isLoading: boolean;
  onPickExample: (prompt: string) => void;
}

const TIME_FORMAT = new Intl.DateTimeFormat("fa-IR", { hour: "2-digit", minute: "2-digit" });

// Examples are written against the tools that actually exist
// (get_my_policy_count, get_my_policies, get_policy_details, get_policy_status),
// so a chip can never promise a capability the backend does not have.
const EXAMPLES: readonly string[] = [
  "چند بیمه‌نامه دارم؟",
  "بیمه‌نامه‌های من را فهرست کن.",
  "وضعیت بیمه‌نامه‌هایم فعال است؟",
];

function formatTime(value: string): string {
  const parsed = new Date(value);
  // Server timestamps are trusted for ordering only; an unparseable one must not
  // render "Invalid Date" inside Persian text.
  return Number.isNaN(parsed.getTime()) ? "" : TIME_FORMAT.format(parsed);
}

function roleLabel(role: ChatMessage["role"]): string {
  switch (role) {
    case "user":
      return "شما";
    case "assistant":
      return "دستیار";
    case "system":
      return "سیستم";
    case "tool":
      return "ابزار";
  }
}

export function MessageList({ messages, pendingReplyId, isLoading, onPickExample }: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Newest message into view: an answer that lands below the fold reads as
  // "nothing happened" even though the request succeeded.
  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    container.scrollTop = container.scrollHeight;
  }, [messages, pendingReplyId, isLoading]);

  return (
    <div className="chat-thread" ref={scrollRef}>
      {isLoading ? (
        <p className="chat-thread__status" role="status">
          در حال بارگذاری این گفتگو…
        </p>
      ) : null}

      {!isLoading && messages.length === 0 ? (
        <section className="chat-empty" aria-labelledby="chat-empty-title">
          <h2 id="chat-empty-title">چه چیزی می‌توانم بپرسم؟</h2>
          <p>
            این دستیار فقط از داده‌های مجاز خودتان پاسخ می‌دهد: با اجرای ابزارهای خواندنی،
            اطلاعات ثبت‌شده را می‌خواند و به همان استناد می‌کند. اگر داده‌ای در دسترس نباشد،
            صریح می‌گوید — حدس نمی‌زند.
          </p>
          <p className="chat-empty__hint">برای شروع یکی از نمونه‌ها را انتخاب کنید:</p>
          <ul className="chat-empty__chips">
            {EXAMPLES.map((example) => (
              <li key={example}>
                <button
                  type="button"
                  className="chat-chip"
                  onClick={() => {
                    onPickExample(example);
                  }}
                >
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/*
       * role="log" + polite live region: the pending bubble is aria-busy, so
       * screen readers wait and announce the finished answer once instead of
       * reading "thinking" and then the whole message again.
       *
       * The list stays mounted while empty on purpose: a live region created in
       * the same render as its first child is not reliably announced, and the
       * empty -> first message transition is exactly when it matters.
       */}
      <ol
        className="chat-thread__list"
        role="log"
        aria-live="polite"
        aria-relevant="additions text"
        aria-busy={pendingReplyId !== null}
      >
        {messages.map((message) => {
          const isPending = message.id === pendingReplyId;
          const time = formatTime(message.created_at);
          const isMeta = message.role === "system" || message.role === "tool";

          if (isMeta) {
            return (
              <li key={message.id} className="chat-msg chat-msg--meta">
                <p className="chat-msg__meta">
                  <span className="chat-msg__meta-role">{roleLabel(message.role)}</span>
                  <span dir="auto">{message.content}</span>
                </p>
              </li>
            );
          }

          return (
            <li
              key={message.id}
              className={`chat-msg ${message.role === "user" ? "chat-msg--user" : "chat-msg--assistant"}`}
            >
              <div className={`chat-msg__bubble${isPending ? " chat-msg__bubble--pending" : ""}`}>
                <p className="chat-msg__role">
                  {roleLabel(message.role)}
                  {time ? <span className="chat-msg__time">{time}</span> : null}
                </p>
                {isPending ? (
                  <p className="chat-msg__thinking">
                    <span className="chat-msg__dot" aria-hidden="true" />
                    در حال فکر کردن…
                  </p>
                ) : (
                  /*
                   * dir="auto" per bubble: the shell is RTL, but an answer that is
                   * entirely English (tool names, identifiers) must not be forced
                   * into reverse word order. `text-align: start` then follows
                   * whichever direction won.
                   */
                  <p className="chat-msg__text" dir="auto">
                    {message.content}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
