# 0005. Single catch-all intent with an AMAZON.SearchQuery slot

- Status: accepted (treat as the primary expected friction point)
- Date: 2026-09-21
- Deciders: solo (Luiz Wagner)

## Context

To let Alexa forward an arbitrary spoken question, the interaction model
needs one free-form intent. `AMAZON.SearchQuery` is the only practical
free-form slot type ASK offers, and Amazon's console validator rejects a
sample utterance that is *only* the slot — it requires carrier words around
it.

The earlier prototype's `alexa-skill/interaction-model.json` independently
converged on this exact shape: a `TalkIntent` with an `AMAZON.SearchQuery`
slot and carrier-phrase samples (`"I want to say {utterance}"`,
`"talk about {utterance}"` — notably *without* a bare `"{utterance}"`
sample, consistent with it having been rejected). `../architecture.md` §4.1
independently arrived at the same pattern (`AskAnythingIntent`,
`"ask {query}"`, `"tell me {query}"`) and already flags it as "the single
most fiddly part of the console setup."

Two independent attempts landing on the same slot shape, and the current
plan already flagging it as the fiddliest step, makes this the strongest
candidate for what made the prior attempt "very difficult to configure" —
see [0003](0003-self-hosted-https-relay-vs-lambda-arn-endpoint.md) for the
other candidate (signature verification, which the prior attempt's Lambda
endpoint actually avoided).

## Decision

Keep the single catch-all `AskAnythingIntent` + `AMAZON.SearchQuery` slot
pattern — there's no better free-form alternative in ASK — but treat the
interaction-model step as the expected primary friction point this time,
budget real iteration time for it, and use
`../architecture.md`'s already-planned fallback (a two-turn flow: launch
asks "what would you like to ask?", the follow-up utterance is captured
without needing carrier words in the same turn) if the single-shot slot
keeps getting rejected or mis-parsed by ASR.

## Consequences

### Positive
- Reuses a pattern already known to be constructible (the prior prototype's
  JSON model exists and looks complete), so this isn't unexplored territory.
- The two-turn fallback is cheap to add and already scoped.

### Negative / trade-offs
- Still expect several rounds of "Save Model" / validator rejections before
  it builds cleanly.
- Bare-slot utterances are a dead end — don't spend time trying to make
  `"{query}"` alone work.

### Follow-ups
- If this turns out *not* to have been the prior blocker, revisit
  [0003](0003-self-hosted-https-relay-vs-lambda-arn-endpoint.md) as the
  more likely cause.

## Alternatives considered

- **Multiple fixed intents instead of free-form** — rejected, defeats the
  "ask anything" purpose of the skill.
- **Two-turn flow as the primary design, not just a fallback** — kept in
  reserve; single-shot is simpler UX if the validator cooperates.
