# 0006. Skill stays unpublished / development mode

- Status: accepted
- Date: 2026-09-20
- Deciders: solo (Luiz Wagner)

## Context

Amazon custom skills can either stay in development mode (usable only on
Echo devices signed into the developer's own Amazon account) or go through
certification for public distribution. `../architecture.md` §9.8 and §10.4
already recommend development mode for a personal-use assistant.

## Decision

Keep the skill in development mode indefinitely; never submit it for
certification or publish it to the skill store.

## Consequences

### Positive
- No certification gauntlet: no privacy policy URL, no store icons/listing,
  no Amazon review cycle — all of which are plausible additional sources of
  "difficult to configure" if ever attempted.
- Acts as an access control: only Echo devices on the developer's own
  Amazon account can invoke it.

### Negative / trade-offs
- Not shareable with anyone outside that Amazon account without adding them
  as a beta tester.
- Open/unverified: whether development-mode skills require periodic
  re-enabling to stay active (`../architecture.md` §10.4 flags this as
  needing confirmation).

### Follow-ups
- Confirm during Phase 3 (`../architecture.md` §11) whether the dev skill
  needs periodic manual re-enabling.

## Alternatives considered

- **Publish publicly** — rejected; unnecessary for personal/household use
  and adds real friction for zero benefit here.
