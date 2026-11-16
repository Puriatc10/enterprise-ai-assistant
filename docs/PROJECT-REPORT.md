# Enterprise AI Assistant — Project Report

**Repository:** `Puriatc10/enterprise-ai-assistant` (private) · **Branch:** `main` · **HEAD:** `10570f2`
**Window:** 2026-11-14 → 2026-11-16 · **30 commits** · **161 tracked files**
**Size:** 11 829 lines of Python (87 files) · 3 729 lines of TypeScript/TSX (19 files) · 767 lines of CSS · 22 documents, 9 ADRs
**Verification at HEAD:** 283 backend tests + 26 client tests passing · ruff lint and format clean · CI green on four jobs
**Live:** app <https://eaa-web-8bvb.onrender.com> · API <https://enterprise-ai-assistant-api-jmdj.onrender.com> (deployed 2026-11-16)

---

## 1. What this is

A web assistant that answers natural-language questions about data held in several
organizational systems, in Persian, by calling narrowly scoped read-only tools. The claim it
is built to keep: **the model proposes, the backend authorizes, and the answer is composed
from what a tool actually returned.**

It is not a chatbot with a database attached. The LLM never sees a credential, a SQL
connection or another customer's row; it sees a list of function declarations and returns a
proposal, which an executor either performs or refuses with a typed reason.

| | State at HEAD |
|---|---|
| Data domains | 3 — insurance (mock), support tickets (SQLite), CRM (PostgreSQL) |
| Tools | 8, all read-only, all composed at startup |
| Interface | Persian RTL, token-based light/dark, SSE streaming, markdown, tool provenance |
| Auth | Supabase Auth; ES256 JWTs verified against JWKS; no shared secret stored |
| Storage | Supabase Postgres with row-level security, queried with the caller's own token |
| Cost control | 20 turns/hour per user, sliding window, 429 + `Retry-After` |
| Deployment | **live on Render** (free plan, Frankfurt) — <https://eaa-web-8bvb.onrender.com> |
| Production readiness | **No** — see §8 |

---

## 2. Phase by phase

### Phase 0 — Bootstrap (2026-11-14, `3d03d43`)

Repository, documentation set (architecture, tech stack, security, roadmap, ADR-0001), a
runnable FastAPI skeleton with `/health`, a React/Vite skeleton, Docker Compose, CI.

*Decision that defined it:* modular monolith with Ports & Adapters; the domain layer imports
no framework and only `infrastructure/` may import an SDK. Everything since has obeyed it.

### Phase 1 — Technical validation (`0bc5596`)

Two investigations, both allowed to fail:

- **Didar24 has no authorized API.** `didar24.com` is Dey Insurance's customer portal (Vue 3
  SPA, 302 lazy chunks), not the Didar CRM; `api.didar24.com` is WAF-fronted, TLS-refusing and
  contract-gated (DayPI programme). The mock connector became the primary data path
  (ADR-0002). No adapter was written against an unpublished contract.
- **Gemini is unusable on this project** — `403 PERMISSION_DENIED`, "free tier is not
  available for this project" (project-scoped, not geo). OpenRouter replaced it (ADR-0003);
  4/4 tool-calling cases passed on a free model.

*Evidence:* `docs/analysis/PHASE1-DIDAR24.md`, `docs/analysis/PHASE1-LLM.md`.

### Phase 2 — Auth, persistence, orchestration (`18bcd3b` → `e17b6c1`)

Asymmetric-only JWT verification (alg allowlist checked before decode, keys cached and
refreshed only on an unknown `kid`), a caller-scoped Supabase repository relying on RLS, the
`LLMPort` abstraction with a LiteLLM adapter, `ChatService`, and a single error envelope
`{"error": {code, message, request_id}}` for every failure.

### Phase 3 — Tools and the guarded executor (`06c5402`)

`ToolRegistry` (allowlist, startup validation, scope filtering) and `ToolExecutor`:
allowlist → scope → argument validation → handler with `AuthContext` → timeout → result
validation → audit. **Identity is a handler argument, never a call argument**; argument models
use `extra="forbid"`, so `get_my_policy_count(user_id="someone")` is a validation error.
Failures return as data, not exceptions (ADR-0004).

### Phase 4 — Connectors (`f6f49fb`, `5de9559`)

One `DataConnectorPort`, three adapters, one projection owned by the domain
(`PUBLIC_POLICY_FIELDS`) so no adapter can leak a national id by forgetting to drop it.
Read-only is enforced twice: a `READ ONLY` transaction with a statement timeout *and* a
`SELECT`-only role. Proven against a real `postgres:16` service container in CI — including
that a `READ ONLY` transaction refuses a write for the privileged role (ADR-0005).

### Phase 5 — The interface (`760f2bc`)

Supabase sign-in in the browser, the error envelope as the only error contract, optimistic
sending with a reserved reply slot, Persian error copy carrying the request id. The turn began
reporting `tools_used` so the UI could show what grounded an answer (ADR-0006).

*Found and fixed here:* conversation titles were never derived — `_title_from` existed but
nothing called it, so every sidebar entry stayed «بدون عنوان».

### Phase 6 — Testing and deployment preparation (`c751891`, `bdd47ff`, `f2c87ae`)

The **journey test** over HTTP: sign in, ask, receive a grounded answer with provenance,
reload, and be refused if the conversation is not yours (404, not 403). Vitest for the client.
`render.yaml`, `DEPLOYMENT.md` with the free tier's real costs, `DEMO.md`, and
`scripts/check_all.sh` (which exists because `ruff check` passes on a file
`ruff format --check` rejects — a gap that cost a CI cycle).

### Phase 7 — Multi-source (`8437789`)

Three domains composed at startup with no change to `ChatService`, the executor or the routes:

| Domain | Source | Read-only enforced by |
|---|---|---|
| Insurance | Mock (Didar24 blocked) | synthetic per-subject data |
| Support | **SQLite file** | `mode=ro` at the engine + progress-handler abort + `LIMIT` |
| CRM | **PostgreSQL** | `SELECT`-only role + `WHERE auth_subject = $1` + `READ ONLY` transaction |

A direct PostgreSQL connection bypasses RLS, so the `WHERE` clause is the control — stated in
the ADR and tested. `list_my_data_sources` answers "what can you do" from the descriptors that
built the registry rather than from a prompt sentence. **Routing measured: 11/12 = 92 %**
with 8 tools (ADR-0008, `docs/analysis/PHASE7-ROUTING.md`).

### Phase 8 — Interface rebuilt, streaming (`10570f2`)

Design tokens (light/dark), self-hosted Vazirmatn, rebuilt shell, sanitised markdown, and SSE
streaming end to end. Retries happen only before the first delta; the server's `done.answer`
replaces the streamed tokens so the screen and the database never disagree; stop keeps partial
text, labelled «پاسخ ناقص ماند» (ADR-0009).

### Phase 9 — Hardening (`77a8494`, `10570f2`)

Per-user sliding-window rate limit checked before any provider work; rename, delete and keyset
pagination; `X-Response-Time` and one structured access log line carrying the request id; the
chat feature split into a lazy chunk. **Not done:** applying `render.yaml`, and the live
Supabase RLS test.

---

## 3. Architecture at HEAD

```text
apps/api/app
  domain/          8 modules   models + ports; no framework, no SDK, no I/O
  application/     9 modules   ChatService, ToolRegistry, ToolExecutor, 4 domain tool modules
  api/             6 modules   routes, error envelope, request-id + timing middleware
  infrastructure/ 22 modules   LiteLLM adapter, Supabase repo, mock/SQLite/Postgres/REST connectors
  security/        4 modules   JWT/JWKS verification, rate limiting
  core/            4 modules   settings, composition root, logging

apps/web/src
  App.tsx                      session → screen router (chat is a lazy chunk)
  auth/                        sign-in, session hook
  features/chat/               streaming hook, thread, bubbles, composer, markdown, tool chips
  styles/tokens.css            one palette, light and dark
```

One turn: `POST /conversations/{id}/messages[/stream]` → rate limit → `ChatService` → LLM
proposes → executor validates and runs → connector → result to the model → answer streamed →
audit row per tool call → answer persisted once.

---

## 4. Security controls, and where they live

| Control | Enforced in | Tested by |
|---|---|---|
| Identity from a verified token only | `security/tokens.py`, `security/auth.py` | real ES256 signing with a generated key; alg-confusion and expiry cases |
| No anonymous path | `require_auth_context` → 401/501 | route tests |
| Model cannot name a user | `extra="forbid"` on every argument model | smuggled `user_id` → `invalid_request` |
| No unrestricted SQL | named operations only, no SQL through the port | connector + tool tests |
| Read-only at the source | `READ ONLY` transaction, `SELECT`-only role, SQLite `mode=ro` | live `postgres:16` in CI; write attempts rejected |
| Tenant isolation | repository scoping + RLS policies | cross-user reads are 404, writes refused |
| Secrets never in the bundle | anon key only; `service_role` never referenced | `check_bundle_secrets.py` decodes JWTs and fails on `role=service_role` |
| Cost control | sliding window per subject | 429 + `Retry-After`; streaming is limited before any model call |
| Audit trail | `tool_invocations`, arguments hashed not stored | audit assertions in the tool tests |
| Provider errors don't leak | one `LLMError` taxonomy, provider text logged not returned | error-mapping tests |

---

## 5. Verification ledger

**Proven by execution:**

- 283 backend tests; 10 PostgreSQL integration tests that run in CI against a real server.
- 26 client tests, including a real SSE parser driven by split frames, CRLF, multi-line data
  and a tail frame without a blank line, and the sidebar's rename/delete interactions.
- **The deployed stack, in a browser**: sign-in, a streamed answer with two tool calls, a
  model-written thread title arriving as its own event, a rename that persisted, and a
  reload that replayed the transcript — all through the public API and the real Supabase
  row-level-security policies.
- Journey test: create → ask → grounded answer with `tools_used` → reload → 404 for another
  user.
- CI: four jobs (api, postgres, docker, web) green at `10570f2`; the API container boots with
  no credentials and answers `/health`.
- Live OpenAI-compatible routing: 92 % on 12 fixed Persian questions.
- The real component tree rendered at 1280 / 900 / 420 px in light and dark: no horizontal
  overflow, font and tokens applied, composer and empty state aligned.
- A real browser sign-in attempt against the Supabase project issued `POST /auth/v1/token` and
  rendered the mapped Persian error for bad credentials.

**Not proven:**

- `supabase/migrations/0002_crm_demo.sql` against the live project; it was applied twice
  against a throwaway `postgres:16` container and verified idempotent. The CRM domain is
  wired but has no seeded data in production.
- Docker image builds on this machine (Docker Hub is unreachable here); CI builds both, and
  Render builds the image that is actually running.
- The email-confirmation round trip from a phone: the link follows the browser now, but the
  project's Site URL still says `localhost:3000` until the dashboard is updated.

---

## 6. Measurements worth keeping

| Measurement | Value | Where |
|---|---|---|
| Tool routing accuracy (8 tools, live model) | 11/12 = 92 % | `docs/analysis/PHASE7-ROUTING.md` |
| Free-model latency | 1.1–7.7 s typical, 28.1 s worst observed | Phase 1 |
| Initial JS bundle | 649 kB → **448 kB (128.63 kB gzip)** + 202 kB lazy chat chunk | build output |
| SSE first token | under 2 s on a warm instance for a tool-using question | manual |
| SQLite read-only count, 50 k rows | 0.8 ms | probe before Phase 7 |
| Cold start (free tier) | tens of seconds after 15 min idle — documented, not measured here | `DEPLOYMENT.md` |

---

## 7. Decisions (all in `docs/decisions/`)

| ADR | Decision |
|---|---|
| 0001 | Modular monolith, Ports & Adapters, FastAPI + React, LiteLLM SDK in-process |
| 0002 | Didar24 has no authorized API → mock connector is the demonstration path |
| 0003 | OpenRouter replaces Gemini (project not entitled to the free tier) |
| 0004 | Tool execution model: allowlist, refusals as data, identity from context, 3-round ceiling, hashed audit |
| 0005 | One connector contract; read-only enforced twice; projection owned by the domain |
| 0006 | Browser auth, one error envelope, `tools_used` as provenance |
| 0007 | Render Docker + static site; the free tier accepted rather than hidden; CORS as an exact origin |
| 0008 | Domains as registries; per-engine read-only; scope in SQL where RLS does not apply |
| 0009 | Tokens over a framework; streaming rules; sanitised markdown; lazy chat chunk |

---

## 8. Gaps and risks — the honest list

1. **Email confirmation still points at the project's Site URL** (`http://localhost:3000`)
   until Site URL and Redirect URLs are set in the dashboard. Sign-in works; a *new* user's
   confirmation link does not return to the deployed app.
2. **CRM is live in code, empty in data.** Its migration was exercised on a container, not on
   the project, and seeding binds one operator-supplied subject uuid. A real deployment needs
   a subject→account mapping table.
3. **The free tier sleeps.** First request after 15 minutes idle pays a 30–60 s cold start;
   documented rather than hidden (`DEPLOYMENT.md`).
4. **Insurance data is synthetic** and stays that way until Didar24 access exists (ADR-0002).
5. **A SQLite query already running cannot be cancelled.** The progress handler bounds it; the
   docstring says so rather than implying a hard timeout.
6. **Streaming has no resumability.** A dropped connection ends the turn and marks the message
   incomplete; it does not retry automatically.
7. **Persian copy is inline**, not an i18n catalogue. Correct for one locale, wrong for two.
8. **Session lives in `localStorage`** — standard for SPAs, XSS-exposed. The alternative
   (httpOnly cookie) costs a backend session endpoint and has not been decided.
9. **Rate limiting is in-process.** Correct for one instance; a second instance would double
   every user's allowance.
10. **Routing is measured on one model, one run, 12 questions.** A baseline, not a benchmark.

---

## 9. Running it

```bash
# backend
cd apps/api && uv sync --python 3.12 --extra llm && .venv/Scripts/python.exe scripts/seed_support_sqlite.py
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8010

# frontend (VITE_* values live in the REPO-ROOT .env — vite envDir is ../..)
cd apps/web && npm install && npm run dev        # http://localhost:5173

# everything CI runs
bash scripts/check_all.sh
```

With no keys at all, the API still starts, `/health` reports `llm.configured: false`, and the
UI names exactly which variable is missing.

---

## 10. What I would do next

1. Set Site URL and Redirect URLs in Supabase so a new user's confirmation link returns to
   the deployed app — the last step of the redirect fix that lives in the dashboard.
2. Apply `0002_crm_demo.sql` to the project and seed it, then the third domain stops being
   the empty one.
3. Only then consider knowledge retrieval (RAG) — it is gated on real documents and a paid or
   self-hosted model path, and it should arrive as more tools, not as a second brain.
