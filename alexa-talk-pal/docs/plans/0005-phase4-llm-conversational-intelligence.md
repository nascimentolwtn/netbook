# Plan 0005: LLM-Powered Conversational Understanding for Alexa

## Executive Summary

Transform alexa-talk-pal from stateless question-answering to multi-turn conversational AI by leveraging Alexa's built-in `session.attributes` for context storage. The relay remains stateless at the filesystem level while maintaining conversation history across turns. This approach requires:

- ~400 lines of new code in `app.py` (conversation context management)
- 1 new module for conversation utilities (message building, history trimming)
- Updates to system prompt for conversational awareness
- Adjusted token budgets to accommodate historical context
- New test suite for multi-turn flows

**Phase:** Phase 4 (optional polish per `architecture.md` §11), but worth doing early since multi-turn enablement improves UX significantly.

---

## 0. Prerequisites

This plan assumes a working, tested v1 exists. Two things must be true
before starting, not just "unblocked":

1. **Backlog item 1 (OpenRouter reasoning-leak bug) is fixed.** Closed
   2026-09-23 — `OPENROUTER_MAX_TOKENS` raised to 500, shared
   `_strip_leaked_reasoning` helper added, see
   [ADR 0015](../adr/0015-openrouter-max-tokens-500-plus-reasoning-strip.md)
   and the CHANGELOG's 2026-09-23 entry. Confirmed done — but re-verify no
   regression before building on top of it, since this plan's token-budget
   section (§2.3) and latency assumptions (§6) depend on it holding: the
   same mandatory-reasoning/shared-token-budget bug this fix resolved will
   come back if `max_tokens` is lowered (see §2.3's correction below).
2. **Phase 3 live Echo testing (Plan 0003) has actually run.** As of this
   writing, `docs/plans/0003-phase3-live-mvp-test-with-real-echo.md` is
   still **Status: proposed** — the CHANGELOG confirms Phase 2 + the
   reasoning-leak fix together only "close out everything blocking" that
   test, not that it has happened. Multi-turn conversation is meaningless
   to build on a v1 that hasn't been confirmed working end-to-end against
   a real Echo device yet — in particular, the follow-up-routing gap (§2.5
   below) can only really be validated against real Alexa ASR/NLU
   behavior, not the console simulator alone. Run Plan 0003 to completion
   first.

---

## 1. Current State Analysis

**What exists:**
- Stateless relay: each query treated independently
- Single-turn message list: `[{role: system}, {role: user}]`
- No conversation memory or context tracking
- Fixed system prompt for voice output
- Architecture designed for ~150 tokens output (OpenRouter) / 500 (local)

**Constraints that shape design:**
- Alexa's 8-second response deadline (5-second read timeout on OpenRouter)
- 2GB RAM netbook (no local state storage)
- Free OpenRouter tier with variable latency
- Voice output (short, scannable answers required)
- `session.attributes` is Alexa's native vehicle for state (auto round-tripped)

**Key architectural decision from `architecture.md` §10.3:**
> "Start stateless — each query is independent. It's simplest, cheapest, and fastest. If follow-ups matter, the natural next step is keeping the last N turns in the Alexa `session.attributes` (which ASK round-trips for you, so the relay stays stateless and no storage is needed on the netbook)."

This design choice is optimal and ready to implement.

---

## 2. Architectural Changes Required

### 2.1 Session Attributes Structure

**Current state:** No `session.attributes` used by the relay.

**Proposed structure (simplified — dropped two redundant fields, see below):**
```python
{
  "conversation_history": [
    # Array of {role, content} messages — last N complete exchanges
    {"role": "user", "content": "What is the capital of France?"},
    {"role": "assistant", "content": "The capital of France is Paris."},
    {"role": "user", "content": "Tell me more about its history."},
    {"role": "assistant", "content": "..."},
  ],
}
```

An earlier draft of this structure also carried `conversation_turn_count`
and `conversation_started_at`. Both are dropped:
- `conversation_turn_count` is redundant — turn count is just
  `len(conversation_history) // 2`, derivable on read, not worth storing
  and keeping in sync.
- `conversation_started_at` was marked "optional, for cleanup" but nothing
  in this plan ever reads it — Alexa's own session lifecycle is what ends
  a conversation (§2.4), not a relay-side timestamp check. Keeping unused
  fields in `session.attributes` is a silent trap for a future bug (someone
  eventually writes code that trusts it's accurate).

**Rationale:**
- Keep full message pairs (Q & A) for LLM context
- Maintain conversation state across Alexa session lifetime
- Automatic cleanup when `shouldEndSession: true`
- Backwards-compatible: missing field = first turn

### 2.2 Message Construction Pipeline

**Current (single-turn):**
```python
messages = [
  {"role": "system", "content": VOICE_SYSTEM_PROMPT},
  {"role": "user", "content": query}
]
```

**Proposed (multi-turn):**
```python
messages = [
  {"role": "system", "content": CONVERSATIONAL_SYSTEM_PROMPT},
  # ... historical messages from session.attributes (if any)
  {"role": "user", "content": current_query}
]
```

**New system prompt (conversational aware):**
```python
CONVERSATIONAL_SYSTEM_PROMPT = (
    "You are a helpful voice assistant in an ongoing conversation. "
    "You have access to previous turns in this conversation. "
    "Reply in 2 to 4 short sentences of plain spoken prose. "
    "Do not use markdown, bullet lists, code, emoji, or URLs — "
    "your reply will be read aloud exactly as written. "
    "If the user asks a follow-up (e.g., 'tell me more', 'and why', 'how'), "
    "reference the prior context to give a natural continuation."
)
```

### 2.3 Token Budget Adjustments

**Current budget (as of ADR 0015 — not the 150/500 split an earlier draft
of this plan assumed):**
- OpenRouter: `OPENROUTER_MAX_TOKENS=500` (raised from a hardcoded 150 —
  ADR 0015 found the model's hybrid reasoning is **mandatory** on
  OpenRouter's free endpoint and was consistently burning ~148-150 of the
  old 150-token budget, leaving `content` empty with
  `finish_reason: "length"`)
- Local: `LOCAL_LLM_MAX_TOKENS=500` (ADR 0012/0013 — same root cause found
  first on the local backend)
- Single-turn message: ~50-100 tokens of context

**Proposed budget (multi-turn): do not reduce `max_tokens` on either
backend.** An earlier draft of this plan proposed cutting OpenRouter's
output ceiling to 100 tokens (and local's to 400) to "leave headroom."
That's backwards, and would reintroduce the exact bug ADR 0015 just fixed:

- `max_tokens` on both backends is a **shared budget between the model's
  mandatory reasoning and the spoken answer**, not an output-only ceiling
  (ADR 0012/0015). 500 is already the value that reliably left the
  reasoning step enough room to finish *and* leave tokens for content on
  this model. Reducing it back toward 100-150 risks `finish_reason:
  "length"` and empty `content` again.
- Multi-turn history changes the **input** (prompt) side of the request —
  more tokens the model reads before it starts generating — not the output
  ceiling. There's no token-budget reason tied to history size to lower
  `max_tokens`. If anything, more prompt content gives a reasoning-mandatory
  model more to reason about, arguing for keeping headroom, not cutting it.
- **Action: keep `OPENROUTER_MAX_TOKENS=500` and `LOCAL_LLM_MAX_TOKENS=500`
  unchanged.** Manage latency by trimming *history* (§2.2, already 5
  turns), not by shrinking the output ceiling.

**Realistic latency baseline (the table below was optimistic — treat it as
unverified, not as evidence of headroom):**
ADR 0012 measured OpenRouter free-tier latency for a **single-turn**,
current-production request shape, live from the netbook, same day: min
2.59s, avg 3.98s, **p90 6.08s**, max 6.08s (10/10 successful, no 429s) —
already uncomfortably close to the 8s Alexa deadline before any multi-turn
history is added. Multi-turn history adds prompt-processing time on top of
that baseline (more input tokens to process before generation starts), so
the realistic risk is that a 5-turn conversation pushes some fraction of
requests *past* 8s, not that there's 4+ seconds of comfortable margin as
the table below assumed.

**Action:** don't trust the table below as-is — re-measure with
`measure_latency.py`'s new multi-turn replay mode (§6a) against real
growing-history requests before relying on any number here.

**Latency impact analysis (original table, kept for reference only — the
"vs. 8s Budget" column reflects an optimistic 1-4s assumption, not ADR
0012's measured p90 of 6.08s for single-turn alone):**

| Scenario | Tokens | Est. Latency | vs. 8s Budget |
|----------|--------|--------------|---------------|
| Single-turn (status quo) | ~100 (context+output) | 1-3s | Safe |
| 5-turn history + output | ~1500 (context+output) | 2-4s | Safe |
| High-load free endpoint | ~1500 | 5-6s | Tight but OK |
| Worst case + timeout | N/A | 5s timeout | Graceful fallback |

### 2.4 Conversation Lifecycle

**Session start** (LaunchRequest):
- Ignore `session.attributes.conversation_history` if present
- Start fresh for this Alexa session
- Return greeting without opening history

**Conversation turns** (IntentRequest → AskAnythingIntent):
1. Extract `session.attributes.conversation_history` (if present)
2. Trim to last 5 turns if larger
3. Build full message list with history
4. Call LLM with multi-turn messages
5. Extract response text
6. Append new user query and assistant response to history
7. Return response **with updated `session.attributes`**

**Session end** (AMAZON.Stop/Cancel or `shouldEndSession: true`):
- Clear conversation history by not returning it
- Alexa will drop session and start fresh on next LaunchRequest

### 2.5 Critical Design Gap #1: Follow-Up Routing

An earlier draft of this plan assumed the LLM alone handles follow-ups —
the system prompt says "if the user asks a follow-up (e.g., 'tell me
more', 'and why', 'how'), reference the prior context." That's necessary
but nowhere near sufficient, because **Alexa's NLU has to match an intent
before the relay ever sees the request at all.**

**The gap:** `AskAnythingIntent`'s current sample utterances all require a
carrier phrase plus the `query` slot (`AMAZON.SearchQuery`) — things like
`"ask {query}"`, `"tell me {query}"`. A bare follow-up like "and why?" or
"tell me more" is not a full carrier-phrase utterance in that shape. Alexa
may not generate an `IntentRequest` for `AskAnythingIntent` at all — it
could instead route to `AMAZON.FallbackIntent` (which `app.py`'s intent
dispatch does not currently handle by name, so it falls into the generic
`return alexa_response(GENERIC_ERROR_FALLBACK, ...)` branch), or Alexa's
own device-level "I didn't quite get that" behavior, **before the relay's
multi-turn logic ever runs.** Building the history/context machinery
without fixing this means the flagship use case (natural follow-ups) may
simply never reach the code that handles it.

**Three ways to close this gap — pick at least one before shipping:**
1. **New sample utterances for follow-up-style phrasing.** Add utterances
   without a full-question carrier — e.g. `"and why"`, `"tell me more"`,
   `"what else"`, `"why"`, `"how"` — either as additional samples on
   `AskAnythingIntent` (if a slot-less/short-form sample is even accepted
   by the validator; untested) or as a new dedicated `FollowUpIntent` with
   no slot. Needs pt-BR equivalents too once Plan 0004 lands (`"e por
   quê"`, `"me conta mais"`, `"como assim"`, etc.) — one more reason the
   interaction model has to be updated for *both* locales together (see
   Plan 0004 §11).
2. **`Dialog.ElicitSlot`.** Have Alexa re-prompt to fill the `query` slot
   when NLU produces a partial/low-confidence match. Not implemented
   anywhere in this codebase today — this is new dialog-management work,
   not a config toggle.
3. **`AMAZON.FallbackIntent` handling.** Add an explicit branch in
   `app.py`'s intent dispatch that treats a `FallbackIntent` match as "this
   might be a follow-up" and routes it into the multi-turn LLM call using
   only the stored history (no new query text — Alexa's `FallbackIntent`
   request doesn't carry the raw utterance). Weaker than options 1-2
   (no way to know what the user actually said), but requires no
   interaction-model change.

**This plan's interaction-model section, previously missing, is this one:**
before this feature can work end-to-end, `alexa/interaction-model.json`
needs one of the above changes, tested in the console simulator, before
any live-Echo follow-up test (§9 effort estimate accounts for this).

### 2.6 Critical Design Gap #2: History Persistence Discipline

**The rule:** every response that does not end the session must echo
`sessionAttributes` back, or Alexa drops the conversation history on the
very next turn — session attributes are not preserved automatically by the
platform; they persist only across turns where the skill explicitly
returns them each time.

An earlier draft of this plan only showed updating the happy path (§3.1's
"NEW" flow) and `alexa_response`'s signature (§3.3) to *support* an
optional `session_attributes` parameter — it didn't walk through every
existing call site that has to actually pass it. Checked against the
current `app.py` response paths, every one of these has to be updated to
receive and return the current history, or a single quota/timeout/error
turn silently wipes the conversation for the rest of the session:

| Response | Current `end_session` | History must be echoed? |
|---|---|---|
| Successful LLM answer | `False` | **Yes** — the new turn |
| `NO_QUERY_TEXT` (empty query slot) | `False` | **Yes** — unchanged from before this turn |
| `RATE_LIMIT_FALLBACK` (`OpenRouterRateLimited`) | `False` | **Yes** |
| `TIMEOUT_FALLBACK` (`requests.exceptions.Timeout`) | `False` | **Yes** |
| `GENERIC_ERROR_FALLBACK` (any other exception, or empty `speech`) | `False` | **Yes** |
| `AMAZON.HelpIntent` → `HELP_TEXT` | `False` | **Yes** |
| Unrecognized intent name → `GENERIC_ERROR_FALLBACK` | `False` | **Yes** |
| `QUOTA_EXHAUSTED_FALLBACK` | `True` | No — session ends, Alexa drops it anyway |
| `AMAZON.StopIntent`/`CancelIntent` → `GOODBYE_TEXT` | `True` | No — session ends |
| `LaunchRequest` → `LAUNCH_GREETING` | `False` | No, **deliberately** — a new launch starts a fresh conversation; this is the one `end_session=False` path that intentionally omits history |

**Action:** don't rely on `alexa_response`'s `session_attributes=None`
default doing the right thing by omission. Every one of the six "Yes" rows
above needs its call site in `_handle_ask_anything` (and the top-level
`/alexa` handler for Help/unrecognized-intent) explicitly updated to pass
the current history through, not just the success path. Add a test for
each row — a mid-conversation timeout/error/Help turn that doesn't lose
history is exactly the kind of bug that's invisible in a quick manual test
(the conversation *seems* to work right up until the one turn that quietly
resets it) and expensive to debug from a bug report weeks later.

---

## 3. Integration Points

### 3.1 Request Handler Changes

**Current flow (simplified):**
```
POST /alexa
→ verify signature
→ extract query from slots
→ call ask_llm(query)
→ return alexa_response(speech)
```

**New flow:**
```
POST /alexa
→ verify signature
→ extract request type
→ if LaunchRequest:
    → clear conversation context
    → return greeting
→ if IntentRequest with AskAnythingIntent:
    → extract query from slots
    → extract session.attributes
    → trim conversation history
    → build multi-turn messages
    → call ask_llm(query, messages=all_messages)  ← NEW
    → append to history
    → return alexa_response(speech, session_attributes=updated)  ← NEW
→ if Stop/Cancel/End:
    → clear session context
→ if AMAZON.FallbackIntent (§2.5, follow-up routing gap):
    → decide per one of §2.5's three options — currently unhandled by
      name, falls through to the generic error branch below
→ every other non-ending branch (Help, no-query, rate-limit, timeout,
  generic error, unrecognized intent) must ALSO thread session.attributes
  through and return it unchanged (§2.6) — not shown as a separate arrow
  per branch here, but each one needs the same `session_attributes=`
  argument the happy path gets above
```

### 3.2 LLM Call Signature Changes

**Current:**
```python
def ask_llm(query):
    # Internal: builds messages = [system, user]
    return answer_text
```

**Proposed:**
```python
def ask_llm(query, conversation_history=None):
    """
    query: current user question
    conversation_history: list of prior {role, content} pairs (optional)
    Returns: (answer_text, updated_history)
    """
    messages = build_messages_with_history(
        query,
        history=conversation_history,
        system_prompt=CONVERSATIONAL_SYSTEM_PROMPT
    )
    answer = call_chat_completions(messages)
    updated_history = append_to_history(conversation_history, query, answer)
    return answer, updated_history
```

**What gets appended to history matters:** `answer` here must be the
*cleaned* spoken text — i.e. already passed through `_strip_leaked_reasoning`
/ `_extract_spoken_text` (ADR 0015), the same text that's actually spoken
to the user — never the raw model response. And `append_to_history` must
only ever be called with a real LLM answer, never with one of the canned
fallback strings (`RATE_LIMIT_FALLBACK`, `TIMEOUT_FALLBACK`,
`GENERIC_ERROR_FALLBACK`, `HELP_TEXT`, `NO_QUERY_TEXT`). Saving a fallback
line as an "assistant" turn would poison every future turn in the
conversation with a lie ("I'm getting a lot of questions right now" is not
an answer to whatever the user actually asked) that the model then treats
as real prior context. §2.6's table already establishes those fallback
paths must echo the *existing* history back unchanged — this is the
matching rule for what never gets *added* to it.

**Locale note (coordination with Plan 0004):** this plan builds
`ask_llm(query, conversation_history=None)` English-only, per the
recommended build order (Plan 0004 §11). Plan 0004 extends this same
signature to `ask_llm(query, locale, conversation_history=None)` — whoever
implements second must not drop the other plan's parameter. See Plan 0004
§11 and this plan's own "Shared Touch Points with Plan 0004" note below
(§14).

### 3.3 Response Shaping Changes

**Current:**
```python
def alexa_response(speech_text, end_session=False):
    return jsonify({
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": speech_text},
            "shouldEndSession": end_session,
        },
    })
```

**Proposed:**
```python
def alexa_response(speech_text, end_session=False, session_attributes=None):
    resp = {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": speech_text},
            "shouldEndSession": end_session,
        },
    }
    if session_attributes is not None:
        resp["sessionAttributes"] = session_attributes
    return jsonify(resp)
```

Alexa will return updated `sessionAttributes` to the client, and they'll be included in the next request automatically.

---

## 4. New Module: `conversation.py`

Located at `/home/lw_na/git/netbook/alexa-talk-pal/relay/conversation.py`, this module encapsulates conversation management logic:

```python
"""
Conversation context management for multi-turn Alexa interactions.

Handles:
- Extracting conversation history from Alexa session.attributes
- Building multi-turn message lists for the LLM
- Trimming history to stay within token budget
- Appending new turns to history
- Validating and sanitizing conversation data
"""

def extract_conversation_history(session_attributes):
    """Extract and validate conversation history from Alexa session."""
    # Returns: list of {role, content} dicts or empty list
    
def trim_conversation_history(history, max_turns=5):
    """Keep only the last N complete user-assistant pairs."""
    # Returns: trimmed history
    
def build_messages_with_history(query, conversation_history=None, system_prompt=""):
    """Construct the full message list for LLM call."""
    # Returns: [{role: system}, ...historical..., {role: user, content: query}]
    
def append_to_history(history, user_query, assistant_response):
    """Add the new turn to conversation history."""
    # Returns: updated history list
    
def estimate_tokens(messages):
    """Rough token count for a message list (for monitoring)."""
    # Returns: approximate token count
    
def should_clear_history(request_type):
    """Decide if history should be cleared for this request type."""
    # LaunchRequest, SessionEndedRequest → True; IntentRequest → False
    
def session_attributes_from_history(history):
    """Package history back into session.attributes format."""
    # Returns: dict suitable for Alexa response
```

---

## 5. Guardrails and Edge Cases

### 5.1 Prompt Injection Protection

**Risk:** User crafts input like `"Ignore previous instructions and..."` to manipulate behavior.

**Mitigation:**
1. Keep user inputs in `user` role (LLM treats differently from system)
2. System prompt emphasizes voice constraints (can't respond to meta-instructions)
3. No direct shell execution, code eval, or environment access
4. Model is instructed to stay in-character as voice assistant

**Doesn't require new code:** The existing role-based message structure plus system prompt already provide this.

### 5.2 History Explosion

**Risk:** Keeping all messages grows unbounded.

**Mitigation:**
- Trim to last 5 complete turns (10 messages max) — sufficient for context without token bloat
- Log a warning if history exceeds 10 turns (indicates a bug)
- Cap `session.attributes` payload itself (Alexa has ~24KB limit; 5 turns ≈ 2-3KB)

**Code location:** `conversation.py:trim_conversation_history()`

### 5.3 Timeout During Multi-Turn

**Risk:** LLM takes too long with multi-turn prompt; OpenRouter times out at 5s.

**Mitigation:**
- Timeout behavior is unchanged: relay catches exception → returns `TIMEOUT_FALLBACK`
- History is **preserved** in this case (user can retry and continue from same context)
- This is actually better UX than losing context

### 5.4 Session Inconsistencies

**Risk:** Alexa client crashes mid-session; history becomes stale or mismatched.

**Mitigation:**
- Alexa's native session lifecycle handles this — but not on a ~15-minute
  idle timer as an earlier draft of this plan claimed. In practice a
  session closes within **seconds** of the device getting no reply: after
  each `shouldEndSession: false` response, the Echo listens for a
  follow-up for a short window (on the order of ~8s, not minutes), and if
  the user doesn't speak in that window the session ends. There is no
  official ~15-minute idle session; don't design around that number.
- Next launch starts fresh (we ignore old history on LaunchRequest)
- No need to validate history consistency — just skip malformed entries

### 5.5 Backend Agnosticism

**Risk:** Local llama.cpp backend behaves differently with multi-turn context.

**Mitigation:**
- `conversation.py` is backend-agnostic (builds messages only)
- Both `ask_openrouter()` and `ask_local_llm()` receive same message structure
- No backend-specific adjustments needed (both support OpenAI chat format)
- Separate token budgets already account for different max_tokens per backend

### 5.6 Language Compliance in Mixed-Language History

This plan itself is English-only (recommended build order, Plan 0004
§11). But once Plan 0004 lands, a session's stored `conversation_history`
could contain English turns from earlier in the session while the current
request's locale is pt-BR (or the reverse) — e.g. a device-language change
mid-session (Plan 0004 §2.6), or a bug that doesn't clear history on a
locale mismatch.

**Decision needed (owned by whichever plan lands second, per the build
order):** if a Portuguese follow-up arrives with English turns still in
`conversation_history`, either (a) clear history on locale mismatch before
building the message list (Plan 0004 §2.6 already recommends this as the
general rule), or (b) send the mixed-language history to the model anyway
and accept it may reply in the wrong language or get confused by the
switch. Recommend (a), consistent with Plan 0004 — don't solve this twice
with two different answers.

---

## 6. Token Budget Validation

**Spreadsheet simulation (estimate only — corrected from an earlier draft
that used a 100-token output budget; §2.3 keeps `max_tokens` at 500
unchanged on both backends, and the "vs. 8s Budget" column below is
unverified against ADR 0012's real p90, not a confirmed number):**

| Turn | User Query | Assistant Reply | Cumulative Context | Est. Tokens | Output Budget | Total (worst case) | vs. 8s Budget |
|------|-----------|-----------------|-------------------|-------------|---------------|-------|---------------|
| 1 | "What's Pi?" | "Pi is..." | 1 query+reply | 100 | 500 | 600 | Unverified — see below |
| 2 | "Give more digits" | "Here are..." | 2 queries+replies | 300 | 500 | 800 | Unverified |
| 3 | "Why is it irrational?" | "Because..." | 3 q+r pairs | 500 | 500 | 1000 | Unverified |
| 5 | "How is it used?" | "Engineers..." | 5 q+r pairs | 1000 | 500 | 1500 | Unverified |
| 7 (trimmed to 5) | Follow-up | Answer | 5 q+r pairs (trimmed) | 1000 | 500 | 1500 | Unverified |

**Assumptions:**
- ~50-100 tokens per user query
- ~100-200 tokens per assistant reply
- ~100 tokens for system prompt
- `max_tokens=500` is the *ceiling* the reasoning-mandatory model can use,
  not typically how much it uses — but ADR 0012/0015 both show it's the
  floor that avoids truncation, so it's the right number to budget against
  for a worst case, not 100
- LFM2.5-2.6B processes ~300-400 tokens/sec on good free endpoints

**Conclusion (corrected):** this table's token-count math is fine, but the
"Total tokens ÷ throughput" latency estimate it implies (the earlier
draft's "well under 4s") ignores ADR 0012's actual measured p90 of 6.08s
for a *single*-turn request on OpenRouter's free tier — a number this
table's assumptions never explain away. Growing prompt size with each
turn plausibly makes that worse, not better. **Don't trust this table's
latency column until §6a's multi-turn replay mode produces real numbers**;
treat the token-count estimates as a sanity check only, not a latency
proof.

## 6a. `measure_latency.py` Updates Needed

`measure_latency.py` currently imports `_call_chat_completions` from
`app.py` and calls it with a single `query` string
(`_call_chat_completions(base_url, model, query, api_key=...)`), mirroring
today's single-turn `ask_llm(query)`. Both this plan and Plan 0004 change
that underlying signature, so the latency probe needs matching updates or
it silently stops reflecting what the relay actually sends:

- **Signature change:** once `ask_llm`/`_call_chat_completions` take a
  `messages` list / `conversation_history` (this plan) and `locale` (Plan
  0004) instead of a bare `query`, update `measure()` and its call site
  here to build the same request shape — otherwise this script measures a
  request the relay no longer sends, and its numbers stop being
  trustworthy for either plan's latency claims.
- **Multi-turn replay mode:** add a mode that replays a fixed sequence of
  2-5 queries as a **single growing conversation** — feed each response
  back in as history for the next call — instead of only ever measuring
  isolated single-turn queries from the `QUERIES` list. This is the only
  way to get real numbers for the "5-turn history" scenario §2.3 and §6
  depend on; right now nothing in the repo actually measures a multi-turn
  request, which is why every latency table above is marked unverified.
- **Question-file option (shared with Plan 0004):** add a
  `--questions-file` flag to load a query list from a file instead of the
  hardcoded English `QUERIES` constant, so the same script can be pointed
  at Plan 0004's Portuguese query set without a code fork.
- Re-run this updated script — single-turn *and* multi-turn-replay, both
  locales once Plan 0004 lands — before trusting any latency table in
  either plan.

---

## 7. Phase Implementation Strategy

### Phase 4a: Core Multi-Turn (this work)
1. Write `conversation.py` module
2. Update `app.py` message building
3. Update response shaping to include `sessionAttributes`
4. Update system prompt
5. Adjust token budgets

### Phase 4b: Testing
1. Unit tests for history trimming, message building
2. Mock Alexa requests with session.attributes
3. Integration tests against real OpenRouter
4. Latency benchmarking with 5-turn conversations

### Phase 4c: Optional Enhancements (if time + user interest)
1. Conversation summary/memory (e.g., "Remember: user is interested in X")
2. Explicit "clear history" voice command
3. Per-turn latency logging for monitoring
4. Conversation analytics dashboard (optional)

---

## 8. Risk Assessment and Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Multi-turn prompt exceeds 8s deadline | Medium | High | Token budget pre-calculated; monitor latency; fallback to single-turn if needed |
| Session.attributes size bloats | Low | Medium | Trim to 5 turns max; monitor JSON size |
| Prompt injection via user input | Low | Medium | Role-based message structure; system prompt emphasizes constraints |
| Local backend incompatibility | Low | Low | Both backends support OpenAI chat format; separate token budgets |
| User confused by context carry-over | Low | Low | System prompt instructs conversational behavior; users expect it |
| Signature verification breaks | Very low | High | Existing robust verification (ADR 0014); no changes here |
| History loss on network error | Low | Low | Alexa retries; history preserved server-side automatically |

---

## 9. Effort Estimate

| Task | Effort | Notes |
|------|--------|-------|
| Write `conversation.py` (history/message mgmt) | 2-3 hours | Core logic; well-defined scope |
| Update `app.py` integration | 1-2 hours | Message building; response shaping; threading `session_attributes` through **every** non-ending response path, not just the happy path (§2.6) |
| Update system prompt | 0.5 hours | Copy changes — no token budget change needed (§2.3 keeps `max_tokens` at 500 on both backends) |
| **Interaction model rework (new — was missing from this estimate entirely)** | 3-5 hours | §2.5: new follow-up sample utterances and/or `AMAZON.FallbackIntent` handling and/or `Dialog.ElicitSlot`; console configuration, build, simulator testing. Whichever of the three options is chosen, this is real new work, not covered by any line above |
| `measure_latency.py` updates | 1-2 hours | §6a: signature change, multi-turn replay mode, `--questions-file` flag |
| Unit tests (conversation logic) | 2-3 hours | History trim, message build, edge cases |
| Integration tests (mock Alexa requests) | 2-3 hours | Multi-turn flow validation, including every §2.6 fallback-path-echoes-history case |
| Latency benchmarking & tuning | 1-2 hours | Test 5-turn conversations with the updated `measure_latency.py`; verify against ADR 0012's real p90 baseline, not an assumed one |
| **Real-Echo follow-up testing (new — was missing)** | 2-3 hours | §2.5's routing fix only proves itself against real Alexa ASR/NLU, not the console simulator alone — needs a live Echo session per Plan 0003's prerequisite (§0), asking natural follow-ups ("and why?", "tell me more") and confirming they actually route and carry context |
| Documentation (ADR, architecture update) | 1 hour | Record decisions; update architecture.md |
| **Total** | **16-24 hours** | Up from the original 10-14 hour estimate, which omitted interaction-model rework and real-Echo follow-up testing entirely — both are required for the flagship "natural follow-up" use case to actually work, not optional polish |

---

## 10. Critical Files for Implementation

### Files to Create:
1. **`alexa-talk-pal/relay/conversation.py`** — New module for conversation management
   - `extract_conversation_history(session_attributes)`
   - `trim_conversation_history(history, max_turns=5)`
   - `build_messages_with_history(query, conversation_history, system_prompt)`
   - `append_to_history(history, user_query, assistant_response)`
   - `should_clear_history(request_type)`
   - `session_attributes_from_history(history)`

### Files to Modify:
2. **`alexa-talk-pal/relay/app.py`** — Main relay (update message building, response shaping)
   - Update `_handle_ask_anything()` to extract and pass conversation history
   - Update `ask_llm()` to accept conversation history parameter
   - Update `ask_openrouter()` and `ask_local_llm()` to use full message list
   - Update `alexa_response()` to accept and return `session_attributes`,
     and update **every** call site that returns a non-ending response
     (success, `NO_QUERY_TEXT`, `RATE_LIMIT_FALLBACK`, `TIMEOUT_FALLBACK`,
     `GENERIC_ERROR_FALLBACK`, `HELP_TEXT`, unrecognized intent) to pass
     the current history through — not just the happy path (§2.6)
   - Update system prompt constant to `CONVERSATIONAL_SYSTEM_PROMPT`
   - Add `AMAZON.FallbackIntent` handling per one of §2.5's three options
   - **Do not** change `OPENROUTER_MAX_TOKENS` or `LOCAL_LLM_MAX_TOKENS` —
     both stay at 500 (§2.3); an earlier draft of this plan proposed
     lowering them to 100/400, which would reintroduce the ADR 0015
     truncation bug

3. **`alexa-talk-pal/relay/tests/test_conversation.py`** — New test module
   - Tests for history extraction, trimming, message building
   - Mock Alexa request builders with session.attributes
   - Multi-turn flow tests

4. **`alexa-talk-pal/docs/architecture.md`** — Update section 10.3 & beyond
   - Document multi-turn design decision
   - Update token budget table (§5.3)
   - Update Phase 4 scope

5. **`alexa-talk-pal/docs/adr/0016-multi-turn-conversation-via-session-attributes.md`** — New ADR
   (numbered 0016, not 0015 — ADR 0015 already exists, for the
   reasoning-leak fix; this plan builds first per the recommended build
   order, so it takes 0016, leaving 0017 for Plan 0004's locale ADR — see
   §14 below)
   - Document decision to use session.attributes for context
   - Document the decision to keep `max_tokens` at 500 unchanged (§2.3),
     not the 100/400 an earlier draft proposed
   - Document the two critical design gaps and their resolution (§2.5
     follow-up routing, §2.6 history-persistence discipline)
   - Explain lifecycle management

6. **`alexa-talk-pal/relay/requirements.txt`** — No changes (no new dependencies)

### Files to Reference (read-only):
7. **`alexa-talk-pal/docs/adr/`** — All existing ADRs for context
8. **`alexa-talk-pal/CHANGELOG.md`** — Document completion

---

## 11. Design Trade-Offs and Alternatives Considered

### Alternative 1: Server-Side Session Storage (Rejected)
**Approach:** Store conversation history in a local SQLite/Redis on the netbook.
**Rationale rejected:**
- Adds complexity (DB migrations, disk I/O)
- No better than Alexa's native session.attributes
- Breaks simplicity principle ("relay stays stateless at filesystem level")
- Risk of disk exhaustion on constrained hardware
- Session cleanup logic becomes manual

**Kept:** Use Alexa's built-in session.attributes.

### Alternative 2: Conversation Summary (Deferred)
**Approach:** After N turns, summarize history to "User asked about X, Y, Z" to save tokens.
**Rationale deferred:**
- Adds complexity to initial implementation
- Risk: summary generation might exceed 8s deadline
- Can be added in Phase 5 if token pressure becomes real
- Monitor actual token usage first before optimizing

**Kept:** Exact history for now; can be enhanced later.

### Alternative 3: Separate Multi-Turn Model (Rejected)
**Approach:** Use a different model for multi-turn queries (e.g., larger model).
**Rationale rejected:**
- Adds operational complexity (model selection logic)
- LFM2.5-2.6B already handles multi-turn well (latency is fine)
- Token budget already accommodates it
- Single model simpler to manage and understand

**Kept:** Same model for both single and multi-turn.

### Alternative 4: Explicit "Reset History" Command (Deferred)
**Approach:** Let user say "Alexa, clear history" to wipe conversation.
**Rationale deferred:**
- Nice-to-have, not core MVP
- Session already ends quickly on its own — within seconds of no reply
  (§5.4), not a 15-minute wait as an earlier draft claimed — so "just stop
  talking" is already a fast reset
- Users can end session and re-launch to start fresh
- Can be added as Phase 4b polish item

**Kept:** Implicit reset only (LaunchRequest clears, SessionEndedRequest clears).

---

## 12. Success Criteria

A successful implementation of multi-turn conversational support will:

1. ✓ Maintain conversation history across 3+ consecutive turns within a single Alexa session
2. ✓ Respond to follow-ups contextually (e.g., "tell me more" references prior answers) — **and** the follow-up utterance actually routes to the relay in the first place (§2.5); an LLM that's good at using context is useless if Alexa's NLU never sends the request
3. ✓ Stay under 8-second Alexa deadline in 95% of cases (p95 latency) — verified against ADR 0012's real measured baseline via §6a's updated `measure_latency.py`, not an assumed number
4. ✓ Gracefully handle timeouts and errors without losing context — verified for **every** row in §2.6's table (rate-limit, timeout, generic error, Help, no-query, unrecognized intent), not just the happy path
5. ✓ Clear history automatically on new LaunchRequest or SessionEnded
6. ✓ Work with both OpenRouter (default) and local llama.cpp (opt-in) backends
7. ✓ All existing single-turn functionality remains unchanged when history is empty
8. ✓ Pass unit tests for history trimming, message building, edge cases
9. ✓ Pass integration tests with mock Alexa multi-turn sequences
10. ✓ Real-Echo test confirms at least one natural follow-up phrasing (not
    a full re-asked question) correctly reaches `AskAnythingIntent` or its
    §2.5 replacement and produces a contextual answer

---

## 13. Monitoring and Observability

Once deployed, monitor:

**Metrics to log:**
```python
# In _handle_ask_anything, log:
- Conversation turn count (from history length)
- Estimated tokens in request (conversation.estimate_tokens)
- LLM response time (existing timeout tracking)
- History trimming events (if max_turns exceeded)
- Error rates by turn count (to catch multi-turn-specific issues)
```

**Dashboard items (optional, Phase 4c):**
- Average latency by turn count (single vs. 2-turn vs. 5-turn)
- Percentage of conversations that reach N turns
- Error rate correlation with conversation length

This will validate the token budget assumptions and identify if 5-turn trimming is adequate or needs adjustment.

---

## 14. Shared Touch Points with Plan 0004 (pt-BR)

An earlier draft of Plan 0004 called the two plans "orthogonal." They are
not — both modify the same `app.py` functions, the same interaction-model
artifact family, and the same `session.attributes` payload. This plan
builds those shared pieces first (recommended build order, Plan 0004 §11);
this section is the mirror of Plan 0004's own "Integration with Plan 0005"
section, listing what Plan 0004 will build on top of once this one lands.

- **`ask_llm` / `_call_chat_completions`:** this plan changes these to
  accept a message list / `conversation_history` in place of today's bare
  `query`. Plan 0004 further extends the same call to
  `ask_llm(query, locale, conversation_history=None)` — implement this
  plan's history parameter in a way that leaves room for locale to be
  added next, not as a positional argument that would force every call
  site to be rewritten twice.
- **Backends (`ask_openrouter`, `ask_local_llm`):** both need the trimmed
  history *and*, later, the locale-selected system prompt in the same
  message list.
- **`alexa_response`:** this plan adds `session_attributes`. Plan 0004
  doesn't change this function's shape, but every locale-aware response
  path (once it exists) still needs to pass through whatever this becomes.
- **System prompts:** the combined result is a **per-locale, per-mode**
  matrix — `VOICE_SYSTEM_PROMPT` (en-US, single-turn, today),
  `CONVERSATIONAL_SYSTEM_PROMPT` (en-US, multi-turn, this plan), plus
  pt-BR equivalents of both once Plan 0004 lands. Four prompts, not two.
- **Interaction model:** §2.5's follow-up-routing fix (new sample
  utterances and/or `Dialog.ElicitSlot`/`AMAZON.FallbackIntent` handling)
  needs pt-BR equivalents once Plan 0004 adds that locale, or pt-BR users
  get single-turn behavior while en-US users get multi-turn.
- **Locale-switching mid-session:** once this plan's history exists, Plan
  0004 §2.6 needs it to decide whether a mid-session locale change clears
  history — see also this plan's own §5.6 for the mixed-language-history
  angle.

See Plan 0004 §11 for the full recommended build order and rationale.

---

## Critical Files Summary

- `/home/lw_na/git/netbook/alexa-talk-pal/relay/app.py` — Main relay, message building & response shaping
- `/home/lw_na/git/netbook/alexa-talk-pal/relay/conversation.py` — NEW: Conversation context mgmt
- `/home/lw_na/git/netbook/alexa-talk-pal/relay/tests/test_conversation.py` — NEW: Multi-turn tests
- `/home/lw_na/git/netbook/alexa-talk-pal/docs/adr/0016-multi-turn-conversation-via-session-attributes.md` — NEW: Decision record (0016, not 0015 — see §10)
- `/home/lw_na/git/netbook/alexa-talk-pal/docs/architecture.md` — Reference; update §10.3 & Phase 4
