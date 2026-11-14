# Roadmap

A phase is complete only when its exit criteria are met and verified — not when its code
exists. Phases are sequential: later phases assume earlier contracts are stable.

Legend: ✅ done · 🔜 next · ⬜ not started

---

## Phase 0 — Project Bootstrap ✅

**Goal**: a documented, runnable foundation.

Deliverables:

- Repository structure (`apps/api`, `apps/web`, `docs`).
- Architecture, tech-stack, security documents; ADR-0001; this roadmap.
- `AGENTS.md` engineering rules for coding agents.
- Runnable FastAPI app with a health endpoint; one backend test.
- Runnable React/Vite frontend that reads its API base URL from configuration.
- Docker Compose dev stack; `.env.example`; `.gitignore`/`.dockerignore`.

Exit criteria (all verified, see the bootstrap report):

- `uv run pytest` passes; `ruff check` clean.
- `npm run typecheck` and `npm run build` pass.
- `docker compose config` validates; containers start (when Docker is available).
- `GET /health` returns `200` with **no** LLM key present.
- No secret in the tree; no customer data anywhere.

Explicitly not here: chat, LLM calls, connectors, Supabase schema, deployment.

---

## Phase 1 — Technical Validation ✅

**Goal**: retire the unknowns that could invalidate the architecture, before building on
them. This phase produces findings, not features.

- ✅ Investigate **Didar24** — **answered: no authorized public API.** `didar24.com` is Dey
  Insurance's customer platform (not the `didar.me` CRM); it has a private, WAF-protected
  API host, no documentation and no key issuance. The insurer's partner programme (DayPI)
  is contract-gated and issuance-oriented. Decision recorded in
  `docs/decisions/ADR-0002-didar24-access-path.md`, evidence in
  `docs/analysis/PHASE1-DIDAR24.md`. The mock connector is therefore the primary data path.
- ✅ Create a **Gemini API key** manually (key authenticates) — but the project has **no free
  tier**: `403 PERMISSION_DENIED` until billing is enabled. Gemini's own numbers therefore
  remain unmeasured; the pipeline was validated on a free OpenRouter route instead.
- ✅ Test **function calling through LiteLLM** — 4/4 cases across two independent runs:
  tool proposed, executed, result fed back, final answer generated. Per-case latency,
  rounds and tokens recorded in `docs/analysis/PHASE1-LLM.md`.
- ✅ Validate **Persian question understanding** — correct tool for a direct and an informal
  count question; `policy_id` extracted from Persian prose; and the **no-tool control**
  (chit-chat) held, which is what separates understanding from "always call a tool".
- ✅ Prove the **mock `get_my_policy_count()`** path end to end — the spike's local executor
  returned synthetic counts (3 total / 2 active) and the answer was composed from them.
- ✅ Establish a **synthetic insurance dataset** — three fabricated policies inline in the
  spike; a proper fixture moves to Phase 3/4 with the connector.
- ✅ Record **actual quotas and limitations** — Gemini entitlement refusal (project-scoped,
  not IP), Gemini 3.x rejecting `temperature`, SDK retries masking errors, and OpenRouter
  free-pool `429`s. All in `docs/analysis/PHASE1-LLM.md` §4.

Carry-over into Phase 2: multi-turn context (the spike is single-turn), and a paid-tier
re-run against Gemini only if billing ever appears — the MVP default is now OpenRouter
(`ADR-0003`).

Exit criteria: a short findings document with measurements, a working prototype script,
and a yes/no answer on Didar24. Any architecture change this forces goes through an ADR.

---

## Phase 2 — Backend Foundation 🔄 (in progress)

- ✅ **Supabase dev project + schema**: `conversations`, `messages`, `tool_invocations`
  with indexes, an `updated_at` trigger, RLS on all three, grants to `authenticated`
  only. Verified live: `anon` is refused (`42501`) on every table, and the project's
  JWKS publishes ES256 keys.
- ✅ **Auth integration**: bearer token → `AuthContext` via JWKS (ES256/RS256 allowlist
  checked before decoding, required `exp`/`sub`/`iss`/`aud`, key cache refreshed on an
  unknown `kid`). Fails closed with `501` when unconfigured. 15 tests sign real tokens.
- ✅ **Persistence adapter**: `ConversationRepository` port (scope comes from
  `AuthContext`, never from arguments) with an in-memory adapter for tests and a
  PostgREST adapter that **forwards the caller's token**, so RLS applies. No
  `service_role` key is held anywhere.
- ✅ **LLM port + LiteLLM adapter**: provider-neutral error taxonomy, adapter-owned
  retries, sampling parameters optional; `FakeLLMAdapter` for scripted tests.
- ⬜ **Conversation orchestration**: load history, one LLM round, persist both turns.
- ⬜ **Error envelope + request-id middleware**: one consistent failure shape.
- ⬜ **Authenticated end-to-end test**: needs a real login, which arrives with the
  Phase 5 frontend (the negative paths are covered now).

Exit criteria: an authenticated request can post a message and get a model answer
persisted and replayed; tests run with no network and no real key.

---

## Phase 3 — Tool Registry and Execution ✅

- ✅ Registry: tool declaration (name, description, argument model, output model, required
  scopes), startup validation, allowlist. `app/application/tools/registry.py`.
- ✅ Argument validation and identity injection rules — arguments are Pydantic models with
  `extra="forbid"`, and identity is passed to the handler as `AuthContext`, never accepted
  as an argument. `app/application/tools/executor.py`.
- ✅ Execution: timeout, result validation, structured error taxonomy, audit record
  (`tool_invocations`, arguments stored as a SHA-256 hash).
- ✅ Orchestration loop: multiple tool rounds, round ceiling (3), stated partial answer.
- ✅ The four initial tools wired to the **mock** connector —
  `get_my_policy_count`, `get_my_policies`, `get_policy_details`, `get_policy_status`.

Exit criteria met: the negative cases from `SECURITY.md` §10 are covered by tests
(unknown tool, scope refusal, smuggled identity field, timeout, handler crash, foreign
policy id), and "چند بیمه‌نامه دارم؟" is answered from a tool result — verified against a
live model in Phase 1 and against the fake adapter in the suite (129 tests).

Open follow-up: the authenticated round-trip against real Supabase data needs a login
and lands with the frontend in Phase 5.

---

## Phase 4 — Data Connectors

- **Mock connector** promoted from a test double to a coherent demonstration source.
- **PostgreSQL connector**: read-only role, named repository functions, row limits.
- **Didar24 connector**: **blocked by design** — no authorized API exists (ADR-0002). The
  mock is the documented demonstration path; a real adapter requires a DayPI agreement
  first, so no code is written against an unpublished contract.
- **External REST connector**: reusable adapter with fixed endpoint allowlist.
- Standardized `ConnectorResult` and normalization tests per connector.

Exit criteria: at least two independent sources answer through the same orchestrator
without orchestration changes.

---

## Phase 5 — Frontend and End-to-End Integration

- Chat UI: message list, composer, loading/error states, RTL-correct Persian layout.
- Auth screens and token handling (memory + refresh, or httpOnly cookie — decided here).
- Conversation history list; resume a conversation.
- Tool-execution feedback ("checking your policies…") without leaking internals.
- Persian response presentation; number/date formatting via `Intl`.

Exit criteria: a user can log in, ask a Persian question, see a grounded answer, and
reload the conversation.

---

## Phase 6 — Testing, Deployment, Demonstration

- Unit and integration suites; end-to-end test of the primary journey.
- Security test pass (authn/authz negatives, log hygiene, bundle secret scan).
- Docker build validation for both images.
- Render deployment: web service (Docker) + static site; environment variables set.
- Public demonstration with **synthetic data only**, and a written statement of
  free-tier limits (cold start, ephemeral storage).

Exit criteria: a public URL answers the demonstration question reliably enough to show,
with no real customer data reachable.

---

## Future Phase — Knowledge Retrieval (RAG)

Only when there are real documents and a paid/self-hosted model path:

- document ingestion (chunking, metadata, provenance);
- embeddings and vector storage (pgvector first — it is already a Postgres);
- semantic + hybrid retrieval with citations;
- retrieval exposed as **tools**, so structured and document retrieval coexist in the
  same orchestration loop rather than replacing it.

Do not start this before Phase 6 exit criteria are met.

---

## Deferred / explicitly rejected (revisit only with an ADR)

Write operations against external systems · connector-management UI · dynamic connector
configuration · multi-agent orchestration · fine-tuning · Kubernetes · microservices ·
message queues.
