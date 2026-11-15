# ADR-0009 — The interface, streaming, and what the client is allowed to render

- **Status:** accepted
- **Date:** 2026-11-16
- **Context phase:** Phase 8 (interface and streaming)

## Context

The first interface was built for correctness: it proved the loop worked and looked like a
form. Phase 8 had to make it feel like a product without weakening any of the guarantees
the earlier phases established, and the biggest change — streaming — alters the core
client loop.

## Decisions

### 1. Design tokens and one font, not a styling framework

`styles/tokens.css` holds the palette, spacing, radius, shadow, duration and font tokens in
light and dark; components consume them and own no colours. Persian is set in
**Vazirmatn** (self-hosted, variable, subset by script) at 15 px/1.75.

**Why:** the existing CSS was already token-based, and 1 180 lines of ad-hoc rules were
replaceable without a build-tool change. A utility framework would have rewritten every
component to buy nothing a token layer did not.

**Cost:** no component library, so interactive primitives (drawer, menu) are hand-built.
Accepted at this size; the accessibility basics they need are ~40 lines each.

### 2. Stream by default; keep the non-streaming endpoint

`POST /conversations/{id}/messages` still exists and still returns a whole answer. The UI
uses `…/messages/stream` (SSE), whose events are `token`, `tool`, `done`, `error`.

**Why:** the blocking endpoint is the honest API for scripts and tests, and removing it
would make the journey test depend on an event stream. Two paths, one service, shared
helpers — the security and persistence rules cannot drift between them.

**Cost:** two code paths to keep in step, mitigated by both calling the same
`_name_conversation`, `_record_audit` and repository methods.

### 3. Retries only before the first token

A streamed answer that fails mid-flight surfaces the error and stops; the adapter will not
retry once a delta has been emitted.

**Why:** retrying after the user has read half an answer duplicates text on screen. The
partial text stays, labelled «پاسخ ناقص ماند», instead of being silently replaced.

### 4. The server's `done.answer` is authoritative, not the concatenated tokens

The client renders tokens for responsiveness, then replaces the bubble with the answer the
server persisted.

**Why:** it guarantees the screen and the transcript agree. If a token were dropped in
transit, the client would otherwise show text the database does not have.

### 5. Markdown is rendered through a sanitiser, never raw HTML

`react-markdown` + `remark-gfm` + `rehype-sanitize` (default schema); `rehype-raw` is not
installed; links open with `rel="noopener noreferrer"`; code blocks are forced LTR and
isolated inside the RTL flow.

**Why:** model output is untrusted input. A renderer that accepts HTML turns a prompt
injection into script execution in the user's session.

### 6. The chat feature is a lazy chunk

`App.tsx` loads `ChatPage` with `React.lazy`; the login screen does not pay for the
markdown renderer.

**Why:** first paint is the one place a free-tier deployment cannot afford waste. Measured:
initial JS 649 kB → **448 kB (128.63 kB gzip)**, with a 202 kB chat chunk loaded on sign-in.

### 7. One action per control

Copy lives on the answer bubble (hover/focus). Retry — which needs the hook — sits in a
row above the composer for the newest answer. The duplicate copy control that the merge
produced was removed.

**Why:** two buttons that do the same thing make the second one a decoy, and this was
exactly the kind of overlap two parallel agents create.

## Consequences

- The interface looks like a modern assistant product: 260 px sidebar, centred 46 rem
  thread, rounded composer, real dark mode, Vazirmatn.
- Streaming makes a 5–30 s turn tolerable; stop keeps what arrived.
- The client bundle is split, so the auth screen stays light.
- Two visual states (light/dark) and three widths were checked by rendering the real
  component tree; the visual harness used for that was deleted before commit.

## Provisional

- No prose-level internationalisation: Persian copy is inline in components.
- The sidebar loads one page of conversations; "load more" uses the keyset cursor the API
  exposes but has no infinite scroll.
- Streaming has no resumability: a dropped connection ends the turn and the message is
  marked incomplete rather than retried automatically.
