# 0017. Add pt-BR as a second locale on the same skill, not a separate skill

- Status: accepted
- Date: 2026-09-23
- Deciders: solo (Luiz Wagner)
- Implements: `docs/plans/0004-phase4-portuguese-br-locale-support.md`

> **Reconciliation update (2026-09-23):** this plan and Plan 0005 were
> implemented in parallel on separate branches, each against the
> unmodified single-turn baseline (see "Build-order deviation" below and
> [ADR 0016](0016-multi-turn-conversation-via-session-attributes.md)'s
> mirror note). The two branches were subsequently merged by hand in
> `relay/app.py`. The deferred items this ADR originally listed —
> `ask_llm`'s combined signature, the four-prompt locale×mode matrix, and
> `session.attributes` interplay with locale — are now implemented as of
> that merge:
> - `ask_llm(query, locale=DEFAULT_LOCALE, conversation_history=None)` —
>   the exact target signature both plans called for (this ADR's §"Decision"
>   below, and ADR 0016).
> - `CONVERSATIONAL_SYSTEM_PROMPTS = {"en_US": ..., "pt_BR": ...}` is now
>   the live, per-locale, multi-turn-aware prompt table (the single-turn
>   `SYSTEM_PROMPTS` dict this ADR describes below is kept only as a
>   reference constant, superseded the same way ADR 0016 superseded
>   `VOICE_SYSTEM_PROMPT`).
> - The synthetic `AMAZON.FallbackIntent` continuation query (ADR 0016
>   §2.5 option 3) is now locale-keyed
>   (`FALLBACK_CONTINUATION_QUERIES["pt_BR"]`) so a Portuguese session
>   doesn't get an English line injected into its stored history — neither
>   plan anticipated this specific intersection on its own; it was added
>   during the merge.
> - Everything else below (locale extraction/normalization, `RESPONSE_TEXTS`,
>   `fallback_messages.get_messages`, the pt-BR interaction model) is
>   unchanged by the merge.
>
> Still outstanding, unchanged from this ADR's original scope: Alexa
> Developer Console configuration, live-Echo testing, and re-running
> `measure_latency.py` against the real endpoint (plan §6 Phases 2-3).

## Context

The relay and interaction model were English-only. Alexa Skills support
multiple locales under a single Skill ID (ADR 0009 already noted this as a
non-hard-wall for en-US-only v1), each with its own interaction model but
sharing the Skill ID, endpoint URL, and secrets. Plan 0004 scoped adding
Portuguese (Brazil) as a second locale on top of the existing single-turn
relay.

**Build-order deviation from the plan's own §11 recommendation:** the plan
recommended landing Plan 0005 (multi-turn conversation, which changes
`ask_llm`'s signature to take `conversation_history`) first, so this work
could build locale routing on top of the already-changed signature
(`ask_llm(query, locale, conversation_history=None)`). That didn't happen —
Plan 0005 was implemented in parallel on a separate branch against the same
unmodified baseline, not merged first. This work therefore lands against
the current single-turn `ask_llm(query)` signature; the multi-turn
signature, `session.attributes` interplay (plan §2.6, "Bilingual
Sessions"), and the four-prompt (locale × mode) matrix described in the
plan's §11 are explicitly deferred to whoever reconciles the two branches.

## Decision

- **One skill, two locales.** pt-BR is a second interaction model on the
  existing Skill ID (English Talk Pal), not a new skill — same endpoint
  URL, same secrets, same `ALEXA_SKILL_ID` check. Console-side creation
  (Alexa Developer Console → add locale) is manual, tracked as remaining
  work; only the JSON artifact is produced here, at
  `alexa/interaction-model.pt-BR.json`.
- **Reuse the invocation name** `"english talk pal"` (ADR 0009) for pt-BR
  rather than inventing a Portuguese phrase — lowest risk, no
  ASR-collision testing needed, per the plan's recommended default.
- **Locale extraction:** `_extract_locale(parsed_body)` reads
  `request.locale` from the Alexa payload. Alexa sends this hyphenated per
  BCP-47 (`"pt-BR"`, `"en-US"`) — that's the real wire format normalized
  from, not an assumption. `_normalize_locale` converts to an internal
  underscore key (`"pt_BR"`, `"en_US"`) and falls back to `en_US` for
  anything missing, malformed, or not a locale this relay has content for
  (no allow-list env var — dropped per plan §2.4 as YAGNI, same as
  `LOCALE_PREFERENCE`).
- **Language-aware content, not language-aware logic.** A single
  `SYSTEM_PROMPTS` dict (en_US/pt_BR), a single `RESPONSE_TEXTS` dict
  (HELP/GOODBYE/LAUNCH/NO_QUERY/RATE_LIMIT), and `fallback_messages.py`'s
  `MESSAGES`/`get_messages()` (GENERIC_ERROR/QUOTA_EXHAUSTED/TIMEOUT) hold
  the two languages' strings. Every response-producing code path threads
  the locale it already extracted through `_get_response_text(key,
  locale)` / `get_messages(locale)` rather than branching on locale
  in-line.
- **`ask_llm`'s final signature here is `ask_llm(query, locale=DEFAULT_LOCALE)`**
  — no `conversation_history` parameter. `ask_openrouter`,
  `ask_local_llm`, `_call_openrouter`, `_call_local_llm`, and
  `_call_chat_completions` all gained a matching `locale` parameter
  (defaulted, so `measure_latency.py`'s existing call site kept working
  unchanged) that selects the system prompt via
  `_system_prompt_for_locale`.
- **Testing strategy:** new `relay/tests/test_locale.py` (not
  `test_signature.py`, which stays scoped to signature verification) covers
  locale extraction/normalization, response-text/message selection, full
  `/alexa` POST for both locales including all fallback paths, and a
  language-compliance heuristic (§7 of the plan) — routing to the right
  system prompt is not proof the reply is actually in that language, so a
  stopword/diacritic heuristic is applied both to this relay's own pt_BR
  content (regression protection) and to a mocked English reply against a
  pt-BR request (proves the heuristic would catch a real leak). This
  heuristic cannot itself prove a live free-tier model won't answer in
  English — that requires the live-Echo/simulator testing in the plan's
  Phase 2–3, which is out of scope for this change (see Consequences).

## Consequences

### Positive
- No new skill, no new secrets, no new env vars — locale is entirely
  request-driven.
- Every hardcoded English string in the relay now has a Portuguese
  counterpart, with a test suite (39 new cases) that fails loudly if one
  goes missing or regresses to English.
- `ask_llm` and friends keep a call shape close enough to the pre-existing
  single-turn baseline that reconciling against Plan 0005's history-bearing
  signature later is a straightforward additive merge (add
  `conversation_history=None`), not a redesign.

### Negative / trade-offs
- **Not merged with Plan 0005.** The plan's own §11 called the two plans
  non-orthogonal and recommended sequencing them; that recommendation was
  not followed here (see Context). `alexa_response`'s eventual
  `session_attributes` parameter, the locale-switch-clears-history decision
  (plan §2.6), and the four-prompt (locale × single/multi-turn) matrix all
  remain unresolved until someone reconciles this branch against Plan
  0005's.
- **Language compliance is unverified against a real model.** The
  heuristic test suite only proves the plumbing is locale-correct and that
  the heuristic itself is discriminating — it says nothing about whether
  `liquid/lfm-2.5-2.6b:free` will reliably answer in Portuguese given a
  Portuguese system prompt and query. Small free-tier models are not
  reliably instruction-following on language choice (plan §7); this needs
  live simulator/Echo testing before pt-BR is considered production-ready.
- **Console configuration, latency testing, and live Echo testing are all
  still manual/outstanding** (plan §6 Phases 0.2, 2, 3) — this ADR covers
  only the relay-code and artifact portion of the plan's Minimal Scope.
- Portuguese translations (system prompt, response texts, fallback
  messages, sample utterances) were written, not sourced from a native
  speaker or professional translation pass — plausible but unverified
  Brazilian Portuguese phrasing.

### Follow-ups
- Console-side pt-BR locale creation and build (plan §6 Phase 2).
- Re-run `measure_latency.py --questions-file <pt-BR queries> --locale
  pt_BR` (both flags added in this change) to confirm the 8s deadline holds
  for Portuguese, per ADR 0011/0012's already-tight English baseline (plan
  §6 Phase 0.2).
- Live Echo testing in both locales, including the language-compliance
  question a heuristic can't answer (plan §6 Phase 3).
- Reconcile with Plan 0005 once both branches are ready to merge: combined
  `ask_llm(query, locale, conversation_history=None)` signature, the
  locale-switch/history-clearing decision (plan §2.6), and a four-prompt
  system-prompt matrix.

## Alternatives considered

- **Separate skill for pt-BR** — rejected; Alexa natively supports
  multiple locales per skill (ADR 0009), so a second skill would duplicate
  the Skill ID, endpoint, and secrets management for no benefit.
- **Distinct Portuguese invocation name** (e.g. `"assistente conversa"`) —
  rejected for now as higher-risk (untested ASR collision) with no
  offsetting benefit over reusing the existing, already-tested invocation
  name; can be revisited after live testing if `"english talk pal"` proves
  awkward for Portuguese speakers.
- **`LOCALE_PREFERENCE` / `SUPPORTED_LOCALES` env vars** — rejected as
  YAGNI (plan §2.4); the only real failure mode (missing/unrecognized
  locale) is already handled by the `en_US` default, leaving nothing for a
  config toggle to do.
- **Landing this after Plan 0005** (the plan's own recommended order) —
  not followed; the two plans were implemented in parallel on separate
  branches by design of this work session, with reconciliation deferred
  (see Context and Consequences).

### Update (2026-09-23, per-locale invocation names after real-Echo testing)

**The shared-invocation-name decision above didn't hold.** This ADR's
Decision section (and its "Distinct Portuguese invocation name" rejection
under Alternatives) called reusing one phrase across both locales the
lowest-risk option, needing no ASR-collision testing. Real testing on a
real Echo (bilingual en-US + pt-BR device) showed otherwise: en-US's
invocation name itself first had to change from `"english talk pal"` to
`"chat buddy"` (ADR 0009's update, unrelated ASR-collision rejection),
and that new name, spoken as `"abrir Chat Buddy"`, was not recognized in
Portuguese -- even though the same device correctly handles other
ordinary Portuguese utterances (confirmed with built-in commands, both
`"tell me a joke"` and `"conte-me uma piada"` worked). That rules out a
device-language-configuration explanation; the mixed-language phrase
itself doesn't fit pt-BR's ASR vocabulary.

**Revised decision:** en-US and pt-BR now use independent invocation
names -- en-US keeps `"chat buddy"`; pt-BR uses `"papo amigo"` ("friendly
chat"), chosen for being common, everyday Portuguese words with no
English loanwords. This needed no relay code change: `invocationName` was
already a per-locale field in each interaction model
(`alexa/interaction-model.json` / `alexa/interaction-model.pt-BR.json`),
so the two locales were never actually coupled at the code level, only by
this ADR's stated choice to keep their values equal.

### Follow-ups (added 2026-09-23)
- Once `"papo amigo"` is live and Built for the pt-BR locale, confirm on
  a real Echo that it's recognized (mirroring the en-US confirmation
  above) -- not yet done as of this update.
- If future locales are added, default to letting each pick its own
  invocation name rather than assuming a shared phrase will work; treat a
  shared name as something to verify, not assume.
