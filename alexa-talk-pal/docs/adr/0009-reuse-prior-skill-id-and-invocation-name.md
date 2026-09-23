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

### Update (2026-09-22, Phase 2 console reconfiguration)

**Skill ID reuse didn't happen.** When doing the endpoint/interaction-model
edits from this ADR's follow-ups, the original "English Talk Pal" skill
turned out to be registered under a different Amazon account than the one
the Echo device actually uses — invisible until logging in to make the
edit, since ADR 0006's "same account as the Echo" requirement was assumed
already true, not re-verified at decision time. The skill was recreated
from scratch under the correct account instead.

What survived: the invocation name (`"english talk pal"`) and en-US
locale, reused by choice as this ADR intended. What didn't: the Skill ID
itself is new, and the endpoint/interaction-model edits (backlog items
4–7, ADR 0003/0005) were applied to the new skill directly rather than as
follow-on edits to a reused one. The "no new console skill-creation step"
positive above did not hold in practice. Functionally this changes
nothing for Phase 3 (the new Skill ID is what `ALEXA_SKILL_ID` in the
netbook `.env` and the relay's `applicationId` check use), but any future
reference to "the reused skill" should assume a fresh Skill ID, not the
prototype's original one.
