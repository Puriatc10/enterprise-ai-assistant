# Phase 1 — Didar24 Technical Validation

**Date:** 2026-11-14
**Question:** can the assistant retrieve a customer's insurance data from Didar24 through an
authorized API?
**Answer:** **Not today.** There is no public or self-serve API for customer policy data.
The only sanctioned route is a partner/contract API programme (DayPI), and even that is
described as issuance/inquiry for partners — not a consumer-scoped read API.

Consequence: the MVP uses the **mock connector** (as `ADR-0001` anticipated), and the
Didar24 adapter stays a Phase 4 item gated on a signed agreement.

---

## 1. Disambiguation first — "Didar" is two different products

This matters more than any endpoint detail, because building against the wrong one would
waste a phase.

| | `didar24.com` | `didar.me` |
|---|---|---|
| What it is | **منظومه دیدار** — the digital services platform of **بیمه دی** (Dey Insurance): policies, claims, medical centres, agent club | **Didar CRM** — a sales/CRM product (deals, activities, products) |
| Vendor | بیمه دی, built by "شرکت نو ره اندیشان تیسا" (per page metadata) | Didar Information Technology |
| Public API | none found | documented: `developers.didar.me`, `POST /api/...?apikey=...` |
| Fits our product? | **Yes** — the spec's questions are insurance questions ("how many policies do I have?") | No — it holds sales pipeline data, not insurance policies |

The project spec says "customer-specific **insurance** information", so `didar24.com` is
the intended source. `didar.me`'s API is real and well documented, but it is a different
product; it is mentioned here only so nobody integrates the wrong one later.

## 2. Method (and its limits)

- Public web sources: the platform itself, the insurer's digital-services site, search.
- The public front-end bundle of `didar24.com` (a static asset anyone's browser downloads)
  to identify the app's own API surface and feature set. **No authenticated page was
  opened, no account was used, no private endpoint was called.**
- Unauthenticated reachability probes of documentation paths (`/swagger`, `/openapi.json`,
  `/api-docs`, `/docs`, `/api/`) and of the `api.` host root.
- Browser instrumentation was attempted (CDP) but the host refused the browser's egress
  ("The URL you requested has been blocked") while plain `curl` from the same machine got
  `200`. That asymmetry is itself a finding (see §5).

## 3. What the platform actually is

- `GET https://didar24.com/` → `200`, 4,685-byte shell, `<title>منظومه دیدار</title>`,
  `<div id="app">` — a client-rendered **Vue 3 + Vite** SPA (`assets/index-DNunRKlB.js`,
  639 KB entry, 302 lazy-loaded chunks).
- Chunk names reveal the real feature surface: `carBodyInsurance`, `fireInsurance`,
  `familyInsurance`, `groupInsurance`, `individualInsurance`, `declarationDamage`,
  `medicalDamage-*`, `prescriptionNewRequest`, `medicalCenterAppointment`,
  `dependentInsureds`, `activePlaque`, `utilityBills*`, `access-management`, `login`.
- Third-party scripts in the entry bundle: `smart.didar24.com/smartcrm-loader` (a CRM
  widget), `bugsfree.ir`, `raychat`, `yektanet`, Google Maps/GTM. These are marketing and
  support integrations — **not** an API surface for us.
- `robots.txt` allows everything (`User-agent: *` / empty `Disallow:`), which is unusual for
  a login portal and says nothing about API access either way.

So: a full customer/agent portal. Real data, real value — and entirely behind login.

## 4. API surface: what exists, and what is closed to us

| Probe | Result | Reading |
|---|---|---|
| `didar24.com/swagger/index.html`, `/openapi.json`, `/api-docs`, `/docs`, `/api/` | no response (TLS handshake refused) | no public documentation in the obvious places |
| `api.didar24.com` DNS | resolves → `185.143.233.133`, `185.143.234.133` | a **dedicated API host exists** |
| `http://api.didar24.com/` | `200`, ArvanCloud, 6,177-byte interstitial: *"در حال انتقال به سایت مورد نظر هستید…"* with a **Tehran-timezone branch** | the API is fronted by a WAF/CDN that serves a geo-aware holding page instead of API responses |
| `https://api.didar24.com/` | fatal TLS alert (`SEC_E_ILLEGAL_MESSAGE`) | HTTPS to the API host is refused for this client |
| Repeated requests to `didar24.com` | after ~10 requests: TLS resets, then full refusal | active rate-limiting / bot protection |

Conclusions:

1. **An API exists** — the SPA talks to it, and the `api.` host resolves. It is simply not
   published, not documented, and not open.
2. **There is no public contract.** No developer portal, no OpenAPI document, no key
   issuance, no sandbox. Undocumented does not mean "just call it": without a contract
   there is no authorization, no schema stability, and no right to access another person's
   data with it.
3. **It is actively defended.** Geo-aware WAF behaviour plus request throttling means even
   an unauthorized integration would be brittle and adversarial — and our own
   `SECURITY.md` forbids exactly that.

## 5. The only sanctioned route: DayPI (partner API)

`dayins24.ir` — بیمه دی's digital-services/innovation site — advertises **DayPI**:

> «ارائهدهنده خدمات نوآوری بیمهای در بستر فناوری مانند وبسرویسها به شرکتها،
> اینشورتکها و سایر کسبوکارهای نوآورانه»

Published API families:

| Family | What the page says the APIs do |
|---|---|
| خودرو (شخص ثالث و بدنه) | استعلام، صدور، خسارت — i.e. **inquiry, issuance, claims** for partners and agents |
| بیمههای زندگی (مستمری پیشامد، پژواک) | online sale/issuance through partner platforms |
| شخص واحد (درمان انفرادی) | online issuance for platforms and apps |
| بیمه آتشسوزی | online issuance for platforms and apps |

Access mechanics visible on the page: a **"ثبت درخواست دریافت API"** request form, a
selection of which API families you want, and an "سامانه همکاران" (partner system) through
which orders are placed and policies issued.

What this means for us, without softening it:

- **It is a B2B programme, granted by the insurer, per partner.** Not self-serve, not per
  end user of our app.
- **Scope is issuance-centric.** "استعلام" (inquiry) exists for vehicle insurance — so
  policy lookup is plausible — but the page promises nothing about *consumer-scoped read*
  across all lines, which is exactly what "how many policies do I have?" needs.
- **A customer's identity would have to be established by us and trusted by them.** Our
  `AuthContext` says "this is user X"; their API would need a contractual basis to accept
  that, or we would be acting as an agent on the customer's behalf.

So: a viable path *if* the business side wants it — a partnership conversation, not an
engineering task.

## 6. Decision

1. **MVP uses the mock connector** over synthetic data (already designed as
   `DataConnectorPort`). No real data, no scraping, no reverse engineering.
2. **Do not build a Didar24 adapter in Phase 4** unless a written agreement exists first.
   Building it "ready to switch on" would mean writing to an unpublished, unstable,
   unauthorized contract — speculative work with a real security smell.
3. **Shape the mock after DayPI's vocabulary** (`policy`, `inquiry`, `claim`, `issuance`)
   so a future adapter maps onto it without redesigning the tool layer.
4. **Record it** in `ADR-0002`, so a future contributor does not "discover" the endpoint and
   integrate it informally.

## 7. What would change the answer

- A signed DayPI agreement that includes a **consumer-scoped policy list/inquiry** endpoint,
  with a clear statement of how a partner asserts the customer's identity.
- Or a different, documented data source (an insurer with a public customer API, or a
  synthetic dataset we own end to end for the demonstration).

## 8. Questions for the business side (not engineering)

1. Which insurance lines do the demo questions need — تکمیلی/درمان, خودرو, عمر?
2. Do we have, or can we get, a نمایندگی/همکاری relationship that would justify a DayPI
   request form?
3. If a customer authenticates in our app, what is our legal basis for reading their
   policies — delegation, partnership, or nothing yet?

## 9. Roadmap impact

Phase 1 item "investigate authorized Didar24 integration options": **done, with a negative
result**, which is a successful outcome — it prevents a phase of work against an
unavailable source. The mock connector is promoted from fallback to primary path for the
demonstration, and `docs/ROADMAP.md` Phase 4 is annotated accordingly.
