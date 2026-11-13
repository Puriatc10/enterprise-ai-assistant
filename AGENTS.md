# AGENTS.md

Instructions for AI coding agents working in this repository. Read this file first,
then read the documents it points to **before** substantial implementation.

This file is a rules sheet, not an architecture document. Do not duplicate doctrine
here; link to it.

---

## 1. Product context

**Enterprise AI Assistant** — an authenticated web assistant that answers
natural-language questions (Persian first) about data held in multiple organizational
systems. It answers by calling narrowly scoped, **read-only** tools and composing a
response from the returned data.

The LLM is a reasoning and language layer. It is **not** a data store and **not** an
authorization system.

## 2. Read first

| Before you… | Read |
|---|---|
| touch module layout or add a dependency | `docs/architecture/ARCHITECTURE.md`, `docs/architecture/TECH_STACK.md` |
| touch auth, tools, connectors, or logging | `docs/architecture/SECURITY.md` |
| start work from the roadmap | `docs/ROADMAP.md` |
| revisit a settled choice | `docs/decisions/ADR-0001-initial-architecture.md` |

If a task conflicts with those documents, stop and report the conflict instead of
silently diverging.

## 3. Architectural rules

1. **Modular monolith.** One deployable FastAPI app, internally modular. No
   microservices, no separate LLM gateway service, no message broker.
2. **Ports & Adapters.** `domain/` defines protocols and models and imports no
   framework. `infrastructure/` implements them. Dependencies point inward.
3. **No provider leakage.** Nothing above `infrastructure/llm/` may import `litellm`,
   `google.*`, or Supabase SDK types. Same for connectors: the orchestrator sees the
   connector port, never a vendor response shape.
4. **Dependency direction is one-way:** `api` → `application` → `domain`;
   `infrastructure` implements `domain` ports and is injected at the edge.
   If you need an import in the other direction, the abstraction is wrong.
5. **Layering discipline.** Routers validate and delegate; they hold no business
   logic. Orchestration does not do HTTP or SQL.
6. **Config from environment only.** All settings live in `app/core/config.py`
   (Pydantic Settings). No literal URLs, model ids, or credentials elsewhere.
7. **Add a directory when it has an occupant.** No speculative empty packages.

## 4. Security rules (non-negotiable)

1. **Identity comes from the verified token.** User id, tenant id, and roles are read
   from the authenticated context in `app/security/`. A model-supplied user or tenant
   field is a bug — reject it, do not "respect" it.
2. **The model never executes anything.** It proposes a tool call; the backend
   validates name, arguments, and authorization, then executes.
3. **No generic escape hatches.** Never add `execute_sql`, `fetch_url`, `run_shell`, or
   any tool whose arguments can widen its own scope. Tools are narrow, typed, and
   user-scoped (`get_my_policies()` — not `get_policies(user_id)`).
4. **Read-only against external systems.** SELECT and GET only. No writes, no DDL.
5. **Secrets stay server-side.** No key, token, or connection string in the frontend
   bundle, in logs, in prompts, or in tool results.
6. **Least privilege.** Use dedicated read-only connector credentials; never the
   application's own database role.
7. **Tool execution is bounded.** Registry-only names, schema-validated arguments,
   per-call timeout, max rounds per turn, and errors returned as structured facts —
   never as stack traces.
8. **Data privacy.** Free-tier Gemini may use submitted content. Only synthetic or
   non-sensitive data may be sent without an approved paid configuration.

Details and rationale: `docs/architecture/SECURITY.md`.

## 5. Coding conventions

**Python**
- Python 3.12+, full type hints on public functions; no untyped `dict` soup —
  use Pydantic models at boundaries.
- Async for I/O. Do not block the event loop with sync clients.
- Errors: raise specific exceptions in the domain, map to HTTP at the `api` layer with
  one consistent error envelope.
- Logging: structured, key-value; no PII, no secrets.
- Style: `ruff` (lint + format). No new lint suppressions without a comment saying why.

**TypeScript / React**
- Strict TypeScript; no `any` in new code.
- Components in `src/components`, feature code in `src/features`, API calls in
  `src/services`, shared types in `src/types`.
- The backend base URL comes from configuration (`VITE_API_BASE_URL`), never a literal.

## 6. Development workflow

1. Read the relevant docs (section 2).
2. State the plan for anything larger than a single-file change.
3. Implement the smallest coherent step; keep the app runnable at every commit.
4. Run the checks in section 7.
5. Update documentation in the same change if behavior or design changed.
6. Commit messages: Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`,
   `test:`, `chore:`). Never commit `.env` or secrets.

## 7. Testing expectations

- Every new behavior gets a test; bugs get a test that fails before the fix.
- Backend: `pytest`, fast and off-line by default. External providers are stubbed —
  no test may require a real API key or network.
- Security-relevant paths (auth, tool authorization, scope enforcement) require
  explicit negative tests: wrong user, wrong tenant, malformed arguments, unknown tool.
- Frontend: typecheck plus build must pass; component tests arrive with the chat feature.
- Do not claim a check passed unless you ran it and saw it pass; report the exact
  blocker otherwise.

## 8. Documentation requirements

- Architecture changes: update `ARCHITECTURE.md`; reversible decisions with real
  trade-offs: add an ADR under `docs/decisions/`.
- Stack changes: update `TECH_STACK.md` (role, alternatives, limitations).
- Security-relevant changes: update `SECURITY.md` in the same change.
- Keep the roadmap honest: mark phases by state, do not tick unfinished work.

## 9. Scope restrictions

Do not implement ahead of the roadmap phase in progress. Specifically, at Phase 0/1:

- no chat endpoint or LLM call beyond validation spikes;
- no real Didar24 integration, no scraping, no reverse engineering, no bypassing
  access controls;
- no unrestricted SQL or HTTP tool;
- no vector database, no RAG, no multi-agent framework, no Kubernetes;
- no customer data — synthetic data only;
- no deployment, no paying for services, no creating external accounts;
- no `git push` to a remote without explicit human authorization.

## 10. Definition of done

The app boots without secrets · tests pass · lint/format clean · docs consistent with
code · no secrets or PII in the diff · the change stays inside the current phase.
