import { useEffect, useRef } from "react";
import type { JSX } from "react";

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled: boolean;
  maxLength: number;
}

const FA_NUMBER = new Intl.NumberFormat("fa-IR");
/** The counter is noise on a short question; it appears only near the ceiling. */
const COUNTER_WINDOW = 200;

function SendIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" aria-hidden="true">
      <path d="M10 15.5v-11M5.6 9L10 4.5 14.4 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function StopIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" aria-hidden="true">
      <rect x="6.2" y="6.2" width="7.6" height="7.6" rx="1.6" fill="currentColor" />
    </svg>
  );
}

export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  isStreaming,
  disabled,
  maxLength,
}: ComposerProps): JSX.Element {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const locked = disabled || isStreaming;
  const canSend = value.trim().length > 0 && !locked;
  const remaining = maxLength - value.length;

  // Auto-grow: reset to "auto" before measuring, otherwise scrollHeight only
  // ever reports the already-grown box and the field can never shrink back
  // after a deletion. CSS max-height caps it at 8 lines, then it scrolls.
  useEffect(() => {
    const field = textareaRef.current;
    if (!field) return;
    field.style.height = "auto";
    const max = Number.parseFloat(window.getComputedStyle(field).maxHeight);
    const height = Number.isFinite(max) ? Math.min(field.scrollHeight, max) : field.scrollHeight;
    field.style.height = `${height}px`;
  }, [value]);

  // Focus has to wait until the field is enabled again: .focus() on a disabled
  // textarea is silently ignored, which would strand the caret in <body> after
  // every send and break typing without a click.
  useEffect(() => {
    if (!locked) textareaRef.current?.focus();
  }, [locked]);

  const submit = (): void => {
    if (!canSend) return;
    onSend();
  };

  return (
    <div className="composer-dock">
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <label className="sr-only" htmlFor="composer-input">
          متن سؤال
        </label>
        <textarea
          id="composer-input"
          ref={textareaRef}
          className="composer__input"
          value={value}
          rows={1}
          maxLength={maxLength}
          disabled={locked}
          placeholder="سؤال خود را بنویسید…"
          aria-describedby="composer-hint"
          onChange={(event) => {
            onChange(event.target.value);
          }}
          onKeyDown={(event) => {
            // A Persian IME also emits Enter to accept a candidate; committing a
            // word must not send the message.
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              submit();
            }
          }}
        />

        <div className="composer__row">
          <p className="composer__hint" id="composer-hint">
            {remaining <= COUNTER_WINDOW ? (
              <span className={remaining <= 0 ? "composer__count is-full" : "composer__count"}>
                {FA_NUMBER.format(value.length)} / {FA_NUMBER.format(maxLength)}
              </span>
            ) : (
              <span className="composer__keys">
                {/* "send with Enter" / "new line with Shift+Enter": naming the
                    action first keeps the pair unambiguous in a bidi line. */}
                ارسال با <kbd>Enter</kbd>
                <span aria-hidden="true"> · </span>
                خط جدید با <kbd>Shift+Enter</kbd>
              </span>
            )}
          </p>

          {isStreaming ? (
            <button type="button" className="composer__stop" onClick={onStop} aria-label="توقف پاسخ">
              <StopIcon />
            </button>
          ) : (
            <button type="submit" className="composer__send" disabled={!canSend} aria-label="ارسال سؤال">
              <SendIcon />
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
