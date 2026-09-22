# 0013. `INFERENCE_BACKEND` switch: local llama.cpp available, OpenRouter stays default

- Status: accepted
- Date: 2026-09-22
- Deciders: solo (Luiz Wagner)

## Context

ADR 0012 found the local llama.cpp backend (Windows PC, 192.168.4.55:11434,
`LFM2.5-2.6B-Q4_K_M.gguf`) 3-5x faster than OpenRouter with no rate-limit
exposure, but blocked by a real bug: hybrid reasoning shares the relay's
`max_tokens=150` budget with the spoken answer, and reasoning alone could
consume the whole thing -- truncated or (once) completely empty `content`.
Two restart attempts were tried (reasoning on, reasoning-split disabled)
and both failed the same repro question at a meaningful rate (retested
2026-09-22: 3/7 clean, 3/7 truncated mid-sentence, 1/7 empty).

Rather than hunting for a `--reasoning-format none` launch flag or
swapping models, tried the cheaper fix ADR 0012 had actually rejected as
an alternative (on the untested assumption it would cost latency budget):
just give local calls a bigger `max_tokens`. Tested at 500 across 15
trials (5x the original repro question, then the full 10-query set) --
**15/15 clean**, all `finish_reason: "stop"`, latency barely moved (p90
~1.3s either way, since the model naturally stops around 100-240 tokens
on its own -- it only needed headroom, not a higher ceiling it would
actually use). The assumption behind rejecting this fix in ADR 0012 was
wrong in practice.

## Decision

Wire local as a switchable backend, not a replacement:

- `INFERENCE_BACKEND` env var, default `openrouter` (existing behavior,
  completely unchanged code path -- `ask_openrouter` untouched).
- `INFERENCE_BACKEND=local` routes to `ask_local_llm`, which calls
  `LOCAL_LLM_BASE_URL` (default `http://192.168.4.55:11434/v1/chat/completions`)
  / `LOCAL_LLM_MODEL` (default `LFM2.5-2.6B-Q4_K_M.gguf`) with
  `LOCAL_LLM_MAX_TOKENS=500` (default), vs. OpenRouter's
  `OPENROUTER_MAX_TOKENS=150`.
- No automatic fallback *between* the two backends -- `local` has no 429
  handling or secondary model of its own (a private server, not a shared
  free tier), it's a hard switch. `_call_chat_completions` now takes
  `max_tokens` as an overridable parameter instead of a hardcoded 150.
- Default stays OpenRouter. Explicit choice, not a technical limitation:
  local depends on the Windows PC being on and reachable over LAN, which
  isn't guaranteed the way OpenRouter's cloud endpoint is -- flipping the
  *default* is a bigger commitment than just making the option available.

## Consequences

### Positive
- Closes napkin backlog item 3 (Phase 4 option) -- the switch works,
  tested end-to-end (`ask_llm('Roughly how far is the Moon from Earth?')`
  with `INFERENCE_BACKEND=local` returned a full, clean, correctly-toned
  answer).
- Zero behavior change for the default path -- `ask_openrouter`'s code is
  byte-for-byte the same function it was before this ADR.
- The fix (`max_tokens=500` for local only) is cheaper and more robust
  than the alternatives ADR 0012 considered (model swap, client-side
  `<think>` stripping) -- no new dependency, no server reconfiguration
  needed on the Windows PC.

### Negative / trade-offs
- Local backend has no rate-limit/quota protection of its own -- the
  relay's existing `DAILY_CAP` guard still applies regardless of backend,
  which is the only thing standing between a runaway loop and hammering
  the Windows PC.
- Switching to `local` means the Alexa skill's availability now depends on
  the Windows PC being powered on and reachable -- not the case today
  (napkin: Windows PC isn't described as always-on). Not a blocker for
  keeping this as an opt-in dev/testing path, but a real reason it isn't
  the default.
- `max_tokens=500` was tuned against 15 trials on one question set, not a
  broad sweep -- if a real question needs more than ~500 tokens of
  reasoning+answer combined, the same truncation bug could reappear at a
  higher token count. Cheap to bump further if that happens.

### Follow-ups
- If the Windows PC becomes reliably always-on, revisit flipping the
  default (napkin backlog item 3 already flags this as a future decision,
  not resolved here).

## Alternatives considered

- **Make local the default, OpenRouter the fallback** -- declined by the
  user for now: local's availability depends on the Windows PC being on,
  which isn't guaranteed; not worth the risk for a household-facing skill
  until that's more certain.
- **Find the actual `--reasoning-format none` flag / swap models** --
  no longer necessary now that `max_tokens=500` resolves the bug cleanly
  with the currently-loaded model; kept as a note in ADR 0012 in case a
  future model change reintroduces the problem.
