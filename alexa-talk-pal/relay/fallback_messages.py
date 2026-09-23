"""Locale-aware fallback messages for the relay's LLM-failure paths
(quota exhausted, timeout, generic error). See docs/plans/0004 §6 Phase 1.1.

`get_messages(locale)` is the intended entry point -- `locale` is the
relay's normalized internal key (e.g. "en_US", "pt_BR"), as produced by
app.py's `_extract_locale`/`_normalize_locale`, not Alexa's raw hyphenated
wire format ("en-US"/"pt-BR"). Unrecognized locales fall back to en_US.

The bare `*_MESSAGE` constants below are kept only as an en_US-only
backward-compat surface for any external caller that imported them
directly before locale support existed; new code should go through
`get_messages()`.
"""

MESSAGES = {
    "en_US": {
        "GENERIC_ERROR": "Sorry, I couldn't reach my brain just now. Try again?",
        "QUOTA_EXHAUSTED": "I've used up my questions for today. Ask me again tomorrow.",
        "TIMEOUT": "Sorry, that took me too long to think through. Mind trying again?",
    },
    "pt_BR": {
        "GENERIC_ERROR": "Desculpe, não consegui pensar direito agora. Tente de novo?",
        "QUOTA_EXHAUSTED": "Já usei minhas perguntas de hoje. Me pergunte de novo amanhã.",
        "TIMEOUT": "Desculpe, demorei demais para pensar nisso. Pode tentar de novo?",
    },
}


def get_messages(locale):
    """Returns the message dict for `locale` (already-normalized internal
    key), falling back to en_US for anything unrecognized."""
    return MESSAGES.get(locale, MESSAGES["en_US"])


# Back-compat module-level constants (en_US only).
GENERIC_ERROR_MESSAGE = MESSAGES["en_US"]["GENERIC_ERROR"]
QUOTA_EXHAUSTED_MESSAGE = MESSAGES["en_US"]["QUOTA_EXHAUSTED"]
TIMEOUT_MESSAGE = MESSAGES["en_US"]["TIMEOUT"]
