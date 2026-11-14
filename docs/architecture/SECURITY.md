# Security Model

Status: Phase 0. This document is normative: implementation phases must satisfy it, and
deviations require an ADR. Code lives in `apps/api/app/security/` and is enforced at the
tool executor and connector boundaries.

Threat model in one line: **the model is untrusted input, the browser is untrusted
input, and every external system is reached with the user's own authority and nothing
more.**

---

## 1. Authentication model

- **Identity provider**: Supabase Auth. It issues JWTs (access + refresh) after
  password/OAuth login.
- **Transport**: the browser sends `Authorization: Bearer <access_token>` on every API
  call. Tokens are held in memory or a secure store — never in a URL, never in
  `localStorage` if a httpOnly cookie option is available.
- **Verification (backend, mandatory)**:
  1. signature against Supabase's JWKS (cached, refreshed on unknown `kid`);
  2. `iss` and `aud` must match the project;
  3. `exp`/`nbf` with small clock skew;
  4. `sub` present and non-empty.
- **Derived context**: verification produces an immutable `AuthContext(subject,
  tenant_id, roles, scopes)`. Everything downstream uses *this object* — no handler,
  service, tool, or connector may accept a client-supplied `user_id`/`tenant_id`.
- **Failure modes**: missing/expired/invalid token → `401`; valid token, insufficient
  scope → `403`; both are logged with the reason and a request id, and never with the
  token value.
- **Phase 0 state**: the enforcement point exists and returns `501 Not Implemented`
  rather than allowing unauthenticated traffic through. It fails closed by design.

Why server-side verification at all, given Supabase RLS: RLS protects the database, not
the connectors. External APIs and read-only SQL sources have no knowledge of Supabase
row policies, so the backend must carry the authorization decision to them.

## 2. Tenant isolation

- Every persisted row that describes user activity carries `tenant_id` and `user_id`,
  both written from `AuthContext`.
- Every read is filtered by `tenant_id` (and `user_id` where the data is personal) at the
  repository layer — not in a router, not in a prompt.
- RLS policies in Supabase mirror these filters as defense in depth. Backend filtering is
  the primary control; RLS is the backstop.
- Cross-tenant access attempts are a security event: log, deny, and return `404`
  (not `403`) for object-by-id lookups so existence is not leaked.
- No connector call may run without an `AuthContext`; there is no "system" or "anonymous"
  mode outside migrations and tests.

## 3. User-level authorization

- Scopes are explicit strings (`chat:write`, `policies:read`, …) carried in the token and
  copied into `AuthContext.scopes`.
- Tool definitions declare the scopes they require. The executor checks the declared
  scopes *before* running anything.
- **Ownership is enforced twice**: the tool requires a scope, and the connector restricts
  the query to `AuthContext.subject`. A tool that takes an object id (e.g.
  `get_policy_details(policy_id)`) must confirm that object belongs to the caller and
  otherwise behave as if it does not exist.
- Roles never arrive from the model, from a request body, or from a header other than the
  verified token.

## 4. Connector security

Each connector is a security boundary and must:

1. use a **dedicated read-only credential** (Postgres: a role limited to `SELECT` on a
   named set of views; REST: a scoped API key), stored in environment variables;
2. expose an **allowlist** of operations — named repository functions or fixed endpoints,
   never a generic query or URL builder;
3. **re-derive scope** from `AuthContext` and validate that the resulting request cannot
   reach another user's data;
4. enforce a **timeout** and a **row/response limit** on every call;
5. **normalize** output to application schemas — vendor payloads and raw error bodies do
   not propagate (they leak internals and confuse the model);
6. map failures to a small taxonomy (`timeout`, `unauthorized`, `not_found`,
   `upstream_error`, `invalid_response`) so the model can explain a failure truthfully
   without exposing internals;
7. be **failure-isolated**: one connector's outage degrades one capability, and the
   assistant says so instead of guessing.

Forbidden in any connector: dynamic SQL from model input, string-concatenated queries,
`SELECT *` on wide tables, write/DDL statements, following arbitrary URLs supplied by the
model, scraping authenticated pages, or bypassing access controls. If Didar24 has no
authorized API, the mock connector over synthetic data is used — that is the policy, not
a workaround.

## 5. Secret management

| Secret | Where it lives | Never |
|---|---|---|
| `GEMINI_API_KEY` / `OPENROUTER_API_KEY` | backend env / host secret store | frontend bundle, logs, prompt, tool result |
| `SUPABASE_ANON_KEY` | can be public (by design) | — do not confuse it with a privileged key |
| Supabase **service_role** key | **not held by this project** — the backend forwards the caller's own JWT to PostgREST so RLS enforces scope; a bypass key would disable the backstop | anywhere |
| connector credentials | backend env, read-only roles | app database, git, client code |
| user tokens | client memory / secure cookie | URLs, logs, error messages |

Access tokens are verified against the project's JWKS (asymmetric ES256/RS256), so no
shared JWT signing secret is stored anywhere either.

Rules:

- `.env` is git-ignored; `.env.example` documents names only and contains no usable
  value. Committing a secret requires rotation, not just deletion.
- `VITE_*` variables are **public**; nothing sensitive may use that prefix.
- Logging is scrubbed by an explicit allowlist of fields, not by regex over free text.
  Authorization headers, cookies, and query strings are dropped at the logger boundary.
- Docker images contain no env files (`--exclude .env*` in `.dockerignore`).
- Rotating a key must be a config change plus restart; no code edit.

## 6. LLM data privacy

- **Free-tier constraint**: free-tier routes (Gemini's free tier, OpenRouter's `:free`
  models) may log, review, or use submitted content to improve their products.
  Therefore, until a paid or self-hosted configuration is approved, only **synthetic,
  non-sensitive** data may be sent: fabricated policies, fabricated names, no real PII,
  no real insurance records, no private documents, no confidential company information.
- **Prompt minimization**: send only what the answer needs. Tool results are projected to
  the fields the answer requires; identifiers are opaque; no whole-record dumps.
- The LLM never receives credentials, connection strings, tokens, internal hostnames, or
  stack traces.
- Tool results are data, not instructions. The orchestrator labels them as untrusted
  content so injected instructions inside retrieved text are not obeyed.
- Conversation history is persisted with the same access controls as any other
  user-owned data, and is deletable per conversation.
- Anything beyond synthetic data requires, in writing: an approved paid tier or
  self-hosted model, an approved provider data-processing agreement, and an updated
  version of this section.

## 7. Read-only data access

- MVP connectors are strictly read-only: no `INSERT`/`UPDATE`/`DELETE`, no DDL, no
  external write APIs.
- Postgres access uses a role whose grants are `SELECT` on specific views; the application
  database is a different credential entirely.
- No tool exposes SQL, HTTP, filesystem, or shell execution to the model.
- Read-only is enforced in the database/API credential itself as well as in code — code
  discipline alone is not a control.

## 8. Tool execution security

The executor is the single choke point. Every call passes, in order:

1. **Registry lookup** — unknown tool names are rejected (never "best effort").
2. **Schema validation** — Pydantic per-tool argument model, strict types, no extra
   fields. Invalid → structured error, no execution.
3. **Scope check** — tool's declared scopes ⊆ `AuthContext.scopes`.
4. **Identity injection** — user/tenant come from `AuthContext`; any model-supplied
   identity field is rejected as malformed input.
5. **Rate/limit check** — max calls per turn, max rounds per conversation turn.
6. **Execution with timeout** — every connector call is bounded; a timeout yields a
   `ToolResult(status=timeout)`, never a hung request.
7. **Output validation** — the result is re-validated against the tool's output schema
   before it goes to the model.
8. **Audit** — one record per invocation: conversation id, tool name, argument hash,
   status, latency, user id. Arguments are hashed, not stored verbatim, to avoid
   accidental PII retention.

Additional invariants: tool definitions are code (registered at startup), not runtime
configuration; the model cannot request a tool list it was not offered; errors returned
to the model are sanitized messages, not tracebacks.

## 9. Logging and sensitive-data handling

- **Log**: request id, route, status, latency, `session.sub` (pseudonymous id), tool name,
  tool status, connector name, error taxonomy code.
- **Never log**: tokens, authorization headers, cookies, API keys, connection strings,
  full tool arguments, full prompt/response bodies, raw connector payloads.
- Structured (JSON) logs in any deployed environment; human-readable in development.
- Prompt/response capture, if ever enabled for debugging, is opt-in, time-boxed, and
  limited to synthetic data.
- Audit records for tool invocations are retained per data policy and are queryable per
  conversation; they carry ids and hashes, not content.

## 10. Verification requirements

Security controls are only real if tested. Required negative tests before Phase 3 is
considered complete:

- request without a token → `401`; expired token → `401`; wrong `aud`/`iss` → `401`;
- valid token, missing scope → `403`;
- tool called with another user's object id → `404`, no data leaked;
- tool called with a model-supplied `user_id` → rejected as invalid input;
- unknown tool name → rejected, no execution;
- malformed/oversized arguments → rejected before connector call;
- connector timeout → `ToolResult(status=timeout)`, request still completes;
- tool round limit exceeded → partial answer with an explicit limitation;
- secrets absent from logs and from the built frontend bundle (grep the `dist/` output).

## 11. Open security questions (to resolve before public demonstration)

1. Does any Didar24 integration path exist that is authorized and read-only? (Phase 1)
2. A paid provider tier, or a self-hosted model, for anything beyond synthetic data?
3. Token storage strategy in the browser (memory + refresh vs httpOnly cookie) and the
   CORS/CSRF implications of each. (Phase 5)
4. Data retention window for conversations and tool audit records.
5. Whether Supabase RLS will be enabled from the first migration (recommended: yes).
