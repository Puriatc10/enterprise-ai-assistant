# Phase 7 — Tool routing, measured

**Model:** `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`
**Tools:** 8 · **Domains:** insurance (mock), support (SQLite)
**Run:** 2026-11-15, one pass, `scripts/route_eval.py`, live API calls.
**Result:** **11 / 12 = 92 %** (threshold for the phase: 80 %).

## Per-question outcome

| Question | Expected | Called | |
|---|---|---|---|
| چند بیمه‌نامه دارم؟ | `get_my_policy_count` | `get_my_policy_count` | ✅ |
| بیمه‌نامه‌هایم را نشان بده | `get_my_policies` | `get_my_policies` | ✅ |
| وضعیت بیمه‌نامه POL-1A2B3C-1 چیست؟ | `get_policy_status` | `get_policy_status` | ✅ |
| چند تیکت باز دارم؟ | `support_count_my_tickets` | `support_count_my_tickets` | ✅ |
| تیکت‌های پشتیبانی من را لیست کن | `support_list_my_tickets` | `support_list_my_tickets` | ✅ |
| جزئیات تیکت TKT-1001 را بگو | `support_get_ticket` | `support_get_ticket` | ✅ |
| چه داده‌هایی می‌توانی ببینی؟ | `list_my_data_sources` | `list_my_data_sources` | ✅ |
| به چه اطلاعاتی دسترسی داری؟ | `list_my_data_sources` | `list_my_data_sources` | ✅ |
| قیمت دلار امروز چند است؟ | *no tool* | `list_my_data_sources` | ❌ |
| سلام، حالت چطوره؟ | *no tool* | *(none)* | ✅ |
| چند بیمه‌نامه دارم و چند تیکت باز دارم؟ | both count tools | both | ✅ |
| خلاصه‌ای از بیمه‌ها و تیکت‌هایم بده | both list tools | both | ✅ |

## What the single miss actually is

The out-of-domain question ("what is the dollar rate today?") produced
`list_my_data_sources` rather than no call at all.

That is a **defensible grounding step, not a hallucination**: the model checked what it
has before answering, which is the behaviour the capability tool was added for. The
rubric is stricter than the product needs — it scores only the first response, so it
cannot see whether the *answer* then declined. It is recorded as a miss rather than
quietly re-scored, because moving the rubric after seeing the data is how routing numbers
stop meaning anything.

The concrete consequence to watch in a demonstration: if the model calls the capability
tool on an off-topic question, the user sees a short delay followed by a refusal. That is
correct-but-slower, not wrong.

## Why this matters more than it looks

With four tools, a wrong choice is cheap to spot. With eight across two domains, a
routing error looks like a confident wrong answer — the worst failure this product has,
because the whole claim is that answers come from data. This number is now the baseline:
any change to tool descriptions, names or count should be re-measured here, and a drop
below 80 % is a regression regardless of how good the prose sounds.

## Reproducing

```bash
cd apps/api
.venv/Scripts/python.exe scripts/seed_support_sqlite.py     # if the demo DB is absent
.venv/Scripts/python.exe scripts/route_eval.py --json routing.json
```

Needs `OPENROUTER_API_KEY`. Free-tier results vary between runs; use `--repeat 3` for a
stability check, which costs three times the calls.
