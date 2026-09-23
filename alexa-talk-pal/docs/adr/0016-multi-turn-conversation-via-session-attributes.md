# 0016. Multi-turn conversation via Alexa `session.attributes`

- Status: accepted
- Date: 2026-09-23
- Deciders: solo (Luiz Wagner)
- Implements: [Plan 0005](../plans/0005-phase4-llm-conversational-intelligence.md)

> **Reconciliation update (2026-09-23):** this plan and Plan 0004 (pt-BR
> locale) were implemented in parallel on separate branches, each against
> the unmodified single-turn baseline, then merged by hand in
> `relay/app.py`. See
> [ADR 0017](0017-multi-locale-pt-br-support.md)'s mirror note for the
> full list of what changed at the merge — in short, `ask_llm` gained the
> `locale` parameter this ADR's §"Decision" below already anticipated
> (`ask_llm(query, locale=DEFAULT_LOCALE, conversation_history=None)`),
> and `CONVERSATIONAL_SYSTEM_PROMPT` became a per-locale
> `CONVERSATIONAL_SYSTEM_PROMPTS` dict (the bare name is kept as a
> back-compat en_US alias). The two critical design gaps this ADR
> resolves (§2.5 follow-up routing, §2.6 history-persistence discipline)
> and everything else below are unchanged by the merge.

## Context

The relay was single-turn only: every `AskAnythingIntent` request built a
fresh `[system, user]` message list, so a follow-up like "tell me more"
had no access to what was just discussed. `architecture.md` §10.3
(open question 3) already named the intended design: keep the last N
turns in Alexa's `session.attributes`, which ASK round-trips automatically,
so the relay itself stays stateless at the filesystem level (no SQLite,
no on-disk session store, no cleanup job on the netbook).

Plan 0005 worked through this in detail and flagged two gaps that would
have made the feature silently not work end-to-end even with correct
history-management code:

1. **Follow-up routing.** `AskAnythingIntent`'s sample utterances all
   require a carrier phrase plus the `query` slot. A bare follow-up
   ("and why", "tell me more") doesn't match that shape, so Alexa's NLU
   can route it to `AMAZON.FallbackIntent` instead -- which the relay
   didn't handle by name, so it fell into the generic error fallback
   before any multi-turn logic ever ran.
2. **History-persistence discipline.** Session attributes are not
   preserved automatically by the platform -- every response that doesn't
   end the session has to explicitly echo them back, or the conversation
   silently resets on the next turn. It's easy to wire up the happy path
   and miss the five-plus fallback/error/Help paths, which then look fine
   in casual testing right up until the first timeout or rate limit mid
   conversation quietly wipes the history.

## Decision

- **Store conversation history in `session.attributes.conversation_history`**
  as a flat list of `{"role", "content"}` messages (last 5 complete
  user/assistant turns, i.e. up to 10 messages). No `conversation_turn_count`
  or `conversation_started_at` fields -- both are either derivable
  (`len(history) // 2`) or unused by anything that reads history back, and
  an unused field in stored state is a latent bug waiting for someone to
  trust it.
- **New `conversation.py` module** owns all history logic as pure
  functions with no Flask/network dependency: `extract_conversation_history`
  (defensive -- never raises, skips malformed entries), `trim_conversation_history`
  (caps at 5 turns, logs a warning if it actually had to cut anything),
  `build_messages_with_history`, `append_to_history` (returns a new list,
  never mutates), `estimate_tokens` (rough 4-chars/token heuristic, for
  monitoring only), `should_clear_history`, `session_attributes_from_history`.
- **`ask_llm(query, conversation_history=None)`** now returns
  `(answer_text, updated_history)` instead of a bare string. It builds the
  full message list once (system prompt + trimmed history + current
  query) and dispatches to whichever backend (`ask_openrouter` /
  `ask_local_llm`) is configured, both of which now take a `messages` list
  instead of a bare `query` string -- `_call_chat_completions` no longer
  builds the message list itself. The signature is deliberately
  `(query, conversation_history=None)`, not a bare `**kwargs` or history
  folded into `query`, so Plan 0004's locale work can extend it to
  `ask_llm(query, locale, conversation_history=None)` without a second
  rewrite of every call site.
- **`alexa_response(speech_text, end_session=False, session_attributes=None)`**
  only includes `sessionAttributes` in the response body when the argument
  is not `None`. Every non-session-ending call site was updated to pass
  the current history explicitly -- there's no default that "does the
  right thing" by omission; see the table below.
- **New `CONVERSATIONAL_SYSTEM_PROMPT`** (plan §2.2, used verbatim) is now
  the only system prompt the live code path sends, even on a first turn
  with no history yet -- it works fine empty ("you have access to previous
  turns" is simply not exercised until there are any). The old
  `VOICE_SYSTEM_PROMPT` constant is kept in `app.py` as a single-turn
  reference point (ADR 0015 discusses it) but is no longer read by any
  live code.
- **`max_tokens` unchanged.** `OPENROUTER_MAX_TOKENS` and
  `LOCAL_LLM_MAX_TOKENS` both stay at `500` (confirmed as the actual
  current value in `app.py` before starting this work, matching ADR
  0015/ADR 0012-0013). An earlier draft of Plan 0005 proposed cutting
  these to 100/400 to "leave headroom" for history -- that's backwards:
  `max_tokens` is a shared budget between this model's *mandatory*
  reasoning and the spoken answer, not an output-only ceiling, and 500 is
  the value that reliably avoids `finish_reason: "length"` truncation.
  Multi-turn history grows the *prompt* (input) side of the request, which
  has no bearing on the output ceiling. History size is managed by
  trimming (5 turns), not by starving the output budget.

### Resolving gap #1 (follow-up routing)

Implemented **option 3** from plan §2.5: an explicit
`AMAZON.FallbackIntent` branch in `app.py`'s intent dispatch
(`_handle_fallback_intent`). `FallbackIntent` requests never carry the raw
utterance Alexa's NLU couldn't match, so there's no real query text to
forward. With no stored history, there's nothing to continue -- treated
the same as an `AskAnythingIntent` with an empty slot (`NO_QUERY_TEXT`).
With stored history, the relay sends a synthetic continuation query
(`FALLBACK_CONTINUATION_QUERY = "Please continue based on what we were
just discussing."`) through the same multi-turn LLM path, so the model
answers using the real prior context even though it never sees what the
user actually said this turn.

Options 1 (new sample utterances / a dedicated `FollowUpIntent`) and 2
(`Dialog.ElicitSlot`) were explicitly **not** attempted -- both require
Alexa Developer Console access and an interaction-model rebuild that a
coding agent in this environment cannot do or test. Real-Echo confirmation
that a natural follow-up phrase actually reaches this branch (plan §9,
§12 item 10) remains manual, hardware-dependent work.

### Resolving gap #2 (history-persistence discipline)

All non-session-ending response paths thread history through a single
shared helper, `_respond_with_llm(query, current_history)`, used by both
`_handle_ask_anything` and `_handle_fallback_intent` -- one implementation
of the quota-check/ask_llm/exception-handling/response-shaping flow, so
the echo-vs-append rule can't drift between the two call sites that need
it. Final behavior, matching plan §2.6's table exactly:

| Response | `end_session` | History |
|---|---|---|
| Successful LLM answer | `False` | new turn appended |
| `NO_QUERY_TEXT` (empty slot / no history on Fallback) | `False` | echoed unchanged |
| `RATE_LIMIT_FALLBACK` | `False` | echoed unchanged |
| `TIMEOUT_FALLBACK` | `False` | echoed unchanged |
| `GENERIC_ERROR_FALLBACK` (exception or empty answer) | `False` | echoed unchanged |
| `AMAZON.HelpIntent` | `False` | echoed unchanged |
| Unrecognized intent name | `False` | echoed unchanged |
| Unrecognized top-level request type | `False` | echoed unchanged |
| `QUOTA_EXHAUSTED_FALLBACK` | `True` | not needed, session ends |
| `AMAZON.StopIntent` / `CancelIntent` | `True` | not needed, session ends |
| `LaunchRequest` | `False` | **deliberately omitted** -- fresh conversation |

A canned fallback string is never appended to history as an assistant
turn -- `append_to_history` is only ever called inside `ask_llm`, on the
model's real (cleaned, post-`_strip_leaked_reasoning`) answer; every
error/fallback branch returns before that point and echoes the *input*
history instead.

The outermost last-resort exception handler in `/alexa` (wrapping request
parsing itself, not any specific intent branch) intentionally does **not**
attempt to echo history -- it's a genuinely last-resort safety net for
bugs in code that runs before history is even extracted, and reaching
further into already-exceptional state to compute an echo risks a second
exception. This one path is not in plan §2.6's table.

## Consequences

### Positive
- Multi-turn conversations now work through the code path, verified with
  unit tests on every table row above plus `conversation.py`'s pure
  functions (edge cases: malformed entries, over-cap trimming, empty/None
  history, non-mutation).
- Relay stays stateless at the filesystem level -- no new storage, no
  cleanup job, consistent with the original architecture.md decision.
- `ask_llm`'s signature leaves room for Plan 0004's `locale` parameter
  without another rewrite of `ask_openrouter`/`ask_local_llm`/every call
  site.
- `measure_latency.py` now measures the actual request shape the relay
  sends (messages list, not a bare query) and gained a `--multi-turn`
  replay mode and `--questions-file` flag, so the "5-turn history" latency
  question plan §2.3/§6 left explicitly unverified can actually be
  measured before trusting any number in that plan.

### Negative / trade-offs
- `AMAZON.FallbackIntent` handling (gap #1) is weaker than a real
  interaction-model fix: the relay has no idea what the user actually
  said, only that *something* didn't match. `FALLBACK_CONTINUATION_QUERY`
  is a generic stand-in, and gets stored in history as if the user said it
  verbatim -- a future turn's `conversation_history` will contain this
  synthetic line, not the real utterance.
- If history overshoots the 5-turn cap by one turn right after an append
  (12 messages briefly stored instead of 10), it's not re-trimmed until
  the *next* request's extract step. Documented behavior, not a bug --
  plan §2.4's lifecycle only specifies a trim step before building
  messages, not immediately after appending.
- Real-Echo verification that a natural follow-up phrase actually routes
  through `AMAZON.FallbackIntent` (rather than Alexa's own device-level
  "I didn't quite get that" behavior, which never reaches the skill at
  all) is unverified -- console-simulator/unit-test coverage only.

### Follow-ups
- Once Plan 0003's real-Echo testing runs, confirm at least one natural
  follow-up phrasing reaches `_handle_fallback_intent` and produces a
  contextual answer (plan §12 item 10).
- If `AMAZON.FallbackIntent` proves too weak in practice (users often ask
  something FallbackIntent can't meaningfully continue), revisit plan
  §2.5 options 1/2 (new sample utterances or `Dialog.ElicitSlot`) --  both
  need Developer Console interaction-model work this pass didn't touch.
- Re-run `measure_latency.py --multi-turn` against the live netbook/
  OpenRouter before trusting any latency number for 5-turn conversations
  (plan §2.3/§6 explicitly left this unverified).

## Alternatives considered

- **Server-side session storage (SQLite/Redis on the netbook)** — rejected
  per plan §11 alternative 1: adds disk I/O and cleanup logic on
  constrained hardware for no benefit over Alexa's native
  `session.attributes`, which already round-trips per-session state for
  free.
- **Lowering `max_tokens` to make room for history** — rejected; would
  reintroduce the exact truncation bug ADR 0015 just fixed, since
  `max_tokens` budgets output/reasoning, not input/history size.
- **New sample utterances / `Dialog.ElicitSlot` for follow-up routing**
  (plan §2.5 options 1/2) — not attempted; out of scope for this pass
  (requires Developer Console access and a rebuilt interaction model this
  environment can't exercise or test).
