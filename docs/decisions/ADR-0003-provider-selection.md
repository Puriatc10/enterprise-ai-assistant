# ADR-0003 — MVP provider: OpenRouter free models, not Gemini

- **Status**: Accepted — supersedes `ADR-0001` Decision 5
- **Date**: 2026-11-14
- **Phase**: 1 — Technical Validation
- **Evidence**: `docs/analysis/PHASE1-LLM.md`

## Context

`ADR-0001` chose Gemini (`gemini/gemini-3.5-flash-lite` via LiteLLM) as the initial
candidate provider, on the assumption of a free tier plus adequate Persian and
function-calling performance. Phase 1 tested that assumption and it failed on a specific,
reproducible point:

```
POST .../models/gemini-3.5-flash-lite:generateContent → 403, ip=172.217.116.4, 1.70 s
{"status":"PERMISSION_DENIED",
 "message":"The Gemini API free tier is not available for this project…"}
```

- The key is valid (`API_KEY_INVALID` is a different error).
- The network is fine (IPv4 egress, 1.7 s) — so this is **not** an Iran geo-block.
- The restriction is **project-scoped**, which means a CI runner or a VPN would not help.
- Removing it requires a billing instrument.

Meanwhile an alternative was validated end to end: `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`
passed all four tool-calling cases twice, at $0, with 1.1–7.7 s latency.

## Decision

1. **The MVP's provider is OpenRouter**, configured as
   `LLM_PROVIDER=openrouter`, `LLM_MODEL=openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`,
   with `OPENROUTER_API_KEY` as its credential.
2. **Gemini is kept as an alternative**, not deleted: the field, the key and the documented
   verification stay, so switching back is a config change once billing exists.
3. **The LLM port keeps its shape.** No orchestration, domain model or tool code changes
   because of this — which is the provider-independence claim, now exercised for real
   rather than asserted.
4. **A fallback model id is mandatory** for any free route, because the pool is shared:
   `openrouter/inclusionai/ling-3.2-flash:free` or
   `openrouter/dots-studio/dots-3-note-preview:free`.
5. **Synthetic data only** remains in force. Free routes may log or train on traffic, so
   `SECURITY.md` §6 is unchanged in substance and now reads provider-neutrally.

## Consequences

- **Cost:** $0 for the MVP's validation and demonstration phase. No card, no billing
  account, no procurement.
- **Fragility accepted:** a shared free pool can return `429 upstream_provider_shared_pool`
  — observed on a model that had answered 30 s earlier. Mitigations: probe before running,
  keep fallbacks, map 429 to a retryable error with backoff in the adapter (Phase 2), and
  surface "busy, try again" in the UI rather than a stack trace.
- **Quality uncertainty accepted:** output quality follows whichever model the gateway
  routes to; one observed Persian answer contained a Russian connective («которых»).
  Prompt work and possibly a stronger model are needed before the Phase 5 demo.
- **Two hops instead of one:** requests pass through a gateway before the model provider.
  One more latency source and failure point, and one more party in the data path — which
  is precisely why the synthetic-data rule stays.
- **Gemini's measurements remain unknown.** If billing ever appears, re-run
  `scripts/phase1_spike.py --model gemini/gemini-3.5-flash-lite` and compare: the spike
  takes a flag, not an edit.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Enable Gemini billing | No payment path available for this project today; would also make the "free MVP" premise false. Not rejected on merit — parked until a card exists |
| Vertex AI (Gemini) | Same billing requirement, plus a GCP project and service-account plumbing |
| Local Ollama | Best privacy and no key at all, but a small local model is a weak proxy for Persian tool-calling quality, and the download is heavy. Kept as the privacy fallback if data policy ever tightens |
| Groq free tier | Fast and free, but a different model family with narrower choice; OpenRouter already exposes many models behind one key, so a later Groq move is a model string |
| Wait for Didar24/Phase 4 before any LLM decision | Blocks all orchestration learning on an unrelated unknown |

## Revisit when

A billing instrument exists (re-run the Gemini comparison), the demo needs guaranteed
latency (paid tier or self-hosted), or real data enters scope (paid/self-hosted, per
`SECURITY.md` §6). Supersede this ADR rather than editing it.
