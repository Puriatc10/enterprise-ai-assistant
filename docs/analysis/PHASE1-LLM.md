# Phase 1 — LLM / Tool-Calling Validation

**Date:** 2026-11-14
**Status:** ✅ **tool calling validated** on a free OpenRouter route — Persian question →
correct tool → grounded Persian answer, including the no-tool control. ⛔ Gemini itself
remains blocked at the account level (no free tier; billing required), so **the numbers
below describe the pipeline, not Gemini's own quality.**

---

## 1. What was built

| Artifact | Purpose |
|---|---|
| `apps/api/scripts/phase1_spike.py` | four synthetic Persian cases → tool call → tool result → final answer; per-case latency, token counts, JSON trace, `--model` flag |
| `.github/workflows/llm-spike.yml` | manual-dispatch live run using a repository secret; **no-ops** when the secret is absent |
| `apps/api/tests/test_phase1_spike.py` | seven tests pinning the executor contract (unknown tools and identity-shaped arguments rejected) |

Hermetic by construction: `litellm` is imported inside `main()`, a missing credential
prints a skip with exit `0`, and no test needs network.

## 2. The case set

| Case | Question | Expectation |
|---|---|---|
| `count-persian` | چند بیمه‌نامه دارم؟ | `get_my_policy_count` |
| `specific-policy-persian` | وضعیت بیمه‌نامه با شماره PL-100232 چیه؟ | `get_policy_status` **and** `policy_id` extracted from prose |
| `chit-chat-no-tool` | سلام! امروز حالت چطوره؟ | **no** tool call (the control — without it, a model that always calls tools scores 100%) |
| `count-informal-persian` | من کلا چند تا بیمه نامه فعال دارم؟ | `get_my_policy_count` |

Data: three fabricated policies (`PL-100231` درمان تکمیلی فعال, `PL-100232` شخص ثالث فعال,
`PL-100233` آتش‌سوزی منقضی). Synthetic only — no real customer data touched any provider.

## 3. Results

**Model actually used:** `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` via LiteLLM
(prefix `openrouter/`). **Not** the target model — see §5.

Run of 2026-11-14, second (repeat) run, with `litellm.num_retries = 0`:

| Case | Tool called | Rounds | Latency | in | out | Result |
|---|---|---|---|---|---|---|
| `count-persian` | `get_my_policy_count` | 2 | 3 695 ms | 531 | 51 | ✅ |
| `specific-policy-persian` | `get_policy_status` + `{"policy_id": "PL-100232"}` | 2 | 7 664 ms | 625 | 93 | ✅ |
| `chit-chat-no-tool` | *(none)* | 1 | 1 464 ms | 451 | 55 | ✅ control held |
| `count-informal-persian` | `get_my_policy_count` | 2 | 4 295 ms | 549 | 69 | ✅ |
| **total** | 3/3 expectations, 1/1 control | | | **2 156** | **268** | ✅ |

First run (independent, same configuration): 4 381 / 5 185 / 1 124 / 4 728 ms — also 4/4.
A third run, after the provider became the configured default (no `--model` flag):
4/4 again, with 1.6–28.1 s — the 28 s outlier was free-pool contention on one call, and it
is the reason the timeout default is 45 s rather than 30 s (at 30 s that call would have
been cut off). Three runs, no flakes.

Note on the artefact in §3: the Russian connective «которых» appeared again in the third
run, so it is **systematic for this model**, not a one-off glitch. It must be fixed by
prompting or a stronger model before the demo — see §7.

**Answers produced, verbatim:**

- «شما **۳** بیمه‌نامه دارید، از которых **۲** مورد **فعال** است.» — correct count (3 total,
  2 active) **but contains a Russian phrase («которых»)**. A multilingual code-switch
  artefact, not a data error. Recorded as a quality risk for the demo, not a blocker.
- «وضعیت بیمه‌نامه PL-100232: نوع: شخص ثالث، وضعیت: فعال، شرکت: بیمه دی، …» — correct on
  every field, Persian digits rendered.
- «سلام! ممنون، خوبم. چطور می‌تونم کمکتون کنم؟» — no tool call. Control passed.
- «شما **۲** بیمه‌نامه فعال دارید.» — correct, and it distinguished *active* (2) from
  *total* (3) without being told that distinction in the prompt.

**Cost:** $0 — free route. The earlier estimate of ≈$0.005 for the paid Gemini run stands,
but this run cost nothing.

## 4. Findings

### 4.1 Gemini: reachable network, non-entitled account

```
POST .../models/gemini-3.5-flash-lite:generateContent → http=403, ip=172.217.116.4, t=1.70s
{"code":403,"status":"PERMISSION_DENIED",
 "message":"The Gemini API free tier is not available for this project…"}
```

The key is valid (an invalid key returns `API_KEY_INVALID`), the network is fine (IPv4,
1.7 s), and the restriction is **project-scoped** — so a CI runner or a VPN would not have
helped. Vertex AI or billing remains the only route to the actual target model.

### 4.2 Gemini 3.x rejects `temperature`

`UnsupportedParamsError` from LiteLLM **before** any HTTP request (4–6 ms). Consequence
for the domain model: `LLMRequest.temperature: float = 0.0` must not be assumed portable.
**The orchestrator must not send sampling parameters it cannot guarantee**; the adapter
maps or drops them per family.

### 4.3 SDK retries turned a hard 403 into a 7-minute hang

A direct LiteLLM call surfaced the 403 as `BadRequestError` in **1.9 s**; with the SDK's
default retries the full spike exceeded a 420 s budget. With `litellm.num_retries = 0` the
same failure surfaced in **2.6 s**. Rule: the adapter owns retry policy — transport errors
and 429s only, capped.

### 4.4 A free route is not a guaranteed route

`google/gemma-4-31b-it:free` returned `429 — "temporarily rate-limited upstream,
limit_source: upstream_provider_shared_pool"` on the first full run, seconds after a
successful probe. Mitigation applied: probe the candidate list first, then run. Of the 15
free tool-capable models (filtered from 471), five answered and two refused (`429` shared
pool, `403` workspace-only). Free routes need a fallback list, not a single id.

Also observed: reasoning models spend the output budget on reasoning, so a 10-token cap
produced empty `content` with non-zero usage — a probe artefact worth knowing before
concluding "the model returned nothing".

### 4.5 Non-ASCII and the shell

The first raw probe sent `بگو سلام` from a shell and the model replied *"your message
consists of question marks"* — the **shell** mangled the UTF-8, not the API. Case text must
live in a UTF-8 file (as `CASES` does) or be passed through a file/Python, never inlined in
a Windows shell command.

## 5. What this does and does not prove

**Proven:** the LiteLLM tool loop works end to end; Persian intent selects the right tool;
a typed argument (`policy_id`) is extracted from Persian prose; the model declines to call
a tool when none is needed; the answer is composed from the tool result and nothing else;
latency (1.1–7.7 s) fits a chat UI with a progress state; provider switching is a flag, not
an edit.

**Not proven:** Gemini 3.5 Flash-Lite's own quality, latency or limits (blocked); production
rate limits (free routes are capped and shared); multi-turn conversation (single-turn only
here); Persian *rendering* quality — the code-switch artefact in §3 says prompt work and
possibly a stronger model are still needed before a public demo.

## 6. Adapter rules extracted (Phase 2 input)

1. Sampling parameters are optional and provider-mapped; never required by the port.
2. One error taxonomy in the adapter: auth, entitlement, rate, timeout, invalid-response.
3. The adapter owns retries (`num_retries = 0` at the boundary, explicit policy above it).
4. Provider is configuration: `--model`/`LLM_MODEL`, credential resolved by prefix.
5. Setup failures map to instructions ("enable billing or change provider", "run where the
   region is allowed"), never to provider JSON.
6. Model id and verification date live together in `TECH_STACK.md`; ids and tiers move.
7. Keep a fallback model id for any free route.

## 7. Open items

- [ ] Gemini entitlement: enable billing, or decide the demo runs on an alternative provider
      permanently (and amend `ADR-0001` Decision 5 accordingly).
- [ ] Re-run this exact case set against the real Gemini model once entitled, and compare.
- [ ] Persian prompt work: eliminate code-switching (explicit "respond only in Persian",
      possibly a stronger model) before the Phase 5 demo.
- [ ] Multi-turn context: the spike is single-turn; Phase 2 must test a follow-up turn.
