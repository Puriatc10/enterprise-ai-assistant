/**
 * Session state for the whole app, derived from supabase-js.
 *
 * The Supabase client is the single source of truth: this hook reads the stored
 * session on mount, then mirrors `onAuthStateChange` so a background token
 * refresh or a sign-out in another tab reaches the UI without a reload.
 *
 * Every component that needs the token calls `useSession()`; supabase-js
 * broadcasts auth events to all subscribers, so per-instance state stays
 * consistent. No provider is needed, which keeps `main.tsx` trivial and means a
 * component never fails because it was rendered outside a context.
 *
 * No session object is ever logged: the access token is a credential.
 */

import type { Session, SupabaseClient } from "@supabase/supabase-js";
import { useCallback, useEffect, useState } from "react";

import { env } from "../lib/env";
import { supabase } from "../services/supabase";

export type SessionState =
  | { status: "misconfigured"; missing: string[] }
  | { status: "loading" }
  | { status: "signedOut" }
  | { status: "signedIn"; token: string; email: string | null };

export interface UseSessionResult {
  session: SessionState;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

/**
 * Thrown by `signUp` when the project requires email confirmation. It is an
 * `Error` (so `catch (error)` works for everyone) but a distinguishable one, so
 * the UI can present it as good news — "check your inbox" — instead of failure.
 */
export class EmailConfirmationRequiredError extends Error {
  constructor(
    message = "ثبت‌نام انجام شد. یک ایمیل تأیید برای شما ارسال شد؛ پس از تأیید ایمیل، وارد شوید.",
  ) {
    super(message);
    this.name = "EmailConfirmationRequiredError";
  }
}

const MESSAGES = {
  wrongCredentials: "ایمیل یا رمز عبور نادرست است.",
  weakPassword: "رمز عبور ضعیف است. رمز عبور قوی‌تری انتخاب کنید (دست‌کم ۶ نویسه).",
  alreadyRegistered: "این ایمیل قبلاً ثبت شده است. وارد شوید یا رمز عبور را بازیابی کنید.",
  invalidEmail: "ایمیل واردشده معتبر نیست.",
  emailNotConfirmed: "ایمیل شما هنوز تأیید نشده است. لینک تأیید را در صندوق پستی خود بررسی کنید.",
  rateLimited: "تعداد تلاش‌ها بیش از حد مجاز است. چند دقیقه صبر کنید و دوباره تلاش کنید.",
  signupDisabled: "ثبت‌نام در این محیط غیرفعال است. با مدیر سامانه تماس بگیرید.",
  sessionExpired: "نشست شما منقضی شده است. دوباره وارد شوید.",
  network: "ارتباط با سرویس احراز هویت برقرار نشد. اتصال شبکه را بررسی کنید و دوباره تلاش کنید.",
  generic: "عملیات احراز هویت انجام نشد. لطفاً دوباره تلاش کنید.",
  noSession: "سرور نشستی بازنگرداند. لطفاً دوباره تلاش کنید.",
} as const;

function readString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function authErrorCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null) {
    return null;
  }
  return readString((error as { code?: unknown }).code);
}

function authErrorMessage(error: unknown): string {
  if (typeof error !== "object" || error === null) {
    return "";
  }
  return readString((error as { message?: unknown }).message) ?? "";
}

/**
 * Map a Supabase auth error to one short Persian sentence.
 *
 * Codes are checked first because they are stable; the English message is only
 * used as a pattern to classify the error, never shown to the user.
 */
export function describeAuthError(error: unknown): string {
  switch (authErrorCode(error)) {
    case "invalid_credentials":
    case "invalid_grant":
    case "user_not_found":
      return MESSAGES.wrongCredentials;
    case "weak_password":
      return MESSAGES.weakPassword;
    case "user_already_exists":
    case "email_exists":
      return MESSAGES.alreadyRegistered;
    case "email_address_invalid":
    case "validation_failed":
      return MESSAGES.invalidEmail;
    case "email_not_confirmed":
      return MESSAGES.emailNotConfirmed;
    case "over_request_rate_limit":
    case "over_email_send_rate_limit":
    case "over_sms_send_rate_limit":
      return MESSAGES.rateLimited;
    case "signup_disabled":
    case "email_provider_disabled":
    case "provider_disabled":
      return MESSAGES.signupDisabled;
    case "session_not_found":
    case "session_expired":
    case "refresh_token_not_found":
    case "refresh_token_already_used":
    case "bad_jwt":
      return MESSAGES.sessionExpired;
    default:
      break;
  }

  const message = authErrorMessage(error).toLowerCase();
  if (message.includes("invalid login credentials")) {
    return MESSAGES.wrongCredentials;
  }
  if (message.includes("already registered") || message.includes("already exists")) {
    return MESSAGES.alreadyRegistered;
  }
  if (message.includes("weak") && message.includes("password")) {
    return MESSAGES.weakPassword;
  }
  if (message.includes("invalid email")) {
    return MESSAGES.invalidEmail;
  }
  if (message.includes("email not confirmed")) {
    return MESSAGES.emailNotConfirmed;
  }
  if (
    message.includes("failed to fetch") ||
    message.includes("networkerror") ||
    message.includes("network request failed") ||
    message.includes("load failed")
  ) {
    return MESSAGES.network;
  }
  return MESSAGES.generic;
}

function toSessionState(session: Session | null): SessionState {
  if (!session || !session.access_token) {
    return { status: "signedOut" };
  }
  return {
    status: "signedIn",
    token: session.access_token,
    email: session.user.email ?? null,
  };
}

function initialSessionState(): SessionState {
  // Without a client there is nothing to wait for: a missing variable is a
  // configuration fact, not a loading state.
  if (supabase === null) {
    return { status: "misconfigured", missing: env.missing };
  }
  return { status: "loading" };
}

function requireClient(): SupabaseClient {
  if (supabase === null) {
    throw new Error(
      "پیکربندی احراز هویت کامل نیست. متغیرهای محیطی Supabase را تنظیم کنید و صفحه را دوباره بارگذاری کنید.",
    );
  }
  return supabase;
}

export function useSession(): UseSessionResult {
  const [session, setSession] = useState<SessionState>(initialSessionState);

  useEffect(() => {
    const client = supabase;
    if (client === null) {
      return;
    }

    // Guards against state writes after unmount: getSession() is async and the
    // component can disappear while it is in flight.
    let active = true;

    void client.auth.getSession().then(({ data, error }) => {
      if (!active) {
        return;
      }
      if (error) {
        setSession({ status: "signedOut" });
        return;
      }
      setSession(toSessionState(data.session));
    });

    // Keeps the token fresh (supabase-js refreshes it in the background) and
    // reacts to a sign-out performed in another tab.
    const { data: authListener } = client.auth.onAuthStateChange((_event, nextSession) => {
      if (!active) {
        return;
      }
      setSession(toSessionState(nextSession));
    });

    return () => {
      active = false;
      authListener.subscription.unsubscribe();
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string): Promise<void> => {
    const client = requireClient();
    const { data, error } = await client.auth.signInWithPassword({
      email: email.trim(),
      password,
    });

    if (error) {
      throw new Error(describeAuthError(error));
    }
    if (!data.session) {
      throw new Error(MESSAGES.noSession);
    }
    setSession(toSessionState(data.session));
  }, []);

  const signUp = useCallback(async (email: string, password: string): Promise<void> => {
    const client = requireClient();
    const { data, error } = await client.auth.signUp({
      email: email.trim(),
      password,
    });

    if (error) {
      throw new Error(describeAuthError(error));
    }

    // With "Confirm email" enabled the project returns a user but no session.
    // Pretending the user is signed in here would produce 401s on every API call.
    if (!data.session) {
      throw new EmailConfirmationRequiredError();
    }
    setSession(toSessionState(data.session));
  }, []);

  const signOut = useCallback(async (): Promise<void> => {
    const client = requireClient();
    try {
      await client.auth.signOut();
    } catch {
      // A failed network call must not trap the user in a signed-in shell; the
      // local session is cleared below and the next mount re-reads the storage.
    } finally {
      setSession({ status: "signedOut" });
    }
  }, []);

  return { session, signIn, signUp, signOut };
}
