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

## Phase 4 — Data Connectors ✅

- ✅ **Mock connector** promoted from a test double to a coherent demonstration source —
  deterministic per-subject portfolio, so a foreign policy id genuinely resolves to
  `not_found` (`infrastructure/connectors/mock/policies.py`).
- ✅ **PostgreSQL connector**: named operations only, every query inside a `READ ONLY`
  transaction with a statement timeout, server-side `LIMIT`, identity bound from the
  verified context, rows projected to the public field set
  (`infrastructure/connectors/postgres/policies.py`).
- ✅ **Didar24 connector**: **blocked by design** — no authorized API exists (ADR-0002).
  No code is written against an unpublished contract; a DayPI agreement comes first.
- ✅ **External REST connector**: allowlisted operations only, no redirects, no caller
  URLs, streaming size cap, typed status mapping
  (`infrastructure/connectors/external_api/rest.py`).
- ✅ Standardized `ConnectorResult` and a shared projection in `domain/models/policies.py`,
  so no adapter can decide on its own what a model may see.

Exit criteria met: two independent sources (`mock`, `postgres`) answer through the same
registry, executor and `ChatService` without a line of orchestration changing —
`tests/test_connector_swap.py`. The PostgreSQL controls are proven against a real server
in CI (`tests/integration/test_postgres_live.py`, `postgres:16` service container),
including that the read-only role cannot write and that a `READ ONLY` transaction refuses
a write even for a privileged role.

**Known limit:** the available deployment target for the demo is Supabase, whose PostgREST
already covers the "structured data" case; the standalone PostgreSQL connector exists for
future on-premise sources and is exercised in CI rather than in the demo.

---

## Phase 5 — Frontend and End-to-End Integration ✅

- ✅ Chat UI: conversation sidebar, message thread, composer, loading/error/empty states,
  RTL-correct Persian layout (`apps/web/src/features/chat/`).
- ✅ Auth screens and token handling: `supabase-js` holds the session, refreshes it, and
  every API call carries `Authorization: Bearer …` (ADR-0006). No token is ever stored by
  application code; a 401 is treated as an expired session, not a failed message.
- ✅ Conversation history list; reopening a conversation reloads its messages.
- ✅ Tool-execution feedback: the turn returns `tools_used: [{tool, status, latency_ms}]`
  and the UI renders it under the answer — names and outcomes only, no arguments or rows.
- ✅ Persian response presentation; dates via `Intl` (`fa-IR`); tool names, model ids and
  request ids forced LTR inside the RTL layout.

Exit criteria partially met: a user can log in, ask a Persian question and see a grounded
answer with its tool provenance — **once the Supabase project has a confirmed user.** The
code path is verified (a real `POST /auth/v1/token` to the project returns the mapped
Persian error for bad credentials), but no confirmed test user exists yet, so the full
login → answer → reload journey has not been walked end to end.

---

## Phase 6 — Testing, Deployment, Demonstration ✅

- ✅ Unit and integration suites; **journey test** over HTTP: log in, ask, get a grounded
  answer with its tool provenance, reload the conversation, and be refused if it is not
  yours (`tests/test_journey.py`). Frontend unit tests with Vitest
  (`src/services/api.test.ts`, `src/features/chat/useChat.test.tsx`).
- ✅ Security pass: unknown tool, insufficient scope, smuggled identity field, timeout,
  handler crash, foreign policy id, cross-tenant conversation read (all 404, not 403 — no
  existence oracle), bundle credential scan (`scripts/check_bundle_secrets.py`).
- ✅ Docker build validation for both images, in CI, including booting the API with no
  credentials and asserting `/health`.
- ✅ Deployment prepared: `render.yaml` (Docker web service + static site), with
  `docs/development/DEPLOYMENT.md` and ADR-0007. **Not deployed** — no Render account was
  used from this environment, and deploying is the owner's decision.
- ✅ Demonstration script with pre-checks and failure modes (`docs/DEMO.md`); synthetic data
  only, with the free-tier limits written down rather than worked around.

Exit criteria **partially** met: everything is ready for a public URL, and the only thing
between this repository and a demonstration is applying the blueprint and a confirmed test
user. Until that URL exists, the claim "publicly demonstrable" is prepared, not proven.

---

## Phase 7 — Multi-source capability ✅

- ✅ Three domains composed into one registry at startup, with no change to `ChatService`,
  the executor or the routes: insurance (mock, ADR-0002), support tickets (**SQLite,
  read-only at the engine**), CRM (PostgreSQL, `SELECT`-only role) — ADR-0008.
- ✅ Eight tools: the original four, `support_count_my_tickets` / `support_list_my_tickets`
  / `support_get_ticket`, `crm_list_my_deals` / `crm_summarize_my_pipeline` /
  `crm_get_account`, plus `list_my_data_sources`.
- ✅ Cross-domain question answered in one turn with both tool calls visible
  (`tests/test_multi_source.py`).
- ✅ Seeded data as reviewable SQL: `data/support_demo.sql` + a seeder, and
  `supabase/migrations/0002_crm_demo.sql` + `scripts/seed_crm.py`.
- ✅ Routing **measured**: 11/12 = 92 % with 8 tools on the live model, miss recorded
  (`docs/analysis/PHASE7-ROUTING.md`).

Not verified: the CRM migration against the live Supabase project — it was exercised on a
throwaway `postgres:16` container, because no administrative connection exists here.

---

## Phase 8 — Interface and streaming ✅

- ✅ Token layer in light and dark, self-hosted **Vazirmatn**, tabular numerals, LTR
  isolation for ids and code (ADR-0009).
- ✅ Rebuilt shell: 260 px sidebar with a drawer under 900 px, centred 46 rem thread,
  rounded composer, message bubbles with hover actions, tool provenance chips.
- ✅ **SSE streaming** end to end: `token` / `tool` / `done` / `error` events, a stop that
  keeps partial text, retries only before the first delta, server answer authoritative.
- ✅ Sanitised markdown (`rehype-sanitize`, no raw HTML), copy and retry actions, an
  interrupted-answer label.
- ✅ Checked by rendering the real component tree at 1280 px and at 900/420 px in both
  themes: no horizontal overflow, composer and empty state aligned, 21 client tests green.

---

## Phase 9 — Hardening ✅ (deployment pending)

- ✅ Per-user sliding-window rate limit, `429` + `Retry-After`, checked before any provider
  work in both routes.
- ✅ Rename, delete and **keyset pagination** on conversations; another user's thread is a
  404 in both adapters (no existence oracle).
- ✅ Bundle split: initial JS 649 kB → **448 kB (128.63 kB gzip)** with a lazy 202 kB chat
  chunk.
- ✅ `X-Response-Time` plus one structured access log line carrying the request id.
- ⏳ **Not done:** applying `render.yaml` and the live Supabase RLS test. Both need the
  owner's Render account and a confirmed user; everything before that is prepared and
  documented in `docs/development/DEPLOYMENT.md`.

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
