# Demonstration script

Five minutes, one question, one answer that can be traced to a tool call. Everything below
has been run in this repository; nothing is staged.

## Before you start

1. **Warm the API** — `curl -s {API}/health` until it returns 200. On a free instance the
   first request after idle pays the cold start, and a slow first paint reads as broken.
2. **Have a confirmed user.** Supabase → Authentication → Users. If "Confirm email" is on,
   signup returns no session and the app stops at the login screen.
3. **Check the health payload**: `"llm": {"configured": true}` and
   `"policies_source": "mock-policies"`. If `configured` is false, the key is missing and
   every question will answer with a 503.

## The script

| Step | Do this | What to say |
|---|---|---|
| 1 | Open the static site. Point at the login screen. | "Authenticated product, Persian-first, RTL by default. No anonymous path." |
| 2 | Sign in. | "Supabase issued the token; the backend verifies it against the project's JWKS. No shared secret exists anywhere." |
| 3 | Ask **«چند بیمه‌نامه دارم؟»** | "This is a natural-language question about data the model has never seen." |
| 4 | While it answers | "Text streams as the model produces it — a free-tier model takes 5–30 s, so waiting silently was the worst part of the first version. Stop keeps whatever arrived." |
| 5 | Read the answer, then point at the chips under it | "`get_my_policy_count · موفق · ۱۲ms`. That number came from a tool result, not from the model. If the tool had failed, the answer would say so." |
| 6 | Ask **«چند تیکت باز دارم؟»** | "Different domain, different database engine — SQLite, opened read-only. Same orchestrator." |
| 7 | Ask **«چند بیمه‌نامه دارم و چند تیکت باز دارم؟»** | "One question, two sources, two tool calls in the same turn. This is the multi-source claim, not a slide." |
| 8 | Reload the page | "Conversations persist. Rename or delete them from the sidebar; the title is derived from your first message." |

## What is actually happening

```text
question → ChatService → LLM proposes a tool (routing accuracy measured at 92 %)
                       → executor: allowlist → scope → arguments → AuthContext identity
                       → connector: mock policies | SQLite support | Postgres CRM
                       → result back to the model → Persian answer streamed to the browser
                       → tools_used persisted; one audit row per call
```

The model never sees a database, a credential, or another customer's row. It sees eight
narrow read-only functions and returns a proposal.

## Questions that work well

- «چند بیمه‌نامه دارم؟» → count (3 total, 2 active)
- «بیمه‌نامه‌هایم را نشان بده» → list with type, status, dates
- «وضعیت بیمه‌نامه POL-XXXX-1 چیست؟» → status of one policy
- «چند تیکت باز دارم؟» → support count from the SQLite source
- «تیکت‌های پشتیبانی من را لیست کن» → tickets with status and priority
- «چه داده‌هایی می‌توانی ببینی؟» → the capability tool lists the enabled domains
- «چند بیمه‌نامه دارم و چند تیکت باز دارم؟» → two sources, one answer

Ask something the tools cannot answer — «قیمت دلار امروز چند است؟» — and the assistant
should say it does not have that data. That is the demonstration, not a failure: it is the
model declining to invent.

## If something goes wrong

| Symptom | Cause | Do |
|---|---|---|
| Login does nothing, "confirmation email sent" | Email confirmation is on | Confirm the user, or turn the setting off for this project |
| Answer says the service is busy (429) | Free model pool saturated | Wait ~30s and resend; this is mapped, not a crash |
| Long pause, then an answer | Free-tier latency, observed up to ~28s | Keep talking; the 45s timeout covers it |
| `llm.configured: false` | Key missing in the environment | Set `OPENROUTER_API_KEY` and redeploy |

## What not to claim

- Not production-ready: no rate limiting per user, no audit UI, no SLA.
- One data source is real-ish (PostgreSQL, CI-verified) but the demo path is synthetic
  (ADR-0002) — Didar24 has no authorized API.
- The free model can be slow and, in Persian, occasionally drops a foreign connective word.
  That is a known model-quality artefact, recorded in Phase 1.
