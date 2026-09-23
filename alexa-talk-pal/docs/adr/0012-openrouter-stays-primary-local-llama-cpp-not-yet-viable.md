# 0012. OpenRouter stays primary; local llama.cpp backend not yet viable (reasoning-mode truncation)

- Status: accepted
- Date: 2026-09-21
- Deciders: solo (Luiz Wagner)

> **Update (2026-09-22):** the "not yet viable" finding below no longer
> holds — raising `max_tokens` for local-only calls fixed it cleanly, with
> no meaningful latency cost. Local is now wired in as an opt-in backend.
> "OpenRouter stays primary/default" is still the live decision, just for
> a different reason now (LAN dependency, not a correctness bug). See
> [ADR 0013](0013-configurable-local-backend-openrouter-stays-default.md).

## Context

Backlog item 4 (Phase 4 option) asked whether the relay's OpenRouter call
could be pointed at the local llama.cpp server on the Windows PC
(192.168.4.55:11434) instead, now that it has the same model
(`LFM2.5-2.6B-Q4_K_M.gguf`) loaded as the OpenRouter primary picked in ADR
0011 (`liquid/lfm-2.5-2.6b:free`). `app.py`'s `_call_openrouter` was
hardcoded to OpenRouter's URL and auth, so it was generalized into
`_call_chat_completions(base_url, model, query, api_key=None)` (generic
OpenAI-compatible caller; `_call_openrouter` is now a thin wrapper over
it), and `measure_latency.py` gained a `--base-url`/`--api-key` flag so the
same script/stats can target any backend.

Ran both, from the netbook (the real relay's network path) on 2026-09-21:

| Backend | min | avg | p90 | max | Notes |
|---|---|---|---|---|---|
| Local llama.cpp (LAN) | 0.96s | 1.15s | 1.30s | 1.30s | 10/10 OK, no rate limits possible (private server) |
| OpenRouter (same day) | 2.59s | 3.98s | 6.08s | 6.08s | 10/10 OK this run, but noticeably worse than ADR 0011's original measurement (p90 was 1.55-3.20s) -- confirms free-tier latency is genuinely variable run to run, not a one-off fluke |

Local is 3-5x faster and jitter-free. But while confirming answer shape,
found the local server has hybrid reasoning enabled by default: every
response includes a `reasoning_content` field alongside `content`, sharing
the relay's `max_tokens=150` budget. On a normal-length question
("Roughly how far is the Moon from Earth?"), reasoning consumed the whole
budget and the spoken answer was cut off mid-sentence
(`finish_reason: "length"`). Tried three standard per-request toggles to
disable it:
- `chat_template_kwargs: {"enable_thinking": false}` -- made it worse:
  inlined the raw `<think>...</think>` block directly into `content`,
  which would be read aloud verbatim on the Echo.
- `/no_think` appended to the system prompt -- reasoning still ran
  (shorter, but still present).
- `reasoning_effort: "none"` -- same, still ran.

None of these are a real fix. This is a server-launch config issue
(llama.cpp's `--reasoning-format` flag controls this, not a per-request
param) on the Windows PC side, and it directly violates
`architecture.md`'s "never enable reasoning/thinking modes" guidance.

## Decision

Keep OpenRouter (`liquid/lfm-2.5-2.6b:free` primary, `openrouter/free`
fallback, ADR 0011) as the only backend actually wired into `/alexa`. Do
not add a live backend-switch flag to `app.py` yet -- there's nothing safe
to switch to.

`_call_chat_completions` and `measure_latency.py --base-url` stay in the
repo as reusable tools: re-testing local after a server-side fix is a
one-line command, not new code.

## Consequences

### Positive
- `app.py` and `measure_latency.py` are now generic over backend --
  closes the "generalize for local backend too" part of backlog item 4
  without touching the production `/alexa` route's behavior at all
  (`_call_openrouter`'s signature/behavior is unchanged).
- Found a real correctness bug (truncated/garbled spoken answers) before
  it could reach a live Echo, not after.
- Local's raw speed/consistency advantage (3-5x lower p90, no rate-limit
  exposure at all) is now backed by real numbers, not a guess -- worth
  finishing once the blocker is cleared.

### Negative / trade-offs
- Backlog item 4 stays open, now narrower: fix the Windows PC llama.cpp
  server's reasoning-format at launch, re-run
  `measure_latency.py --base-url http://192.168.4.55:11434/v1/chat/completions LFM2.5-2.6B-Q4_K_M.gguf`,
  confirm `finish_reason` is never `"length"` on realistic queries, before
  reconsidering local as primary or fallback.
- OpenRouter's own latency now has two data points showing real run-to-run
  variance (0.54-0.98s min in ADR 0011 vs. 2.59s min here) -- still well
  under the 8s deadline both times, but worth keeping an eye on if it ever
  gets closer to the limit.

### Follow-ups
- Relaunch the Windows PC llama.cpp server with reasoning disabled
  (`--reasoning-format none` or equivalent for this build), then re-run
  the same `measure_latency.py --base-url ...` comparison before touching
  this decision again.
- **Tried and ruled out (2026-09-21)**: restarting the server without
  whatever launch flag was enabling the "thinking" split did not fix
  this -- it made it worse. `reasoning_content` disappeared, but the raw
  `<think>...</think>` block was inlined directly into `content` instead,
  still consuming the shared `max_tokens` budget (`finish_reason: "length"`
  on the same test query as before). A relay hitting this would read the
  think-block out loud to the user, on top of still truncating the actual
  answer.
- **Reverted back to reasoning-on, retested (2026-09-22)**: same repro
  query, `reasoning_content` correctly separated from `content` again --
  but the underlying bug is still there in this config too, and this run
  was worse than the original: `content` came back completely empty
  (`""`), not just truncated, with `finish_reason: "length"` and
  `reasoning_content` at 619 chars. So neither config tested so far
  (reasoning on, or the attempted reasoning-off restart) is safe. The fix
  needs an explicit `--reasoning-format none`-style flag (or
  prompt/template-level suppression that actually stops generation, not
  just re-routes where the tokens land, and not just toggling whichever
  flag was in place before) -- not yet tried.

## Alternatives considered

- **Strip `<think>...</think>` client-side in the relay and raise
  `max_tokens` to give reasoning room** -- rejected for now: burns latency
  budget on reasoning nobody hears, and depends on the model consistently
  emitting well-formed think-tags (the `enable_thinking: false` test above
  shows that's not guaranteed). Fixing it at the server-launch level is
  more robust and doesn't cost relay-side complexity.
- **Wire both backends now with a CLI/env flag, default to OpenRouter** --
  rejected: nothing to gain by wiring a switch to a backend that isn't
  safe to actually use yet; revisit once local is fixed.
