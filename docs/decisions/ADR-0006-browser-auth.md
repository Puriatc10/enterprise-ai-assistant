# ADR-0006 — Browser authentication and answer provenance

- **Status:** accepted
- **Date:** 2026-11-15
- **Context phase:** Phase 5 (frontend and end-to-end integration)

## Context

Until now every API call had to be made by hand with a token. The frontend had to take on
three jobs at once: obtain a verified session, send it on every request, and show the user
something honest about where an answer came from. Questions that shaped the design:

1. Where does the browser get a token, and what may it store?
2. What does the client do with the backend's error envelope?
3. How is "this answer was grounded in real data" shown without inventing anything?
4. What happens when the demo is run without Supabase configured?

## Decisions

### 1. The browser talks to Supabase Auth directly; the backend only verifies

`supabase-js` signs the user in and returns an ES256 access token. The frontend sends it as
`Authorization: Bearer …`; the backend verifies the signature against the project's JWKS
and derives identity from `sub`.

**Why:** the backend never sees or stores a password, and there is no session table to
keep in sync. Verification is asymmetric, so no shared secret exists to leak.

**Cost:** the frontend owns token refresh. A stale token produces a 401 mid-conversation,
so the UI must treat 401 as "session expired", not as "the request failed" (it does).

### 2. Only the anon (publishable) key ships to the browser

`VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` are inlined into the bundle at build
time. That is acceptable *because* they are public by design and row-level security is the
actual control. `service_role` is never referenced on the client, and the CI web job fails
the build if a `service_role` string appears in `dist/`.

**Why:** the alternative — proxying auth through the backend — creates a session endpoint
we would then have to secure, for no gain at this scale.

### 3. Missing configuration is a first-class screen, not a crash

If either Supabase variable is absent the app renders which variable names are missing
instead of throwing during module import. The backend behaves the same way (501 when
unconfigured), so a half-configured demo explains itself rather than looking broken.

**Why:** the project must stay runnable on a machine with no credentials; a white screen is
indistinguishable from a bug.

### 4. The error envelope is the only error contract

The client reads `{"error": {code, message, request_id}}` and surfaces `message` plus
`request_id`. It never parses provider text out of a 502 body, and never shows a raw
English API string in a Persian UI.

**Why:** support needs the request id; the user needs a sentence; neither needs a stack
trace. One envelope means one parsing path.

**Cost:** every new status needs a Persian mapping. Kept small on purpose: 401 (expired
session), 404, 429 (busy), 5xx (source failure), and network/timeout.

### 5. The turn reports which tools ran — names, statuses, durations only

`POST /conversations/{id}/messages` now returns `tools_used: [{tool, status, latency_ms}]`,
and the UI renders it as a quiet line under the answer.

**Why:** "grounded in retrieved data" is the product's whole claim, and it is otherwise
invisible. But the client does not need the arguments or the rows — showing those would
copy customer data into the browser for no benefit and put a second place to redact.

**Cost:** an extra field to keep honest. An answer produced without tools reports an empty
list rather than hiding the line, because "no tools" is a fact the user may act on.

### 6. Sending is optimistic, and a failure keeps the user's message

The user's message is appended before the request; a placeholder occupies the assistant
slot until the real answer replaces it. On failure the placeholder is removed, the user's
message stays, and the error is shown with its request id.

**Why:** a model turn can take ~30 s. Without an immediate echo the interface looks dead,
and on failure silently losing what someone typed is the worst possible response.

### 7. No streaming in this phase

The UI shows a thinking state instead of streaming tokens.

**Why:** streaming would require the tool loop to emit partial turns, which means deciding
what to do when a token stream is interrupted by a tool call. That is a real design problem
and not a UI task. Deferred, with the cost stated: perceived latency is worse than it could
be on a slow free-tier model.

## Consequences

- The end-to-end path (real login → verified token → scoped rows → grounded answer) becomes
  testable by a human in a browser for the first time.
- A browser session is required to exercise the authenticated API; automated end-to-end
  testing without one still relies on the fake adapter and the repository tests.
- `tools_used` creates a small coupling between the orchestrator's audit and the response
  model: both now derive from the same `ExecutionRecord`, so they cannot disagree.

## Provisional

- Persian copy is inline in components, not in an i18n catalogue. Correct for one locale;
  a second locale would require extraction.
- Conversation titles are still derived from the first message (Phase 2 decision) and are
  not editable.
- The sidebar loads conversations once, without pagination beyond the API's `limit`.
