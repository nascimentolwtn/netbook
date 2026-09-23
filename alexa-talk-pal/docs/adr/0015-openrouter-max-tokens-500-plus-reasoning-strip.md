# 0015. Raise `OPENROUTER_MAX_TOKENS` to 500, add a shared reasoning-leak guard

- Status: accepted
- Date: 2026-09-23
- Deciders: solo (Luiz Wagner)
- Resolves: napkin backlog item 1 (OpenRouter reasoning-leak bug found during Phase 2 console testing)

## Context

Plan 0002's console simulator test surfaced a real bug: asking "tell me why
the sky is blue" made the relay speak raw reasoning text instead of a clean
2-4 sentence answer. Routing was correct (proves the endpoint and
interaction model are fine); the problem was in `ask_openrouter`, which read
only `choices[0].message.content` with no reasoning-content handling.

Diagnosed live against the real endpoint (no `.env` read, only the
function's return value / non-secret response fields): `OPENROUTER_MODEL`
(`liquid/lfm-2.5-2.6b:free`, ADR 0011) has **mandatory** hybrid reasoning on
OpenRouter's free tier — the API rejects `reasoning: {enabled: false}` with
"Reasoning is mandatory for this endpoint and cannot be disabled." That
reasoning consistently burned ~148-150 of the hardcoded 150-token budget,
leaving `content` empty or `None` with `finish_reason: "length"`. This is
the exact bug ADR 0012 found and ADR 0013 fixed for the *local* llama.cpp
backend — now confirmed to affect the *default* OpenRouter path too, missed
until now because ADR 0011's model-selection testing measured only latency,
never answer content.

## Decision

- Raise `OPENROUTER_MAX_TOKENS` from a hardcoded `150` to an
  env-configurable default of `500` — same value ADR 0013 chose for
  `LOCAL_LLM_MAX_TOKENS`, on the same reasoning: the model needs headroom
  for reasoning *and* the spoken answer, not a higher ceiling it would
  otherwise use (latency stayed 0.8-1.3s in live testing, well under the
  ~8s Alexa budget).
- Add a shared `_extract_spoken_text` / `_strip_leaked_reasoning` helper,
  used by both `ask_openrouter` and `ask_local_llm` (they share
  `_call_chat_completions`), that strips an inline `<think>...</think>`
  block if one leaks into `content` — the distinct failure mode seen once on
  the local backend (2026-09-21 CHANGELOG entry), never reproduced against
  OpenRouter's cleanly-separated `reasoning`/`reasoning_details` fields
  (which this code never reads as spoken text, so no leak from there is
  possible). An unclosed block (reasoning cut off mid-thought by the token
  budget) is treated as empty rather than spoken as a fragment.
- `_extract_spoken_text` raises `ValueError` instead of crashing on
  `None.strip()` when a backend returns nothing usable, so the existing
  generic-error fallback handles it gracefully instead of a 500.
- Reinforced `VOICE_SYSTEM_PROMPT` with an explicit "never show your
  reasoning" line as cheap extra insurance — not relied on alone, since
  hybrid-reasoning models emit reasoning via the provider's own template
  before the visible answer, largely independent of system-prompt wording.

## Consequences

### Positive
- Fixes the bug found in Phase 2 testing: `finish_reason` goes from
  `"length"` to `"stop"`, clean answers confirmed live across multiple
  repro queries.
- One shared fix covers both backends' known reasoning-leak failure modes
  (budget-starvation and inline-tag-leak) instead of two separate patches.
- No server-side changes needed on the Windows PC or with OpenRouter.

### Negative / trade-offs
- `OPENROUTER_MAX_TOKENS=500` was tuned against live test queries during
  this fix, not a broad sweep — mirrors the same caveat ADR 0013 recorded
  for the local backend's `500`.
- The regex-based `<think>` strip is a defensive layer for a failure mode
  not currently reproduced on OpenRouter; if a future model/provider wraps
  reasoning in a different tag, this won't catch it.

### Follow-ups
- If a different `OPENROUTER_MODEL` is chosen later (napkin guardrail 9),
  re-check whether it also has mandatory reasoning and re-tune
  `OPENROUTER_MAX_TOKENS` accordingly — don't assume 500 is universal.

## Alternatives considered

- **`reasoning: {enabled: false}` / `exclude: true` request parameter** —
  rejected; confirmed live that this specific free-tier model's endpoint
  rejects the request outright rather than honoring it.
- **System-prompt suppression alone** — rejected as the primary fix, kept
  as a cheap secondary layer; unreliable against provider-templated
  reasoning that's emitted before the model reaches the visible answer.
