/**
 * Sign-in / sign-up screen. Persian, RTL, no external UI library.
 *
 * The component owns only presentation: credentials go straight to the callbacks,
 * which are `useSession().signIn` / `signUp`. Errors arriving from those callbacks
 * are already Persian, so they are rendered verbatim inside `role="alert"`.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, JSX } from "react";

import { EmailConfirmationRequiredError } from "../hooks/useSession";
import "./auth.css";

type Mode = "signIn" | "signUp";

interface LoginPageProps {
  onSignIn: (email: string, password: string) => Promise<void>;
  onSignUp: (email: string, password: string) => Promise<void>;
}

export function LoginPage({ onSignIn, onSignUp }: LoginPageProps): JSX.Element {
  const [mode, setMode] = useState<Mode>("signIn");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Separate from `error`: "confirmation email sent" is a success message, and
  // announcing it through role="alert" would read as a failure.
  const [notice, setNotice] = useState<string | null>(null);

  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // The page has exactly one purpose; putting the caret in the first field
    // saves a tab press and works with screen readers.
    emailRef.current?.focus();
  }, []);

  const toggleMode = useCallback(() => {
    setMode((current) => (current === "signIn" ? "signUp" : "signIn"));
    setError(null);
    setNotice(null);
  }, []);

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (pending) {
        return;
      }

      const trimmedEmail = email.trim();
      setError(null);
      setNotice(null);
      setPending(true);

      try {
        if (mode === "signIn") {
          await onSignIn(trimmedEmail, password);
        } else {
          await onSignUp(trimmedEmail, password);
        }
      } catch (caught: unknown) {
        if (caught instanceof EmailConfirmationRequiredError) {
          // Sign-up worked but the project requires a click on the emailed link.
          // Switching to "sign in" prepares the obvious next step.
          setNotice(caught.message);
          setPassword("");
          setMode("signIn");
        } else if (caught instanceof Error && caught.message.length > 0) {
          setError(caught.message);
        } else {
          setError("عملیات انجام نشد. لطفاً دوباره تلاش کنید.");
        }
      } finally {
        setPending(false);
      }
    },
    [email, mode, onSignIn, onSignUp, password, pending],
  );

  const isSignUp = mode === "signUp";

  return (
    <div className="auth" dir="rtl" lang="fa">
      <main className="auth__card">
        <p className="auth__eyebrow">دستیار هوشمند سازمانی</p>
        <h1 className="auth__title">{isSignUp ? "ایجاد حساب" : "ورود"}</h1>
        <p className="auth__subtitle">
          برای پرسیدن پرسش از داده‌های سازمان، ابتدا وارد حساب خود شوید.
        </p>

        <form className="auth__form" onSubmit={handleSubmit} noValidate={false} aria-busy={pending}>
          <div className="auth__field">
            <label className="auth__label" htmlFor="auth-email">
              ایمیل
            </label>
            <input
              ref={emailRef}
              id="auth-email"
              name="email"
              className="auth__input"
              type="email"
              inputMode="email"
              dir="ltr"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-invalid={error !== null}
              aria-describedby={error !== null ? "auth-error" : undefined}
              disabled={pending}
            />
          </div>

          <div className="auth__field">
            <label className="auth__label" htmlFor="auth-password">
              رمز عبور
            </label>
            <input
              id="auth-password"
              name="password"
              className="auth__input"
              type="password"
              dir="ltr"
              autoComplete={isSignUp ? "new-password" : "current-password"}
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-invalid={error !== null}
              aria-describedby={error !== null ? "auth-error" : undefined}
              disabled={pending}
            />
            {isSignUp ? (
              <p className="auth__hint">دست‌کم ۶ نویسه.</p>
            ) : null}
          </div>

          <button type="submit" className="auth__submit" disabled={pending}>
            {pending ? "در حال ارسال…" : isSignUp ? "ثبت‌نام" : "ورود"}
          </button>
        </form>

        {error !== null ? (
          <p className="auth__error" id="auth-error" role="alert">
            {error}
          </p>
        ) : null}

        {notice !== null ? (
          <p className="auth__notice" role="status">
            {notice}
          </p>
        ) : null}

        <div className="auth__toggle">
          <span className="auth__text">
            {isSignUp ? "قبلاً حساب ساخته‌اید؟" : "حساب ندارید؟"}
          </span>
          <button
            type="button"
            className="auth__link"
            onClick={toggleMode}
            disabled={pending}
          >
            {isSignUp ? "ورود" : "ثبت‌نام"}
          </button>
        </div>

        <p className="auth__footnote" role="note">
          این محیط نمایشی است؛ از رمز عبور واقعی سازمانی استفاده نکنید.
        </p>
      </main>
    </div>
  );
}
