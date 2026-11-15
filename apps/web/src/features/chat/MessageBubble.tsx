import type { JSX, ReactNode } from "react";

import type { ChatMessage } from "../../services/api";
import { Markdown } from "./Markdown";

const TIME_FORMAT = new Intl.DateTimeFormat("fa-IR", { hour: "2-digit", minute: "2-digit" });

interface MessageBubbleProps {
  message: ChatMessage;
  /** True while text is arriving: the bubble becomes a polite live region. */
  streaming?: boolean;
  /** Actions revealed on hover/focus (copy, retry…), rendered by the parent. */
  children?: ReactNode;
}

const ROLE_LABEL: Record<ChatMessage["role"], string> = {
  user: "شما",
  assistant: "دستیار",
  system: "سیستم",
  tool: "ابزار",
};

function formatTime(value: string): string {
  const parsed = new Date(value);
  // Ordering is trusted; a malformed timestamp must not print "Invalid Date"
  // in the middle of a Persian sentence.
  return Number.isNaN(parsed.getTime()) ? "" : TIME_FORMAT.format(parsed);
}

export function MessageBubble({ message, streaming = false, children }: MessageBubbleProps): JSX.Element {
  const time = formatTime(message.created_at);

  // System and tool turns are machinery, not conversation: they get a quiet
  // centred line so they never read as something the assistant said.
  if (message.role === "system" || message.role === "tool") {
    return (
      <div className="meta-msg">
        <span className="meta-msg__role">{ROLE_LABEL[message.role]}</span>
        <span className="meta-msg__text" dir="auto">
          {message.content}
        </span>
      </div>
    );
  }

  const isUser = message.role === "user";

  return (
    <article className={isUser ? "msg msg--user" : "msg msg--assistant"}>
      {/* The visual speaker cue is alignment and tint; screen readers get the
          name in words instead of inferring it from CSS. */}
      <div className="msg__bubble" aria-live={streaming ? "polite" : undefined}>
        <span className="sr-only">{ROLE_LABEL[message.role]}: </span>
        {isUser ? (
          // The user's own words are literal: no markdown, no reformatting.
          <p className="msg__text" dir="auto">
            {message.content}
          </p>
        ) : streaming ? (
          // While text is arriving it is shown raw: half-written markdown would
          // flicker between states, and the caret belongs inline with the text.
          // It formats once, when the answer is complete.
          <p className="msg__text" dir="auto">
            {message.content}
            <span className="msg__caret" aria-hidden="true" />
          </p>
        ) : (
          <Markdown>{message.content}</Markdown>
        )}
      </div>

      <div className="msg__meta">
        {time ? (
          <time className="msg__time" dateTime={message.created_at}>
            {time}
          </time>
        ) : null}
        {children ? <div className="msg__actions">{children}</div> : null}
      </div>
    </article>
  );
}
