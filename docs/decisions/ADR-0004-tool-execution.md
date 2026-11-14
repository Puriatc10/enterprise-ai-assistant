# ADR-0004 — Tool execution model

- **Status:** accepted
- **Date:** 2026-11-15
- **Context phase:** Phase 3 (tool registry and execution)

## Context

The product's claim is that the assistant answers from real data without the model being
able to reach that data on its own. Phase 1 proved a model *will* propose plausible tool
calls; the risk is everything that happens after the proposal. Four questions had to be
answered before any connector was wired:

1. How does the model learn which tools exist?
2. What happens when a tool call is wrong, unauthorized, slow, or crashes?
3. Where does user identity come from?
4. How many rounds may a turn consume, and what is recorded?

## Decisions

### 1. The registry is the allowlist, and it is scope-filtered before the model sees it

Tools are declared once with name, description, argument model, optional output model,
required scopes and timeout. `specs_for(context)` returns only the tools the caller may
use; a tool the caller lacks the scope for is not offered at all.

**Why:** offering then refusing wastes a round and invites the model to rephrase. An
unknown tool name is refused outright — no fuzzy lookup, no "did you mean".

**Cost:** the tool list is per-request rather than static, so prompt caching of the tool
block is defeated. Acceptable at four tools; revisit before the registry grows.

### 2. Failures are returned as data, not exceptions

`ToolExecutor.execute()` always returns a `ToolResult` with a status from a fixed
taxonomy (`ok`, `invalid_request`, `forbidden`, `not_found`, `timeout`,
`upstream_error`, `invalid_response`). Handler *refusals* (`ToolRefusal`) carry the
status; unexpected exceptions become `upstream_error` and are logged with a traceback.

**Why:** the model must be able to say "that policy does not exist" or "the system did
not respond" in the user's language. A 500 would end the turn without an answer.

**Cost:** an unexpected handler bug is invisible to the client. Mitigated by logging and
by the audit row carrying the status.

### 3. Identity is an argument of the handler, never of the call

The handler signature is `handler(context: AuthContext, arguments: BaseModel)`. Argument
models use `extra="forbid"`, so `get_my_policy_count(user_id="someone")` is a validation
error. There is no code path where a model-supplied value reaches a data source as an
identity.

**Why:** this is the difference between a tool and a hole. `SECURITY.md` §8 states the
rule; this is where it is enforced.

### 4. A turn gets at most three tool rounds, then a stated partial answer

The loop stops at `DEFAULT_MAX_TOOL_ROUNDS = 3`. Hitting the ceiling produces
`PARTIAL_ANSWER_FALLBACK` — an explicit "I could not complete this" — rather than a
silent truncation.

**Why:** an unbounded loop against a metered provider is a cost bug and a latency bug;
a silent stop is a trust bug.

### 5. The audit row stores a hash of the arguments, not the arguments

`tool_invocations` records conversation, user, tenant, tool name, SHA-256 of the
canonicalised arguments, status and latency.

**Why:** the trail answers "what ran, how long, did it work" without becoming a second
copy of the data the tool looked at — which would extend the blast radius of a database
compromise and complicate deletion requests.

**Cost:** a repeat call is detectable but the query is not reconstructable. Accepted: the
conversation transcript already holds the user's own words.

### 6. Tool turns are not persisted as messages

Only user and assistant text turns are stored. Tool calls and results live in the audit
table and in the in-memory message list for the current turn.

**Why:** replaying an assistant `tool_calls` message requires its `tool_call_id`
partners. Persisting half of that pair — the tool result without the call — produces
history a provider rejects, and the schema does not store `tool_calls`.

**Revisit when:** the transcript must show the evidence behind an answer. That needs
`messages.tool_calls` persisted and a replay test, not a partial write.

## Consequences

- Adding a connector does not change the orchestrator: a connector implements
  `DataConnectorPort`, a tool wraps it, the registry gains one entry.
- Cross-user reads are not possible through tools, because nothing in the call can name
  a user.
- The mock connector's per-subject portfolio makes "another user's policy id" a genuine
  `not_found`, so ownership bugs surface in tests rather than in a demo.
- Scopes exist and are enforced, but no tool currently requires one: every authenticated
  user may read their own data. Roles that read *other* people's data will be the first
  to set `required_scopes`, and that is a product decision, not a code one.

## Provisional

- The three-round ceiling is a guess, not a measurement. It becomes tunable when real
  conversations exist.
- `PARTIAL_ANSWER_FALLBACK` is Persian-only, matching the current single-locale scope.
- Per-tool timeouts are 8s for the mock; real connectors will need their own budgets
  derived from measured latency.
