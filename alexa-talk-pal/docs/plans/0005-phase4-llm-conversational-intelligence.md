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

**Proposed structure:**
```python
{
  "conversation_history": [
    # Array of {role, content} messages — last N complete exchanges
    {"role": "user", "content": "What is the capital of France?"},
    {"role": "assistant", "content": "The capital of France is Paris."},
    {"role": "user", "content": "Tell me more about its history."},
    {"role": "assistant", "content": "..."},
  ],
  "conversation_turn_count": 3,  # Track for trimming logic
  "conversation_started_at": "2026-09-23T15:30:00Z"  # Optional, for cleanup
}
```

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

**Current budget:**
- OpenRouter: 150 tokens max output
- Local: 500 tokens max output
- Single-turn message: ~50-100 tokens of context

**Proposed budget (multi-turn):**
- Trim history to last **5 user-assistant pairs** (~10 messages max)
- Estimate historical context: ~1000-2000 tokens for 5 full turns
- Reduce output max_tokens: **100 tokens for OpenRouter** (down from 150)
- Reduce output max_tokens: **400 tokens for local** (down from 500)
- **Rationale:** Full turn history + LLM reasoning + output should stay under 3-4s on OpenRouter free tier, leaving 4+ seconds of safety margin against the 8s deadline.

**Latency impact analysis:**

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
- Alexa's native session lifecycle handles this (session expires after ~15 min of inactivity)
- Next launch starts fresh (we ignore old history on LaunchRequest)
- No need to validate history consistency — just skip malformed entries

### 5.5 Backend Agnosticism

**Risk:** Local llama.cpp backend behaves differently with multi-turn context.

**Mitigation:**
- `conversation.py` is backend-agnostic (builds messages only)
- Both `ask_openrouter()` and `ask_local_llm()` receive same message structure
- No backend-specific adjustments needed (both support OpenAI chat format)
- Separate token budgets already account for different max_tokens per backend

---

## 6. Token Budget Validation

**Spreadsheet simulation (estimate only):**

| Turn | User Query | Assistant Reply | Cumulative Context | Est. Tokens | Output Budget | Total | vs. 8s Budget |
|------|-----------|-----------------|-------------------|-------------|---------------|-------|---------------|
| 1 | "What's Pi?" | "Pi is..." | 1 query+reply | 100 | 100 | 200 | 1-2s ✓ |
| 2 | "Give more digits" | "Here are..." | 2 queries+replies | 300 | 100 | 400 | 1.5-2.5s ✓ |
| 3 | "Why is it irrational?" | "Because..." | 3 q+r pairs | 500 | 100 | 600 | 2-3s ✓ |
| 5 | "How is it used?" | "Engineers..." | 5 q+r pairs | 1000 | 100 | 1100 | 2.5-3.5s ✓ |
| 7 (trimmed to 5) | Follow-up | Answer | 5 q+r pairs (trimmed) | 1000 | 100 | 1100 | 2.5-3.5s ✓ |

**Assumptions:**
- ~50-100 tokens per user query
- ~100-200 tokens per assistant reply
- ~100 tokens for system prompt
- LFM2.5-2.6B processes ~300-400 tokens/sec on good free endpoints

**Conclusion:** Even with 5-turn history, we stay well under 4s typical latency, leaving 4+ seconds of headroom against the 8s deadline and free-tier variability.

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
| Update `app.py` integration | 1-2 hours | Message building; response shaping |
| Update system prompt & token budgets | 0.5 hours | Copy changes |
| Unit tests (conversation logic) | 2-3 hours | History trim, message build, edge cases |
| Integration tests (mock Alexa requests) | 2-3 hours | Multi-turn flow validation |
| Latency benchmarking & tuning | 1-2 hours | Test 5-turn conversations; verify 8s compliance |
| Documentation (ADR, architecture update) | 1 hour | Record decisions; update architecture.md |
| **Total** | **10-14 hours** | Solo development; 1-2 day sprint |

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
   - Update `alexa_response()` to accept and return `session_attributes`
   - Update system prompt constant to `CONVERSATIONAL_SYSTEM_PROMPT`
   - Adjust `OPENROUTER_MAX_TOKENS` from 150 → 100
   - Adjust `LOCAL_LLM_MAX_TOKENS` from 500 → 400 (after verification)

3. **`alexa-talk-pal/relay/tests/test_conversation.py`** — New test module
   - Tests for history extraction, trimming, message building
   - Mock Alexa request builders with session.attributes
   - Multi-turn flow tests

4. **`alexa-talk-pal/docs/architecture.md`** — Update section 10.3 & beyond
   - Document multi-turn design decision
   - Update token budget table (§5.3)
   - Update Phase 4 scope

5. **`alexa-talk-pal/docs/adr/0015-multi-turn-conversation-via-session-attributes.md`** — New ADR
   - Document decision to use session.attributes for context
   - Justify token budget adjustments
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
- Session already expires naturally after 15 min
- Users can end session and re-launch to start fresh
- Can be added as Phase 4b polish item

**Kept:** Implicit reset only (LaunchRequest clears, SessionEndedRequest clears).

---

## 12. Success Criteria

A successful implementation of multi-turn conversational support will:

1. ✓ Maintain conversation history across 3+ consecutive turns within a single Alexa session
2. ✓ Respond to follow-ups contextually (e.g., "tell me more" references prior answers)
3. ✓ Stay under 8-second Alexa deadline in 95% of cases (p95 latency)
4. ✓ Gracefully handle timeouts and errors without losing context
5. ✓ Clear history automatically on new LaunchRequest or SessionEnded
6. ✓ Work with both OpenRouter (default) and local llama.cpp (opt-in) backends
7. ✓ All existing single-turn functionality remains unchanged when history is empty
8. ✓ Pass unit tests for history trimming, message building, edge cases
9. ✓ Pass integration tests with mock Alexa multi-turn sequences

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

## Critical Files Summary

- `/home/lw_na/git/netbook/alexa-talk-pal/relay/app.py` — Main relay, message building & response shaping
- `/home/lw_na/git/netbook/alexa-talk-pal/relay/conversation.py` — NEW: Conversation context mgmt
- `/home/lw_na/git/netbook/alexa-talk-pal/relay/tests/test_conversation.py` — NEW: Multi-turn tests
- `/home/lw_na/git/netbook/alexa-talk-pal/docs/adr/0015-multi-turn-conversation-via-session-attributes.md` — NEW: Decision record
- `/home/lw_na/git/netbook/alexa-talk-pal/docs/architecture.md` — Reference; update §10.3 & Phase 4
