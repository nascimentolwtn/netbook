"""Conversation context management for multi-turn Alexa interactions.

Plan 0005 (`docs/plans/0005-phase4-llm-conversational-intelligence.md`),
§4. The relay itself stays stateless -- state lives in Alexa's
`session.attributes`, which ASK round-trips automatically. This module is
pure logic (no Flask, no network): it extracts/validates history coming
back from Alexa, trims it, builds the message list the LLM sees, appends
new turns, and packages history back up for the response. `app.py` is the
only caller.

Handles:
- Extracting conversation history from Alexa session.attributes
- Building multi-turn message lists for the LLM
- Trimming history to stay within token budget
- Appending new turns to history
- Validating and sanitizing conversation data
"""
import logging

logger = logging.getLogger(__name__)

_VALID_ROLES = ("user", "assistant")

DEFAULT_MAX_TURNS = 5


def extract_conversation_history(session_attributes):
    """Extract and validate conversation history from Alexa session.

    `session_attributes` is the raw `session.attributes` dict from an
    Alexa request (or None/missing on a first turn). Never raises --
    anything missing, malformed, or wrong-shaped is treated as "no usable
    history" or has the individual bad entry skipped (plan §5.4: "no need
    to validate history consistency -- just skip malformed entries").

    Returns: list of {"role", "content"} dicts, possibly empty.
    """
    if not isinstance(session_attributes, dict):
        return []
    raw_history = session_attributes.get("conversation_history")
    if not isinstance(raw_history, list):
        return []

    history = []
    for entry in raw_history:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        content = entry.get("content")
        if role not in _VALID_ROLES:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        history.append({"role": role, "content": content})
    return history


def trim_conversation_history(history, max_turns=DEFAULT_MAX_TURNS):
    """Keep only the last N complete user-assistant pairs (2*N messages).

    Logs a warning when trimming actually discards messages -- per plan
    §5.2, that's a signal something upstream isn't trimming before storing
    (the normal flow always hands this a history already at or under the
    cap).
    """
    history = history or []
    max_messages = max(0, max_turns) * 2
    if len(history) > max_messages:
        logger.warning(
            "conversation_history has %d messages, over the %d-turn cap "
            "(%d messages) -- trimming",
            len(history),
            max_turns,
            max_messages,
        )
    if max_messages == 0:
        return []
    return history[-max_messages:]


def build_messages_with_history(query, conversation_history=None, system_prompt=""):
    """Construct the full message list for an LLM call: system prompt,
    then any prior turns, then the current user query.

    Returns: [{role: system}, ...history..., {role: user, content: query}]
    """
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation_history or [])
    messages.append({"role": "user", "content": query})
    return messages


def append_to_history(history, user_query, assistant_response):
    """Add the new user/assistant turn to conversation history.

    Returns a new list (does not mutate `history`). `assistant_response`
    must already be the cleaned, spoken text (post
    `_strip_leaked_reasoning`/`_extract_spoken_text`) -- callers must never
    pass a canned fallback string here (plan §3.2): that would poison every
    future turn with a lie the model would treat as real prior context.
    """
    updated = list(history or [])
    updated.append({"role": "user", "content": user_query})
    updated.append({"role": "assistant", "content": assistant_response})
    return updated


def estimate_tokens(messages):
    """Rough token count for a message list, for monitoring only (plan
    §13). Uses the common ~4-chars-per-token heuristic -- not a real
    tokenizer, don't rely on it for anything that needs to be exact.
    """
    if not messages:
        return 0
    total_chars = sum(len(m.get("content") or "") for m in messages)
    return total_chars // 4


def should_clear_history(request_type):
    """Decide if history should be cleared (i.e. not carried forward) for
    this Alexa request type. LaunchRequest starts a deliberately fresh
    conversation (plan §2.4); SessionEndedRequest means there's no next
    turn to carry it to. Everything else (IntentRequest and friends)
    keeps history flowing.
    """
    return request_type in ("LaunchRequest", "SessionEndedRequest")


def session_attributes_from_history(history):
    """Package history back into the session.attributes shape Alexa
    expects in the response body. Always returns a dict (never None) --
    callers decide whether to pass it to `alexa_response` at all;
    `session_attributes_from_history([])` on a first-turn fallback is
    still a valid, explicit "no history yet" echo.
    """
    return {"conversation_history": history or []}
