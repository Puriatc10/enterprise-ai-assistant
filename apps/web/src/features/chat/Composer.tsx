import { useEffect, useRef } from "react";

interface ComposerProps {
  value: string;
  onChange: (next: string) => void;
  onSubmit: (text: string) => Promise<void>;
  /** True while a reply is in flight: the field and the button are locked. */
  disabled: boolean;
  /** Bumping this number moves focus into the textarea (e.g. after a chip click). */
  focusSignal: number;
}

const MAX_LENGTH = 4000;
/** The counter is noise on a short question; it appears only near the ceiling. */
const COUNTER_THRESHOLD = 3600;
const FA_NUMBER = new Intl.NumberFormat("fa-IR");

export function Composer({ value, onChange, onSubmit, disabled, focusSignal }: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const trimmed = value.trim();
  const canSend = trimmed.length > 0 && !disabled;

  // Refocusing has to wait for the field to be enabled again: `.focus()` on a
  // disabled textarea is silently ignored, which would strand the caret in
  // <body> after every send.
  useEffect(() => {
    if (!disabled) {
      textareaRef.current?.focus();
    }
  }, [disabled, focusSignal]);

  const submit = () => {
    if (!canSend) return;
    // Cleared before the request settles: the question is already on screen
    // optimistically, so leaving it in the box would show it twice.
    onChange("");
    void onSubmit(trimmed);
  };

  return (
    <form
      className="chat-composer"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label className="chat-sr-only" htmlFor="chat-composer-input">
        متن سؤال
      </label>
      <textarea
        id="chat-composer-input"
        ref={textareaRef}
        className="chat-composer__input"
        value={value}
        rows={2}
        maxLength={MAX_LENGTH}
        disabled={disabled}
        placeholder="سؤال خود را بنویسید…"
        aria-describedby="chat-composer-hint"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          // An IME (Persian input methods compose characters) also emits Enter to
          // accept a candidate; committing a word must not send the message.
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit();
          }
        }}
      />

      <div className="chat-composer__actions">
        <span className="chat-composer__hint" id="chat-composer-hint">
          {value.length >= COUNTER_THRESHOLD ? (
            <span className={value.length >= MAX_LENGTH ? "chat-composer__counter is-full" : "chat-composer__counter"}>
              {FA_NUMBER.format(value.length)} / {FA_NUMBER.format(MAX_LENGTH)}
            </span>
          ) : (
            <span className="chat-composer__keys">Enter برای ارسال · Shift+Enter برای خط جدید</span>
          )}
        </span>

        <button type="button" className="chat-composer__send" onClick={submit} disabled={!canSend}>
          {disabled ? "در حال ارسال…" : "ارسال"}
        </button>
      </div>
    </form>
  );
}
