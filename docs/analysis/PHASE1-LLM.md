# Phase 1 — LLM / Tool-Calling Validation

**Date:** 2026-11-14
**Status:** ⛔ **blocked on provider access.** The key exists and authenticates; the
Google project has no free tier, and the API refuses to serve requests until billing is
enabled. Three findings below are already worth having; the Persian quality measurement
is still outstanding.

---

## 1. What was built

| Artifact | Purpose |
|---|---|
| `apps/api/scripts/phase1_spike.py` | four synthetic Persian cases → tool call → tool result → final answer, with per-case latency, token counts and a JSON trace |
| `.github/workflows/llm-spike.yml` | manual-dispatch live run using the `GEMINI_API_KEY` secret; **no-ops** when the secret is absent |
| `apps/api/tests/test_phase1_spike.py` | seven tests pinning the executor contract (unknown tools and identity-shaped arguments rejected) |

Hermetic by construction: `litellm` is imported only inside `main()`, so the test suite
and CI stay offline, and a missing key prints a skip with exit code `0`.

The case set is deliberately more than "does it call a tool":

| Case | Question | Expectation |
|---|---|---|
| `count-persian` | چند بیمه‌نامه دارم؟ | `get_my_policy_count` |
| `specific-policy-persian` | وضعیت بیمه‌نامه با شماره PL-100232 چیه؟ | `get_policy_status` **and** `policy_id` extracted from Persian text |
| `chit-chat-no-tool` | سلام! امروز حالت چطوره؟ | **no** tool call |
| `count-informal-persian` | من کلا چند تا بیمه نامه فعال دارم؟ | `get_my_policy_count` |

## 2. Findings (each verified, not assumed)

### 2.1 The Gemini free tier is not available for this project

Raw call to `generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent`
with the key in `x-goog-api-key`:

```
http=403  ip=172.217.116.4  t=1.70s
{"error":{"code":403,"status":"PERMISSION_DENIED",
 "message":"The Gemini API free tier is not available for this project. To use the
 Gemini API, please set up billing to upgrade to the paid tier…"}}
```

Reading:

- The key **is** valid — an invalid key returns `400 API_KEY_INVALID`, not `PERMISSION_DENIED`.
- The network is **fine**: IPv4 egress, 1.7 s, no geo block. The earlier expectation that
  Iran would return `User location is not supported` was wrong for this project; the block
  is at the *billing/tier* level, not the region level.
- Therefore running the spike on GitHub's US runners would **not** help — the restriction
  follows the project, not the IP. That was the plan when the key was added to the repo
  secret; it is now moot.

### 2.2 Gemini 3.x rejects `temperature`

```
UnsupportedParamsError: gemini does not support parameters: ['temperature'],
for model=gemini-3.5-flash-lite
```

Raised by LiteLLM **before** any HTTP request (observed at 4–6 ms). This is an
architectural note, not a bug: `domain/models/conversation.py` currently carries
`LLMRequest.temperature: float = 0.0`, which will fail on this model family. **Phase 2's
adapter must treat sampling parameters as optional and provider-mapped**, dropping them
when the provider does not support them — the orchestrator must not assume them.

### 2.3 LiteLLM's default retry policy turns a hard 403 into a multi-minute hang

The first full spike run exceeded a 420 s budget on four cases. A single direct call
through LiteLLM surfaced the same 403 as `BadRequestError` in **1.9 s**. With the SDK's
default retries enabled, a non-retryable `PERMISSION_DENIED` was retried with backoff
until the timeout budget ran out.

**Rule for the adapter:** own the retry policy. Set `num_retries = 0` at the boundary and
classify errors explicitly — auth/billing/validation errors fail immediately; only
transport errors and 429s are retried, with a cap. Now applied in the spike
(`litellm.num_retries = 0`), where the same failure surfaces in 2.6 s.

### 2.4 Setup failures should read as instructions

The spike now maps two predictable provider failures onto actionable text instead of a
wall of provider JSON: `billing`/`free tier` → "enable billing or change provider", and
`location is not supported` → "run on the runner". Cheap to write, and it is the
difference between a colleague fixing it in a minute and an hour.

## 3. Cost of the unblocked run (for the decision)

`gemini-3.5-flash-lite` at $0.30 input / $2.50 output per 1M tokens. One spike case is
roughly 2 000 input + 200 output tokens across two rounds, so **four cases ≈ $0.005**.
Billing risk is not a reason to hesitate; the account/payment method is the real question.

## 4. Open decision

Which provider route do we take for the validation run?

1. **Enable billing on this Google project** — keeps Gemini + LiteLLM exactly as designed;
   needs a payment method Google accepts.
2. **OpenRouter / Groq free tier** — no billing, `openrouter/<model>` through the same
   LiteLLM call; depends on being able to sign up from this network.
3. **Local Ollama** — zero external dependency and best privacy; a small local model is a
   weaker proxy for Gemini's Persian quality, and costs a model download.

The provider independence this project was built for is what makes all three a
configuration change rather than a rewrite — and finding this out *before* Phase 2 is
precisely what Phase 1 exists to do.

## 5. Still outstanding for Phase 1

- [ ] Run the four cases and record: chosen tool per case, argument extraction, latency,
      token usage.
- [ ] Judge Persian phrasing of the final answer.
- [ ] Record the real quota/limit observed on whichever route is chosen.
- [ ] Export the working values into `docs/architecture/TECH_STACK.md`.
