# ADR-0008 — Multi-source domains

- **Status:** accepted
- **Date:** 2026-11-15
- **Context phase:** Phase 7 (multi-source capability)

## Context

Phase 6 shipped one domain behind one connector. The plan for Phase 7 asked for three
domains on at least two genuinely different source technologies, without touching the
orchestrator. Questions that had to be answered:

1. How does a second domain get in without editing `ChatService`?
2. How is read-only *enforced* on SQLite, and on a direct PostgreSQL connection?
3. Where does a direct connection leave row-level security?
4. How does the model learn what it can reach?
5. How do we know routing still works with twice the tools?

## Decisions

### 1. A domain is a module that returns its own registry

`tools/policies.py`, `tools/support.py`, `tools/crm.py` each export
`build_*_registry(connector) -> ToolRegistry`; `core/dependencies.build_tool_registry`
composes them through `ToolRegistry.compose`, which merges via the constructor so a
name collision across domains still fails at startup.

**Why:** the orchestrator's contract is "a registry and an executor". Domains that arrive
as registries cannot change it, which is the whole modular-monolith claim made executable.

**Cost:** the tool list is per-request and now eight entries; prompt caching of the tool
block is defeated. Accepted until the registry is much larger.

### 2. Support tickets read a SQLite file opened read-only at the engine

`file:{path}?mode=ro` (verified: a write raises *attempt to write a readonly database*),
queries run through `asyncio.to_thread`, and a progress handler aborts a statement after
4 s. Every list carries a `LIMIT` bound from configuration.

**Why:** it proves the connector abstraction against a real engine with no credentials and
no server, so the domain works offline and in CI.

**Honest limitation:** a query already running in a thread cannot be cancelled. The
progress handler is what bounds it, and the docstring says so rather than implying a hard
timeout.

### 3. The CRM domain uses a direct PostgreSQL connection, and therefore enforces scope in SQL

`crm_reader` is a `SELECT`-only role; every statement filters `a.auth_subject = $1`; the
adapter additionally runs inside a `READ ONLY` transaction with a statement timeout.

**Why:** it exercises the external-database path for real — schema, grants, joins,
aggregates — instead of only through PostgREST.

**Cost, stated plainly: a direct connection bypasses RLS.** The `WHERE` clause is the only
control, so it is enforced in every statement and covered by tests that assert another
subject's rows are invisible. The migration also revokes `anon` and `authenticated` from
the `crm` schema, so the tables are not reachable through PostgREST at all — a
policy-less RLS would either lock the reader out or be theatre.

### 4. Capabilities are a tool, not a sentence in the system prompt

`list_my_data_sources` returns the enabled domains and their tool names, built from the
same descriptors that composed the registry.

**Why:** a prompt claim drifts the moment a domain is disabled, cannot be tested, and is
exactly the kind of thing a model embellishes. A tool answers from state.

### 5. Altitude changes are measured, not assumed

`scripts/route_eval.py` asks 12 fixed Persian questions (both domains, cross-domain, and
two that must be declined) and records which tool the model chose. First run:
**11/12 = 92 %** with 8 tools. Recorded in `docs/analysis/PHASE7-ROUTING.md`, including the
single miss (the model checked capabilities on an out-of-domain question instead of
declining outright) rather than re-scoring it after the fact.

**Why:** a routing error is a confident wrong answer, which is this product's worst
failure mode. Doubling the tool count without a number would be optimistic, not careful.

### 6. A configured-but-unreachable source still composes

If the SQLite file is missing, the support tools exist and answer with a typed
`upstream_error` naming the cause; the domain does not silently vanish from the list.

**Why:** a shorter tool list is indistinguishable from "this user has no support data",
and the model would then tell the user something false with confidence.

## Consequences

- Three domains, eight tools, one orchestrator; adding a fourth is a module plus a line in
  the composition root.
- The demo can show a cross-domain answer («چند بیمه‌نامه دارم و چند تیکت باز دارم؟») with
  both tool calls visible.
- `data/demo_support.sqlite3` is generated, git-ignored, and reproducible from the
  committed `support_demo.sql`.
- The CRM schema lives in Supabase but is **not verified against the live project** — the
  migration and seeder were exercised on a throwaway `postgres:16` container, because no
  administrative connection to the project exists in this environment.

## Provisional

- CRM seeding binds to one auth subject per run; a real deployment needs a
  subject→account mapping table, not an operator passing a uuid.
- The routing set is 12 questions on one model. It is a baseline, not a benchmark.
- `crm.activities` is seeded and unused — deliberately, until a tool that reads it is
  designed rather than improvised.
