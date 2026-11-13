# ADR-0001 — Initial Architecture

- **Status**: Accepted (with provisional items listed in §11)
- **Date**: 2026-11-14
- **Phase**: 0 — Bootstrap
- **Deciders**: Project owner, bootstrap engineer

## Context

We are starting an educational-but-real product: an authenticated web assistant that
answers natural-language questions (Persian first) about data held across several
organizational systems (a Didar24 insurance platform, PostgreSQL databases, third-party
REST APIs). The first version must be cheap to host, demonstrable in public, and built so
that later capability (more connectors, RAG, more providers) does not require a rewrite.

Constraints that shaped this ADR:

- one small team, part-time bandwidth;
- free or near-free hosting for the demonstration;
- unverified third-party integration (Didar24 may have no authorized API);
- hard requirement that the model never invents data or decides permissions;
- no real customer data until a paid/DPA-covered model path exists.

## Decision 1 — Modular monolith, not microservices

**Chosen**: one FastAPI deployable, internally modular, with enforced one-way
dependencies.

**Alternatives**: (a) microservices per connector; (b) a single flat application without
module boundaries; (c) serverless functions per endpoint.

**Reasoning**: the variable parts of this system are *external* (providers, connectors),
and those are already behind ports. Service boundaries would add network hops, deployment
units, and failure modes to solve a problem we do not have — team-scale parallelism.
Flat structure was rejected because it makes the "no provider types above the adapter"
rule unenforceable.

**Consequences**: extraction to services later is a deployment change, not a rewrite. In
exchange we must be disciplined about imports (enforced by review and, later, an import
linter).

## Decision 2 — Ports & Adapters (hexagonal)

**Chosen**: `domain/` defines protocols and models; `infrastructure/` implements them;
composition happens at the edge.

**Reasoning**: the two things most likely to change — the LLM provider and each data
source — must be replaceable without touching orchestration. It also makes testing
trivial: a fake adapter replaces Gemini and every connector in tests, with no network.

**Consequences**: slightly more indirection (a port + an adapter per concern). Accepted
deliberately; it is the smallest structure that satisfies provider independence.

## Decision 3 — FastAPI + React

**Chosen**: FastAPI (Python) backend, React + TypeScript + Vite frontend.

**Alternatives**: Django/DRF; Node backend sharing TypeScript with the frontend; Next.js
full-stack.

**Reasoning**: Pydantic is used three times over — HTTP schemas, tool-argument schemas,
and settings — which keeps one source of truth for validation. React/Vite gives a
static-buildable SPA with no server runtime to host. Next.js was rejected because SSR
adds a Node process and buys nothing for an authenticated chat app.

**Consequences**: two languages (mitigated by OpenAPI-derived client types in Phase 5);
FastAPI's DI is convention-based, so wiring discipline lives in the composition root.

## Decision 4 — LiteLLM Python SDK in-process, not the LiteLLM Proxy

**Chosen**: LiteLLM as a library inside FastAPI, behind our own `LLMPort`.

**Alternatives**: (a) self-hosted LiteLLM Proxy service; (b) the `google-genai` SDK
directly; (c) LangChain.

**Reasoning**: a proxy is another deployable, another auth surface, and another thing to
pay for, to gain routing and key management we do not need yet. Calling the vendor SDK
directly would put vendor types in the application layer. LangChain abstracts far more
than we need and is hard to keep out of the domain.

**Consequences**: our adapter owns retries/timeouts/cost visibility. If we later need
multi-provider routing, budgets, or a shared gateway for several services, the proxy
becomes justified and the port makes that swap local.

## Decision 5 — Gemini as the initial candidate provider

**Chosen**: `gemini/gemini-3.5-flash-lite` via LiteLLM, env-configurable.

**Verified at decision time (2026-11-14)**: the model id exists and is offered; LiteLLM
maps it as `gemini/gemini-3.5-flash-lite`; function calling is supported; paid pricing is
$0.30/$2.50 per 1M tokens with a 1M-token context; a free tier exists.

**Alternatives**: OpenAI (no meaningful free tier), Groq (limited model choice), local
Ollama/vLLM (best privacy, needs hardware).

**Reasoning**: free-tier access plus adequate Persian and function-calling performance is
exactly what an educational MVP needs. The model is configuration, not architecture.

**Provisional**: yes. Model availability, price, quota, and Persian quality must be
re-verified in Phase 1. Free-tier content terms forbid real PII — this, not the price, is
the binding constraint on going live with real data.

## Decision 6 — Supabase for application storage and auth

**Chosen**: Supabase (managed PostgreSQL + Auth).

**Alternatives**: own Postgres + Keycloak/Auth.js; Firebase; Clerk/Auth0 + Neon.

**Reasoning**: removes both a database and an auth service from our operational surface,
issues JWTs a FastAPI backend can verify without shared session state, and leaves a real
Postgres we control (raw SQL, RLS, and later pgvector).

**Consequences**: Python SDK support is thin — plan on HTTP/SQL against Supabase rather
than the client library; the service-role key is a privileged secret that must stay
server-side. Free projects pause when idle (acceptable for the demo).

## Decision 7 — Render for first hosting

**Chosen**: Render free Web Service (Docker) + Static Site.

**Alternatives**: Fly.io, Railway, Vercel (frontend only), a VPS.

**Reasoning**: Docker + static site on one platform, automatic TLS, deploy from repo, zero
cost. The known free-tier costs (cold start after idle, ephemeral filesystem, capped
CPU/RAM) do not affect correctness of a stateless API whose storage is Supabase.

**Consequences**: the stack must never depend on local disk; cold-start UX needs a
"waking up" state in the UI (Phase 5). Revisit if the demo needs to feel instant.

## Decision 8 — Read-only data access for the MVP

**Chosen**: all connectors read-only; no writes to external systems.

**Reasoning**: write access widens blast radius enormously (irreversible actions,
audit/consent requirements, prompt-injection consequences) and buys nothing for the
questions the product is meant to answer. Read-only also makes least-privilege
credentials feasible: a Postgres role with `SELECT` on a few views.

**Consequences**: no "update my address" features until a later phase designs
confirmation, idempotency, and audit properly.

## Decision 9 — Mock connector as the demonstration fallback

**Chosen**: a mock connector over synthetic data is a first-class implementation of the
connector port, used for development, tests, and the public demo if Didar24 access is
unavailable.

**Reasoning**: the product's value is the orchestration and authorization path, not any
single vendor. Waiting on an unverified integration would block every phase. Synthetic
data also satisfies the free-tier privacy constraint by construction.

**Consequences**: the demo must be honest that its data source is synthetic. Replacing
the mock with a real adapter is a composition-root change, by design.

## Decision 10 — Bounded tool-calling loop, no agent framework

**Chosen**: a hand-written orchestration loop with max rounds, schema validation,
scope checks, timeouts, and an audit record. No LangChain/LangGraph/CrewAI.

**Reasoning**: the loop is ~100 lines and it is the security-critical core; a framework
would hide exactly the part we must be able to reason about, and would put framework
types in the domain.

**Consequences**: we own the code (small, testable). If multi-agent orchestration is ever
justified, it becomes a new ADR, not a quiet dependency addition.

## Consequences of the whole

- Every later phase has a clear insertion point; nothing needs to be ripped out to add a
  provider, a connector, or RAG.
- The cost of this structure is indirection and import discipline — acceptable for a team
  that wants to demonstrate secure AI integration, not just a demo that answers.
- Nothing here is production-grade yet: no chat, no auth enforcement, no connectors, no
  deployment.

## Provisional items (explicit)

1. LLM model id, quota, and Persian quality — verify in Phase 1.
2. Didar24 integration path — may collapse to mock-only.
3. Browser token storage and CORS/CSRF posture — Phase 5.
4. Supabase schema and whether RLS is on from the first migration — Phase 2.
5. Hosting provider — revisit if free-tier cold starts undermine the demonstration.
6. Streaming vs request/response answers — decide in Phase 5 based on measured latency.

## Superseded by

Nothing yet.
