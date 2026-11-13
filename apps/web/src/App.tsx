import { useCallback, useEffect, useState } from "react";

import { HealthCard, type HealthState } from "./components/HealthCard";
import { API_BASE_URL, ApiError, fetchHealth } from "./services/api";

/**
 * Phase 0 shell.
 *
 * The chat interface arrives in Phase 5. What exists now is deliberately honest:
 * a page that proves the frontend is wired to a configured API and shows that
 * API's real state — no mock conversation, no fake answers.
 */
export function App() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });

  const checkBackend = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const health = await fetchHealth();
      setState({ kind: "ok", health });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Unexpected error while contacting the backend.",
      });
    }
  }, []);

  useEffect(() => {
    void checkBackend();
  }, [checkBackend]);

  return (
    <div className="page">
      <header className="page__header">
        <p className="eyebrow">Enterprise AI Assistant</p>
        <h1>Ask your systems, not your memory.</h1>
        <p className="lede">
          An assistant that answers questions about data held across multiple
          organizational sources — through narrow, read-only, authorized tools. Answers come
          from retrieved data, never from the model's imagination.
        </p>
      </header>

      <HealthCard state={state} onRetry={checkBackend} />

      <section className="panel">
        <h2>Project status</h2>
        <p>
          <strong>Phase 0 — bootstrap.</strong> The foundation is in place; no chat, no
          connectors, and no authentication are enabled yet by design.
        </p>

        <div className="columns">
          <div>
            <h3>Available now</h3>
            <ul>
              <li>FastAPI application with a credential-free health endpoint</li>
              <li>React + TypeScript shell with a configurable API base URL</li>
              <li>Docker Compose development stack</li>
              <li>Architecture, security, and roadmap documentation</li>
            </ul>
          </div>
          <div>
            <h3>Next phases</h3>
            <ul>
              <li>Phase 1 — technical validation (Didar24 access, Gemini tool calling)</li>
              <li>Phase 2 — auth, persistence, LLM port</li>
              <li>Phase 3 — tool registry and guarded execution</li>
              <li>Phase 4 — read-only data connectors</li>
            </ul>
          </div>
        </div>
      </section>

      <footer className="page__footer">
        <span>
          API base URL: <code>{API_BASE_URL}</code>
        </span>
        <span className="muted">Set via VITE_API_BASE_URL at build time.</span>
      </footer>
    </div>
  );
}
