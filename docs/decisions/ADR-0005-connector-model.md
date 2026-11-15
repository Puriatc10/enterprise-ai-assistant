# ADR-0005 — Data connector model

- **Status:** accepted
- **Date:** 2026-11-15
- **Context phase:** Phase 4 (data connectors)

## Context

Phase 3 gave the model four tools behind one executor. Those tools took their data from a
mock. Phase 4 had to add real sources without letting three of them diverge in shape,
authority or safety. The questions:

1. What is the minimum contract an adapter must satisfy?
2. How is read-only access *enforced*, rather than requested in a comment?
3. How does a caller's identity reach a source that has its own user model?
4. What stops an adapter from handing the model more data than a customer may see?
5. How does adding a source avoid touching the orchestrator?

## Decisions

### 1. One port, one result type, one refusal taxonomy

Every adapter implements `DataConnectorPort` (`health`, `fetch`) and returns
`ConnectorResult` with a status from `ConnectorStatus`. No adapter raises for an expected
failure, and none returns a raw SDK object.

**Why:** the tool layer maps connector status → tool status in one table
(`application/tools/policies.py`). A second taxonomy per adapter would mean the mapping
grows with each source and drifts.

### 2. Read-only is enforced twice, in different layers

*The adapter* wraps every query in `connection.transaction(readonly=True)` and sets
`SET LOCAL statement_timeout`. *The database* grants `SELECT` and nothing else to the
connector role.

**Why:** each control catches what the other misses. A misconfigured role still cannot
write through this adapter; a bug in the adapter still cannot write with a read-only role.
Both are asserted in `tests/integration/test_postgres_live.py` against a real server.

**Cost:** one extra round-trip per query for `SET LOCAL`; a read-only transaction blocks
`SELECT ... FOR UPDATE` and temp-table tricks, which is the intent.

### 3. Named operations, never SQL text

`FetchSpec.operation` selects a module-level constant. There is no `execute_sql`, no
template interpolation from `params`, and `policy_id` is bound as a parameter.

**Why:** the moment a tool can carry SQL, the model's only remaining barrier is prompt
text. Bounded operations are greppable, reviewable and testable.

**Cost:** a new question shape needs a new operation and a new tool. Accepted deliberately
— that is the review point where "should the model see this?" gets asked.

### 4. Identity comes from `AuthContext`; the caller cannot name a customer

`fetch(spec, context)` binds `context.subject` as `$1`. `params` carries only non-identity
values (a policy id). A policy belonging to another customer is indistinguishable from a
nonexistent one.

**Why:** no existence oracle, and no path from model output to identity.

**Production note:** the demo stores the auth subject directly in `customer_ref`. A real
deployment needs a subject → customer mapping table; that is a schema decision, not an
adapter one, and the adapter's contract does not change.

### 5. The projection lives in the domain, not in each adapter

`domain/models/policies.py` defines `PUBLIC_POLICY_FIELDS` and `project_policy`, and every
adapter calls it.

**Why:** an adapter that forgets to drop `national_id` would push a customer's national id
into a model prompt and possibly into a provider's logs. One allowlist means forgetting is
not possible — the field simply is not in the tuple.

**Cost:** an adapter cannot expose a source-specific field without a domain change. That
friction is the feature.

### 6. Adding a source is one function in the composition root

`build_policy_connector(settings)` chooses the adapter from `POLICIES_SOURCE`
(`mock` | `postgres`). The registry, executor and `ChatService` are untouched — that is
the exit criterion, asserted by `tests/test_connector_swap.py`.

**Why:** this is the modular-monolith promise made concrete.

**Deliberately not done:** a silent fallback from `postgres` to `mock` when the DSN is
missing. A wrong-source answer delivered confidently is worse than a failed start.

### 7. The REST connector is configuration-shaped, not request-shaped

Operations are fixed `GET`s declared as `operation → path`; the caller cannot supply a
URL, a method, or a redirect target (`follow_redirects=False`), and responses are capped
while streaming.

**Why:** a generic HTTP tool handed to a model is an SSRF primitive and an
exfiltration path. Fixed operations keep the reach identical to what was reviewed.

### 8. Supabase remains the app's store; the Postgres connector is for external sources

The connector targets *other* organizations' databases, never `public.*` in the app's own
Supabase project. The adapter's role is expected to see one dedicated schema or view set.

**Why:** mixing the app's persistence with a connector would let a read path intended for
domain data wander into conversation tables and tenant boundaries.

## Consequences

- Three adapters now satisfy one contract; the orchestrator cannot tell them apart.
- The mock stays the demo path (ADR-0002) because Didar24 has no authorized API; the
  Postgres adapter is proven in CI against `postgres:16` rather than in the demo.
- `asyncpg` is an optional extra (`[postgres]`), so the base install still boots with no
  driver and no credentials.
- The integration job is the only place the real DB path runs; it is skipped locally with
  an explicit reason rather than silently passing.

## Provisional

- Per-connector timeouts (5s statement, 8s tool) are guesses; real sources need measured
  budgets.
- The REST connector has no auth-refresh strategy — a token is passed once at construction.
  Rotating credentials will need a provider callback.
- Row limits are fixed at the connector; pagination is out of scope until a real dataset
  proves it necessary.
