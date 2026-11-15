# Improvement Plan — Phases 7–9

> **Status:** awaiting approval. Nothing below has been implemented.
> **Scope:** turn a single-domain, single-source demonstration into a multi-source
> assistant, and give it an interface that looks and feels like a product.

**Why this document exists:** the MVP proves one thing well — the model proposes, the
backend authorizes, the answer is grounded. Everything around that core is thin: one data
domain (insurance), one real source, an interface built for correctness rather than
comfort, and no cost controls. This plan closes those gaps in three phases, each of which
ships something usable on its own.

Execution plans for each phase will be written in the task/step format of the
`writing-plans` skill when that phase is approved; this document decides *what* and *why*,
not every keystroke.

---

## 1. Where the project actually stands

Measured, not estimated:

| Area | Size | Notes |
|---|---|---|
| Backend domain | 536 lines / 10 files | models + ports, no framework imports |
| Backend application | 830 / 8 | orchestration, tool registry, executor, policy tools |
| Backend infrastructure | 1 367 / 14 | LiteLLM adapter, Supabase repo, mock + Postgres + REST connectors |
| Backend HTTP + core + security | 1 154 / 13 | routes, error envelope, config, JWT/JWKS |
| Backend tests | 2 858 / 18 | 172 passing, 5 PostgreSQL integration (CI only) |
| Frontend | 1 980 / 17 + 1 180 CSS | auth, API client, chat feature, design tokens |
| Frontend tests | 11 | error contract + send behaviour |
| Docs | 7 ADRs + architecture, security, roadmap, demo, deployment | consistent, current to Phase 6 |

**Capability inventory today:** four tools, one domain (`get_my_policy_count`,
`get_my_policies`, `get_policy_details`, `get_policy_status`), three connector
implementations of which only the mock is live (ADR-0002: Didar24 has no authorized API).

## 2. Findings, in the order I would fix them

| # | Finding | Severity | Evidence |
|---|---|---|---|
| 1 | **One domain is not an assistant, it is a demo.** Everything the model can do is insurance. | High | `application/tools/policies.py` is the only tool module |
| 2 | **No perceived latency.** A turn takes 5–30 s behind a spinner; there is no streaming. | High | `LLMPort.complete()` only; Phase 1 measured 28.1 s outliers |
| 3 | **The interface is functional, not professional.** System font stack, 1 180 lines of ad-hoc CSS, no message actions, no dark mode. | High | `web/src/styles.css`, `features/chat/chat.css` |
| 4 | **No cost control.** One user can exhaust the shared free pool; there is no per-user limit. | Medium | no limiter in `app/security/` |
| 5 | **Sidebar is a dead end.** No pagination, no rename, no delete; titles now exist but cannot be corrected. | Medium | `routes/chat.py` exposes list/create/get only |
| 6 | **Bundle is one 462 kB chunk** (132 kB gzip), mostly `supabase-js` loaded before first paint. | Medium | `dist/assets/index-*.js` |
| 7 | **A second real source exists but is never used.** The Postgres connector is CI-verified and not wired to anything a user can reach. | Medium | `POLICIES_SOURCE` selects it, but only for policies |
| 8 | **RLS is untested end-to-end.** Repository tests use the in-memory adapter; the real one needs a logged-in user. | Medium | `tests/` has no live Supabase test |
| 9 | **Session lives in `localStorage`.** Standard for SPAs, XSS-exposed; the alternative costs a backend session endpoint. | Low (decision) | `services/supabase.ts` |
| 10 | **Routing quality is unmeasured.** Four tools can be guessed; with nine the model needs an evaluation set, not hope. | Low now, high after Phase 7 | no routing eval exists |

## 3. Decisions I need from you

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | How the CRM source is reached | (a) direct PostgreSQL with a read-only role; (b) PostgREST with the user's token so RLS applies | **(a)** — it exercises the external-DB path you asked for and proves the connector. Caveat: a direct connection *bypasses RLS*, so `WHERE account_ref = $1` becomes the only control; it is enforced in SQL and tested. (b) is safer for Supabase-owned data but would not test the connector. Best of both: (a) now, and document (b) as the production default for app data. |
| D2 | Where CRM seed data gets its owner | (a) a script seeded with your Supabase user id; (b) auto-provision an account on first login | **(a)** — keeps the MVP read-only, as the security model promises. (b) means a write path into a data source, which the scope explicitly excludes. |
| D3 | Third domain for the SQLite source | (a) support tickets; (b) orders/invoices | **(a)** — different shape (aggregates + status transitions) and immediately legible in a Persian demo: «چند تیکت باز دارم؟» |
| D4 | UI styling approach | (a) keep plain CSS, add a token layer + component files; (b) rewrite in Tailwind | **(a)** — the CSS is already token-based and 1 180 lines is not a mess; Tailwind would be a rewrite for no user-visible gain. Add `react-markdown` + `rehype-sanitize` only where markdown rendering is genuinely needed. |
| D5 | How streaming reaches the browser | (a) SSE from FastAPI; (b) polling a turn status | **(a)** — one connection, no polling cost, and the browser's `ReadableStream` is the right primitive. (b) triples request volume for a worse experience. |
| D6 | Rate limiting store | (a) in-process per-user window; (b) Supabase-backed counter | **(a)** — correct for the single-instance deployment we actually have; (b) adds a round-trip per turn for a guarantee the platform does not yet need. Revisit at the second instance. |
| D7 | Do we keep the Didar24 mock? | keep as-is / remove | **keep** — it is the only insurance path, and the demo script depends on it; it also proves the connector interface is swappable. |
| D8 | Dark mode | yes / no | **yes**, via `prefers-color-scheme` and tokens; cost is ~40 lines of CSS variables given the token layer exists. |

## 4. Phase 7 — Multi-source capability

**Goal:** the assistant answers questions across three independent domains, from at least
two genuinely different source technologies, without a line of orchestration changing.

**Domain → source → tools**

| Domain | Source | Why this combination | Tools |
|---|---|---|---|
| Insurance | mock (unchanged) | Didar24 has no authorized API (ADR-0002) | the existing four |
| Support | **SQLite file**, read-only | zero credentials, works offline, proves the connector abstraction against a real database engine | `support_count_my_tickets`, `support_list_my_tickets`, `support_get_ticket` |
| CRM | **PostgreSQL on Supabase**, read-only role | a real server, real SQL, a schema and grants that must be right | `crm_list_my_deals`, `crm_summarize_my_pipeline`, `crm_get_account` |

Plus one capability tool that exists because the model should be able to answer "what can
you do?": `list_my_data_sources` → `[{id, label, domain, description}]`, scoped to what the
caller may actually reach.

**Work items**

1. **Compose registries, don't replace them.** `application/tools/registry.py` gains
   `ToolRegistry.compose(*registries) -> ToolRegistry`; each domain module
   (`tools/policies.py`, `tools/support.py`, `tools/crm.py`, `tools/overview.py`) owns its
   tool definitions. Startup builds one registry from the configured sources and logs
   `tools=N domains=3`. Duplicate names still fail at startup.
2. **SQLite connector.** `infrastructure/connectors/sqlite/support.py`: opens
   `file:{path}?mode=ro` (verified: writes raise `attempt to write a readonly database`),
   runs queries through `asyncio.to_thread`, sets a progress handler that aborts after
   ~4 s, and always applies `LIMIT`. Operations are named (`list_tickets`, `count_tickets`,
   `ticket_by_id`) — no SQL text crosses the port.
3. **Seeded data as SQL, not a binary.** `data/support_demo.sql` (committed, reviewable) +
   `scripts/seed_support_sqlite.py --out data/demo_support.sqlite3` (idempotent, writes a
   git-ignored file). Synthetic tickets for three subjects, Persian text.
4. **Shared Postgres base.** Extract the read-only transaction + statement timeout +
   row-limit logic out of `postgres/policies.py` into `postgres/base.py`, then
   `postgres/crm.py` supplies its own SQL map. If the extraction is not clean, stop and
   keep two files — the controls are the point, not the DRY-ness.
5. **CRM schema and grants.** `supabase/migrations/0002_crm_demo.sql`: schema `crm`,
   tables `accounts`, `deals`, `activities`, a `crm_reader` role with `SELECT` only, and
   seed rows. `scripts/seed_crm.py --subject <uuid>` binds the demo data to your user.
6. **Cross-source orchestration test.** A question needing two tools from two sources
   («چند بیمه‌نامه دارم و چند تیکت باز دارم؟») must produce one answer whose
   `tools_used` names both, and must not depend on which source answered first.
7. **Routing evaluation.** `scripts/route_eval.py` — 12 Persian questions with the
   expected tool, run against the live model like the Phase 1 spike, writing accuracy to
   `docs/analysis/PHASE7-ROUTING.md`. Not in CI (needs a key and is non-deterministic);
   run before any demo.

**Acceptance criteria**

- `GET /health` reports `tools=10 domains=3`, and the registry refuses a duplicate name.
- One question answered from two sources, with both in `tools_used`, in one turn.
- Every source is read-only *and* enforced at the source: SQLite `mode=ro`, Postgres
  `READ ONLY` transaction + `SELECT`-only role.
- A user with no CRM rows gets a clean «داده‌ای برای شما ثبت نشده است» answer, not an error.
- One source failing (SQLite file missing) degrades that domain only; insurance still answers.
- Routing accuracy ≥ 80 % on the 12-question set, recorded with the model id used.

## 5. Phase 8 — An interface that feels like a product

**Goal:** look and behave like a modern assistant product — calm, dense, Persian-first,
and never make the user wait without feedback.

**Design direction** (concrete, so it can be reviewed before it exists)

- **Type:** self-hosted **Vazirmatn** variable font, 15 px/1.75 body, tabular numerals for
  figures; monospace only for ids and tool names (LTR, isolated).
- **Colour:** neutral canvas, white surfaces, hairline borders, one accent for action.
  Dark mode through the same tokens under `prefers-color-scheme`.
- **Layout:** 260 px sidebar (drawer under 900 px), thread centred at 46 rem, sticky
  composer as a rounded field with the send control inside, messages 0.75 rem apart.
- **Motion:** 150–200 ms ease-out on enter only; `prefers-reduced-motion` disables it.
- **Messages:** assistant text with markdown (sanitised), user text in a tinted bubble,
  hover actions (copy, retry), a collapsible provenance line «۲ منبع بررسی شد».
- **Empty state:** greeting using the signed-in email's local part, four suggestion cards
  drawn from the tools that actually exist.
- **Composer:** Enter sends, Shift+Enter newline, stop button while streaming, character
  counter near the limit, focus never lost after send.

**Work items**

1. **Token layer.** `styles/tokens.css` extracts the nine existing variables, adds
   surface/surface-muted/text-muted/border-strong/accent-hover/shadow/duration, light and
   dark. `styles.css` keeps page-level rules only.
2. **Font.** `public/fonts/vazirmatn-var.woff2` (subset, `font-display: swap`), one
   `@font-face`, `--font-sans` updated. Check: first paint does not shift (sizes + fallback).
3. **App shell.** `components/AppShell.tsx` + `Sidebar.tsx` (sections: new chat, threads,
   account footer with sign-out), `Thread.tsx`, `MessageBubble.tsx`, `Composer.tsx`
   rewritten against tokens. Behaviour parity with the current `useChat` tests is the gate.
4. **Streaming, end to end.** `LLMPort.stream(request) -> AsyncIterator[StreamEvent]`;
   `LiteLLMAdapter.stream()` maps deltas; `ChatService.send_message(stream=True)` yields
   `token` / `tool` / `done` / `error` events and persists the final text exactly once;
   `POST /conversations/{id}/messages/stream` (SSE). The tool loop keeps its 3-round ceiling;
   a tool call mid-stream emits a `tool` event and the answer continues.
5. **Client streaming.** `streamMessage()` reads the SSE body, appends deltas to the
   reserved placeholder, renders a caret, and offers stop. On abort, the partial text is
   kept and labelled as interrupted rather than silently discarded.
6. **Markdown + actions.** `react-markdown` + `remark-gfm` + `rehype-sanitize` (no raw
   HTML, links rendered with `rel="noopener noreferrer"`); copy and retry actions; code
   blocks with a copy button.
7. **Accessibility pass.** Focus-visible rings, `aria-live` on the streaming message,
   keyboard-only walkthrough of login → ask → stop → copy, contrast ≥ 4.5:1 measured.

**Acceptance criteria**

- First token visible in < 2 s on a warm instance for a tool-using question.
- Stop mid-answer keeps the partial text and does not persist a duplicate on the server.
- Existing 11 client tests still pass; new tests cover streaming append, stop, and the
  interrupted-state label.
- Lighthouse accessibility ≥ 95 on the built site; keyboard walkthrough recorded in
  `docs/DEMO.md`.
- Sidebar: rename and delete work (depends on Phase 9 item 2 — sequenced below).

## 6. Phase 9 — Platform hardening

**Goal:** the thing can be left running in front of strangers.

| Item | Change | Acceptance |
|---|---|---|
| Per-user rate limit | `security/rate_limit.py`, sliding window keyed by subject, configurable (default 20 turns/hour), `429` + `Retry-After` | A 21st turn in an hour returns 429 with the header; the message is Persian; a concurrent test proves no double-count |
| Conversation management | `PATCH /conversations/{id}` (rename), `DELETE`, cursor pagination on list | Rename persists across reload; delete removes messages (cascade) and is 404 for others; 50 conversations page correctly |
| Bundle split | Lazy-load the chat feature; auth chunk separate | First-load JS ≤ 250 kB gzip; `npm run build` shows ≥ 2 chunks |
| Observability | Structured log per turn (request_id, tools, latency, tokens), `X-Response-Time` header | A turn's log line can be reconstructed from a `request_id` |
| Deployment | Apply `render.yaml`, set secrets, walk `docs/DEMO.md` on the public URL | `GET /health` green, one full journey in the browser, screenshots in the README |
| Live RLS test | One integration test against Supabase using a real token, skipped without `TEST_SUPABASE_*` | Passing in CI when the secret exists |

## 7. Sequence, effort, and why this order

| Phase | Sessions | Ships | Unblocks |
|---|---|---|---|
| 7 — Multi-source | ~3–4 | three domains, ten tools, routing eval | a demo worth showing |
| 8 — Interface | ~4–5 | Vazirmatn, new shell, streaming, markdown | the "professional" impression |
| 9 — Hardening | ~2–3 | limits, conversation management, split bundle, deploy | showing it publicly |

Phase 7 first because content beats chrome: a beautiful page that can only count insurance
policies is still a demo. Phase 8 before Phase 9 because streaming changes the client's
core loop, and adding it after pagination/rename means rewriting those screens twice.
Phase 9 last because its items are cheap once the UI is stable, and the deployment walk
should exercise the final interface, not a transitional one.

Within Phase 7, order is: SQLite (no credentials, fastest feedback on the abstraction) →
CRM (real server, schema, grants) → cross-source test and routing eval.

## 8. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Nine tools confuse the model; routing accuracy drops | Medium | namespacing by domain, descriptions that say *when* not just *what*, the 12-question eval, and a stricter fallback answer when no tool fits |
| Streaming interacts badly with the tool loop (partial text, then a tool call) | Medium | emit a `tool` event, keep deltas out of persistence until `done`; a test that asserts exactly one persisted assistant message |
| Direct Postgres bypasses RLS | Certain (by design) | read-only role, `WHERE` scoping in every statement, an integration test that proves another subject's row is invisible, and the caveat written in the ADR |
| SQLite blocks the event loop | Low | `to_thread` + progress-handler abort + `LIMIT`; measured 0.8 ms for 50 k rows |
| UI rewrite breaks behaviour the tests cover | Low | the 11 client tests are the gate; rewrite components behind them |
| Free model rate limits ruin a demo | Medium | streaming makes waits tolerable; 429 is already mapped; the eval records the model used |

## 9. Review Focus — what the current design will meet that no test covers yet

These are the five inputs most likely to bite, each of which gets a test in the task that
owns it:

1. A brand-new user whose CRM account has zero rows — the answer must say "nothing
   registered", not return an empty list the model then embellishes.
2. One source down while another answers — partial answers must name what failed.
3. A question that matches no domain («قیمت دلار امروز؟») — decline, don't invent.
4. Persian/Arabic digits and mixed-direction ids in tool arguments (`POL-۱۲۳`).
5. Two turns in flight in different conversations — the second must not overwrite the
   first's UI state (already guarded, must stay guarded through the rewrite).

## 10. Non-goals

Write operations to any source · an admin UI for connectors · multi-agent orchestration ·
vector search / RAG (still blocked on real documents) · Kubernetes · a second deployment
environment · fine-tuning. Each is either out of scope for the MVP or blocked by a
dependency outside this repository (ADR-0002).

---

## Approval checklist

Reply with the numbers you want changed; silence on a row means "take the recommendation".

- [ ] **D1** CRM via direct PostgreSQL + read-only role (recommended) vs PostgREST + RLS
- [ ] **D2** CRM seed binds to your Supabase user id via script (recommended) vs auto-provisioning
- [ ] **D3** Third domain = support tickets (recommended) vs orders/invoices
- [ ] **D4** Plain CSS + tokens (recommended) vs Tailwind rewrite
- [ ] **D5** SSE streaming (recommended) vs polling
- [ ] **D6** In-process rate limit (recommended) vs Supabase counter
- [ ] **D8** Dark mode: yes (recommended) / no
- [ ] Phase order 7 → 8 → 9 (recommended)
- [ ] Anything in §10 that you actually want in scope
