import { lazy, Suspense } from "react";

import { ConfigNotice } from "./components/ConfigNotice";
import { LoginPage } from "./auth/LoginPage";
import { useSession } from "./hooks/useSession";
// Loaded here as well as in ChatPage so the pre-session states are styled even
// when no chat component has mounted yet.
import "./features/chat/chat.css";

/**
 * The chat feature is the heaviest thing in the app (markdown, the streaming client,
 * the whole thread). Loading it lazily keeps the login screen — the first thing anyone
 * sees — from paying for a renderer it cannot use yet.
 */
const ChatPage = lazy(async () => {
  const module = await import("./features/chat/ChatPage");
  return { default: module.ChatPage };
});

function BootScreen() {
  return (
    <div className="boot" dir="rtl" lang="fa">
      <p className="boot__text" role="status">
        در حال بررسی نشست…
      </p>
    </div>
  );
}

/**
 * Route over session state, nothing else.
 *
 * Each branch is a state the session hook actually reports, so there is no
 * "probably signed in" case to guess at: the app either cannot start
 * (misconfigured), is still checking, needs a login, or holds a token.
 */
export function App() {
  const { session, signIn, signUp, signOut } = useSession();

  switch (session.status) {
    case "misconfigured":
      return <ConfigNotice missing={session.missing} />;

    case "loading":
      return <BootScreen />;

    case "signedOut":
      return <LoginPage onSignIn={signIn} onSignUp={signUp} />;

    case "signedIn":
      return (
        <Suspense fallback={<BootScreen />}>
          <ChatPage token={session.token} email={session.email} onSignOut={signOut} />
        </Suspense>
      );
  }
}
