# 0010. Opt in to OpenRouter free-endpoint logging/training

- Status: accepted
- Date: 2026-09-21
- Deciders: solo (Luiz Wagner)

## Context

OpenRouter gates many `:free` models behind an account-level privacy
setting: "enable free endpoints that may train on inputs / publish
prompts." Leaving it off restricts the usable free model list to a much
shorter set; turning it on is, per `architecture.md` §9.6, "the implicit
cost of $0 inference." This was backlog item 1, blocking Phase 1's model
selection (item 2) since the candidate pool depends on the toggle. The
$0-spend-limit API key is already created; this ADR resolves the
remaining open question — the toggle itself.

Everything asked through this skill already goes to Amazon (via the Echo)
and to OpenRouter regardless of this setting. The incremental exposure
from opting in is that the specific downstream `:free` provider serving a
given query may also log/train on that one prompt.

This is a personal household assistant for casual spoken questions
(trivia, quick facts, "what's the capital of X") — not a channel anyone
is likely to route sensitive personal data through, and `architecture.md`
already bakes in two independent mitigations regardless of this toggle:
the relay doesn't persist full utterances to disk by default (§9.6), and
any debug logging that does happen must stay outside the
Syncthing-synced folders (§9.4/§9.6).

## Decision

Opt in: enable free endpoints that may log/train on prompts, keeping the
full `:free` model catalog available for Phase 1's latency-based model
selection (backlog item 2). Mitigate via behavior, not via restricting
the model list — keep genuinely private questions off this Echo skill.

**Action still needed**: this is an OpenRouter account/privacy-dashboard
toggle, not something settable via the API key or `.env` — flip it under
OpenRouter account settings if not already on.

## Consequences

### Positive
- Unblocks backlog item 2 (Phase 1 latency measurement) with the full
  candidate model pool, not a truncated non-training-opt-in subset.
- Matches the project's stated default of keeping complexity minimal
  (architecture.md §11) — no extra bookkeeping to track which specific
  free models require the toggle and which don't.

### Negative / trade-offs
- Prompts sent through `:free` models may be logged/used for training by
  the serving provider, on top of OpenRouter and Amazon already seeing
  them. Accepted as the known cost of $0 inference, not a surprise.
- This is a household-wide behavioral mitigation ("keep private questions
  off the Echo"), not a technical control — it depends on household
  members actually knowing and following it.

### Follow-ups
- None blocking. If this ever needs tightening, the reverse toggle plus
  re-running backlog item 2's model shortlist against the training-safe
  subset is a small, reversible change.

## Alternatives considered

- **Opt out, restrict to non-training-opt-in free models** — rejected:
  shrinks the model list significantly for a personal-use assistant
  where the marginal privacy gain (Amazon and OpenRouter already see
  every query either way) doesn't offset the added complexity of a
  narrower, more volatile candidate pool for Phase 1's model selection.
