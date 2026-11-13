import type { HealthResponse } from "../types/api";

export type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; health: HealthResponse }
  | { kind: "error"; message: string };

interface HealthCardProps {
  state: HealthState;
  onRetry: () => void;
}

/**
 * Backend connectivity panel.
 *
 * Phase 0 has no chat yet, so this card is the honest surface of the system: it
 * shows whether the API answers and whether an LLM credential is configured.
 * It never displays a credential, only whether one exists.
 */
export function HealthCard({ state, onRetry }: HealthCardProps) {
  if (state.kind === "loading") {
    return (
      <section className="card" aria-live="polite">
        <div className="card__row">
          <span className="dot dot--pending" aria-hidden="true" />
          <h2>Checking the backend…</h2>
        </div>
        <p className="muted">This also covers cold starts, which can take a few seconds.</p>
      </section>
    );
  }

  if (state.kind === "error") {
    return (
      <section className="card card--error" aria-live="polite">
        <div className="card__row">
          <span className="dot dot--down" aria-hidden="true" />
          <h2>Backend unreachable</h2>
        </div>
        <p className="muted">{state.message}</p>
        <button type="button" className="button" onClick={onRetry}>
          Try again
        </button>
      </section>
    );
  }

  const { health } = state;

  return (
    <section className="card" aria-live="polite">
      <div className="card__row">
        <span className="dot dot--up" aria-hidden="true" />
        <h2>Backend online</h2>
      </div>

      <dl className="facts">
        <div>
          <dt>Service</dt>
          <dd>{health.service}</dd>
        </div>
        <div>
          <dt>Version</dt>
          <dd>{health.version}</dd>
        </div>
        <div>
          <dt>Environment</dt>
          <dd>{health.environment}</dd>
        </div>
        <div>
          <dt>LLM provider</dt>
          <dd>{health.llm.provider}</dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>{health.llm.model}</dd>
        </div>
        <div>
          <dt>Credential</dt>
          <dd>
            {health.llm.configured ? (
              <span className="tag tag--ok">configured</span>
            ) : (
              <span className="tag">not configured</span>
            )}
          </dd>
        </div>
      </dl>

      <button type="button" className="button" onClick={onRetry}>
        Re-check
      </button>
    </section>
  );
}
