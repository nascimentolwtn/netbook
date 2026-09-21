# 0009. Reuse the prior prototype's Alexa Skill ID, invocation name, and locale

- Status: accepted
- Date: 2026-09-21
- Deciders: solo (Luiz Wagner)

## Context

The prior prototype at `/mnt/e/dev/alexa-talk-pal` already created a skill
in the Alexa Developer Console: invocation name `"english talk pal"`,
en-US locale (`alexa-skill/interaction-model.json`), endpoint currently set
to an AWS Lambda ARN. Backlog item 2 required deciding a locale/invocation
name before building a new interaction model.

A Skill ID is not tied to its endpoint type or its interaction model — both
can be edited in place from the Alexa Developer Console without creating a
new skill or losing the ID.

## Decision

Reuse the existing Skill ID, invocation name (`"english talk pal"`), and
en-US locale rather than creating a new skill. This closes backlog item 2.

Two follow-on edits to the *same* skill are still required and tracked
separately (not new-skill work):
- **Endpoint**: switch from AWS Lambda ARN to HTTPS, per ADR 0003, once the
  relay + tunnel exist (backlog items 4–6).
- **Interaction model**: the existing model uses `TalkIntent` +
  `AMAZON.SearchQuery`; edit toward the shape decided in ADR 0005 (backlog
  item 7).

## Consequences

### Positive
- No new console skill-creation step; closes backlog item 2 immediately.
- Keeps a human-friendly invocation name already chosen and tested once.

### Negative / trade-offs
- Locale is fixed to en-US for now. (Not a hard wall — Alexa skills support
  multiple locales under one Skill ID, so pt-BR could be added later as an
  additional locale on this same skill if wanted; just not being done now.)
- The reused skill's endpoint (Lambda ARN) and interaction model
  (`TalkIntent`) don't match the new architecture yet — it is **not
  functional for testing** until backlog items 4–7 land. Reusing the ID
  does not mean the skill is ready today.

### Follow-ups
- Backlog item 6/7: switch the endpoint to HTTPS and edit the interaction
  model to match ADR 0005, once the relay exists.

## Alternatives considered

- **Create a brand-new skill** — rejected as unnecessary; Skill ID reuse
  carries no endpoint-type lock-in, so there's nothing gained by starting
  over.
