# ADR-0002 — Didar24 integration: mock connector is the primary path

- **Status**: Accepted
- **Date**: 2026-11-14
- **Phase**: 1 — Technical Validation
- **Related**: ADR-0001 (connector fallback), `docs/analysis/PHASE1-DIDAR24.md`

## Context

Phase 1 required answering one question before any connector work: can we read
customer-specific insurance data from Didar24 through an **authorized** API?

Investigation (evidence in `docs/analysis/PHASE1-DIDAR24.md`) established:

- `didar24.com` is بیمه دی's (Dey Insurance) customer/agent platform — the correct target,
  as distinct from `didar.me`, an unrelated CRM with its own documented API.
- The platform is an authenticated SPA. A dedicated `api.didar24.com` host exists, is
  fronted by an ArvanCloud WAF (geo-aware holding page, HTTPS refused for our client), and
  the origin rate-limits after a handful of requests.
- No public documentation, developer portal, sandbox, or key issuance exists.
- The insurer **does** run a partner API programme (DayPI) covering vehicle inquiry/
  issuance/claims, life, individual treatment and fire — but access is granted per partner
  through a request form and a cooperation agreement ("سامانه همکاران"), and its published
  scope is issuance/inquiry for partners rather than consumer-scoped policy reads.

## Decision

1. The **mock connector** over synthetic data is the primary data path for the MVP and the
   public demonstration. It is not a stopgap to be replaced quietly; it is the sanctioned
   source until a contract exists.
2. **No Didar24 adapter is written** in Phase 4 without a signed agreement that includes a
   consumer-scoped read endpoint and a defined way for a partner to assert the customer's
   identity.
3. The mock's domain vocabulary follows DayPI's (`policy`, `inquiry`, `claim`, `issuance`)
   so a future adapter is a translation layer, not a redesign.
4. No scraping, no calling the undocumented private API, no bypassing the WAF — not as a
   "temporary" measure, and not for a demo. This is already a rule in `SECURITY.md`; this
   ADR records that the rule was tested against a real target and held.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Call the SPA's private API directly with a test account | Unauthorized use of an unpublished contract; violates `SECURITY.md` §4 and the platform's access controls; breaks on any release; high legal and ethical risk for an "educational" project |
| Browser automation of the customer portal | Scraping an authenticated page — explicitly out of scope in the project brief; same objections, plus fragility and PII exposure |
| Defer the whole project until DayPI access is granted | Blocks all learning value of phases 2–3, which are about orchestration, authorization and tool safety — none of which depend on the data source |
| Switch to `didar.me` because its API is documented | Wrong domain: CRM deals, not insurance policies. A counting question would be answered from the wrong data |

## Consequences

- The demonstration answers questions over **synthetic** data, and must say so on screen.
  That is also what makes the free-tier LLM constraint satisfiable (§6 of `SECURITY.md`).
- The connector port keeps earning its keep: the mock and a future DayPI adapter are
  interchangeable at the composition root.
- Phase 4 loses a task and gains a prerequisite: business/partnership work, not code.
- Any future contributor who finds `api.didar24.com` and is tempted should read this ADR
  first.

## Revisit when

A DayPI agreement (or another documented, authorized source of insurance data) exists. At
that point this ADR is superseded by a new one describing the real adapter's authorization
model — not amended quietly.
