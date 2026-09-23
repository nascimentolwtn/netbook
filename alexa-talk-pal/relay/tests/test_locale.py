"""Tests for pt-BR locale support in ../app.py and ../fallback_messages.py
(docs/plans/0004-phase4-portuguese-br-locale-support.md).

Scope: locale extraction/normalization (§2.2/§6 Phase 1.2), locale-aware
response text and fallback messages (§6 Phase 1.1/1.3), and a
language-compliance heuristic (§7) that would fail if a pt-BR request came
back in English. `test_signature.py` stays scoped to signature
verification only -- this is the separate file the plan calls for.

Single-turn only, per the plan's actual implementation here: `ask_llm`
takes `(query, locale)`, no `conversation_history` (that's Plan 0005's
territory, built on a different branch against the same unmodified
baseline -- see the plan's §11 and this repo's deviation note).

stdlib unittest only, no network -- LLM backend calls are mocked.
Run from relay/: `python3 -m unittest tests.test_locale -v`
"""
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as relay_app
from fallback_messages import get_messages


# ---------------------------------------------------------------------------
# Language-compliance heuristic (docs/plans/0004 §7) -- not linguistically
# rigorous, just enough to catch the failure mode the plan calls out:
# routing correctness (right system prompt selected) is not the same as
# language correctness (the model/text actually being Portuguese). Presence
# of common Portuguese stopwords/diacritics, absence of common English
# stopwords.
# ---------------------------------------------------------------------------
_PT_STOPWORDS = {
    "voce", "você", "nao", "não", "que", "para", "com", "por", "uma", "um",
    "seu", "sua", "esta", "está", "isso", "sao", "são", "pergunta",
    "perguntar", "ajudar", "desculpe", "hoje", "amanha", "amanhã", "tente",
    "gostaria", "muitas", "agora", "daqui", "minuto", "cerebro", "cérebro",
}
_PT_DIACRITIC_CHARS = "áàâãéêíóôõúüç"
_EN_STOPWORDS = {
    "the", "you", "your", "with", "sorry", "please", "what", "would",
    "like", "ask", "again", "today", "tomorrow", "couldn't", "didn't",
    "minute", "brain", "reach", "just", "catch", "question", "goodbye",
}


def _looks_portuguese(text):
    lowered = (text or "").lower()
    words = set(re.findall(r"[a-zà-ÿ']+", lowered))
    has_diacritic = any(ch in lowered for ch in _PT_DIACRITIC_CHARS)
    has_pt_stopword = bool(words & _PT_STOPWORDS)
    has_en_stopword = bool(words & _EN_STOPWORDS)
    return (has_diacritic or has_pt_stopword) and not has_en_stopword


# ---------------------------------------------------------------------------
# _normalize_locale / _extract_locale
# ---------------------------------------------------------------------------
class NormalizeLocaleTests(unittest.TestCase):
    def test_hyphenated_pt_br_normalizes(self):
        # The real Alexa wire format (BCP-47, hyphenated).
        self.assertEqual(relay_app._normalize_locale("pt-BR"), "pt_BR")

    def test_hyphenated_en_us_normalizes(self):
        self.assertEqual(relay_app._normalize_locale("en-US"), "en_US")

    def test_underscore_form_also_accepted(self):
        self.assertEqual(relay_app._normalize_locale("pt_BR"), "pt_BR")

    def test_case_insensitive(self):
        self.assertEqual(relay_app._normalize_locale("PT-br"), "pt_BR")

    def test_missing_defaults_to_en_us(self):
        self.assertEqual(relay_app._normalize_locale(None), relay_app.DEFAULT_LOCALE)
        self.assertEqual(relay_app._normalize_locale(""), relay_app.DEFAULT_LOCALE)

    def test_unsupported_locale_defaults_to_en_us(self):
        # Real BCP-47 locale, just not one this relay has content for.
        self.assertEqual(relay_app._normalize_locale("fr-FR"), relay_app.DEFAULT_LOCALE)

    def test_malformed_input_defaults_to_en_us(self):
        self.assertEqual(relay_app._normalize_locale(12345), relay_app.DEFAULT_LOCALE)
        self.assertEqual(relay_app._normalize_locale({}), relay_app.DEFAULT_LOCALE)


class ExtractLocaleTests(unittest.TestCase):
    def test_valid_pt_br(self):
        body = {"request": {"type": "LaunchRequest", "locale": "pt-BR"}}
        self.assertEqual(relay_app._extract_locale(body), "pt_BR")

    def test_valid_en_us(self):
        body = {"request": {"type": "LaunchRequest", "locale": "en-US"}}
        self.assertEqual(relay_app._extract_locale(body), "en_US")

    def test_missing_locale_field(self):
        body = {"request": {"type": "LaunchRequest"}}
        self.assertEqual(relay_app._extract_locale(body), relay_app.DEFAULT_LOCALE)

    def test_missing_request_object(self):
        self.assertEqual(relay_app._extract_locale({}), relay_app.DEFAULT_LOCALE)

    def test_malformed_request_object(self):
        # "request" present but not a dict -- must not raise.
        self.assertEqual(relay_app._extract_locale({"request": "oops"}), relay_app.DEFAULT_LOCALE)


# ---------------------------------------------------------------------------
# _get_response_text / RESPONSE_TEXTS
# ---------------------------------------------------------------------------
class GetResponseTextTests(unittest.TestCase):
    def test_en_us_help(self):
        self.assertEqual(
            relay_app._get_response_text("HELP", "en_US"),
            relay_app.RESPONSE_TEXTS["en_US"]["HELP"],
        )

    def test_pt_br_help_differs_from_en_us(self):
        pt = relay_app._get_response_text("HELP", "pt_BR")
        en = relay_app._get_response_text("HELP", "en_US")
        self.assertNotEqual(pt, en)
        self.assertTrue(_looks_portuguese(pt))

    def test_all_keys_present_for_both_locales(self):
        en_keys = set(relay_app.RESPONSE_TEXTS["en_US"])
        pt_keys = set(relay_app.RESPONSE_TEXTS["pt_BR"])
        self.assertEqual(en_keys, pt_keys)
        for key in en_keys:
            with self.subTest(key=key):
                self.assertTrue(relay_app._get_response_text(key, "pt_BR"))

    def test_unknown_locale_falls_back_to_en_us(self):
        self.assertEqual(
            relay_app._get_response_text("GOODBYE", "fr_FR"),
            relay_app.RESPONSE_TEXTS["en_US"]["GOODBYE"],
        )


# ---------------------------------------------------------------------------
# fallback_messages.get_messages
# ---------------------------------------------------------------------------
class GetMessagesTests(unittest.TestCase):
    def test_en_us(self):
        messages = get_messages("en_US")
        self.assertEqual(messages["GENERIC_ERROR"], "Sorry, I couldn't reach my brain just now. Try again?")

    def test_pt_br(self):
        messages = get_messages("pt_BR")
        self.assertNotEqual(messages["GENERIC_ERROR"], get_messages("en_US")["GENERIC_ERROR"])
        for key, text in messages.items():
            with self.subTest(key=key):
                self.assertTrue(_looks_portuguese(text), text)

    def test_unknown_locale_falls_back_to_en_us(self):
        self.assertEqual(get_messages("xx_YY"), get_messages("en_US"))

    def test_same_keys_both_locales(self):
        self.assertEqual(set(get_messages("en_US")), set(get_messages("pt_BR")))


# ---------------------------------------------------------------------------
# Language-compliance heuristic applied to this relay's own pt-BR content --
# real regression protection: catches an accidental English string sneaking
# into the pt_BR table, without needing a live LLM call.
# ---------------------------------------------------------------------------
class PortugueseContentComplianceTests(unittest.TestCase):
    def test_pt_br_response_texts_look_portuguese(self):
        for key, text in relay_app.RESPONSE_TEXTS["pt_BR"].items():
            with self.subTest(key=key):
                self.assertTrue(_looks_portuguese(text), text)

    def test_pt_br_system_prompt_looks_portuguese(self):
        self.assertTrue(_looks_portuguese(relay_app.SYSTEM_PROMPTS["pt_BR"]))

    def test_en_us_system_prompt_does_not_look_portuguese(self):
        self.assertFalse(_looks_portuguese(relay_app.SYSTEM_PROMPTS["en_US"]))

    def test_heuristic_would_catch_an_english_reply_mislabeled_pt_br(self):
        # Sanity check on the heuristic itself: a plainly English sentence
        # must not pass as Portuguese.
        self.assertFalse(_looks_portuguese("Sorry, I couldn't reach my brain just now."))

    def test_heuristic_accepts_real_portuguese_sentence(self):
        self.assertTrue(
            _looks_portuguese("O céu é azul por causa da dispersão da luz do sol na atmosfera.")
        )


# ---------------------------------------------------------------------------
# Full /alexa POST, both locales
# ---------------------------------------------------------------------------
class AlexaEndpointLocaleTests(unittest.TestCase):
    SKILL_ID = "amzn1.ask.skill.test-locale-suite"

    def setUp(self):
        self.client = relay_app.app.test_client()
        self._patches = [
            mock.patch.object(relay_app, "DEBUG_SKIP_SIGNATURE", True),
            mock.patch.object(relay_app, "ALEXA_SKILL_ID", self.SKILL_ID),
            # Quota bookkeeping (file I/O, cross-test counter state) is out
            # of scope for locale tests -- always allow through here.
            mock.patch.object(relay_app, "check_and_increment_quota", return_value=True),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _body(self, request_type, locale, intent=None):
        body = {
            "version": "1.0",
            "session": {"application": {"applicationId": self.SKILL_ID}},
            "request": {"type": request_type, "locale": locale},
        }
        if intent is not None:
            body["request"]["intent"] = intent
        return body

    def _post(self, body):
        return self.client.post("/alexa", json=body)

    def _speech(self, resp):
        return resp.get_json()["response"]["outputSpeech"]["text"]

    # -- LaunchRequest -------------------------------------------------
    def test_launch_en_us(self):
        resp = self._post(self._body("LaunchRequest", "en-US"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._speech(resp), relay_app.RESPONSE_TEXTS["en_US"]["LAUNCH"])

    def test_launch_pt_br(self):
        resp = self._post(self._body("LaunchRequest", "pt-BR"))
        self.assertEqual(resp.status_code, 200)
        text = self._speech(resp)
        self.assertEqual(text, relay_app.RESPONSE_TEXTS["pt_BR"]["LAUNCH"])
        self.assertTrue(_looks_portuguese(text))

    def test_launch_missing_locale_defaults_english(self):
        body = self._body("LaunchRequest", None)
        del body["request"]["locale"]
        resp = self._post(body)
        self.assertEqual(self._speech(resp), relay_app.RESPONSE_TEXTS["en_US"]["LAUNCH"])

    # -- AMAZON.HelpIntent ----------------------------------------------
    def test_help_pt_br(self):
        intent = {"name": "AMAZON.HelpIntent"}
        resp = self._post(self._body("IntentRequest", "pt-BR", intent))
        text = self._speech(resp)
        self.assertEqual(text, relay_app.RESPONSE_TEXTS["pt_BR"]["HELP"])
        self.assertTrue(_looks_portuguese(text))

    # -- AMAZON.StopIntent / CancelIntent --------------------------------
    def test_stop_pt_br_ends_session(self):
        intent = {"name": "AMAZON.StopIntent"}
        resp = self._post(self._body("IntentRequest", "pt-BR", intent))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.RESPONSE_TEXTS["pt_BR"]["GOODBYE"])
        self.assertTrue(data["response"]["shouldEndSession"])

    # -- AskAnythingIntent: no query slot value --------------------------
    def test_ask_anything_no_query_pt_br(self):
        intent = {"name": "AskAnythingIntent", "slots": {"query": {"value": ""}}}
        resp = self._post(self._body("IntentRequest", "pt-BR", intent))
        text = self._speech(resp)
        self.assertEqual(text, relay_app.RESPONSE_TEXTS["pt_BR"]["NO_QUERY"])
        self.assertTrue(_looks_portuguese(text))

    # -- AskAnythingIntent: happy path, locale threaded to ask_llm -------
    #
    # ask_llm returns (answer_text, updated_history) -- the combined
    # signature from reconciling this plan with Plan 0005's multi-turn
    # work (see app.py's module docstring and docs/adr/0016+0017). These
    # requests carry no session.attributes, so current_history is [].
    def test_ask_anything_success_passes_locale_to_ask_llm(self):
        intent = {"name": "AskAnythingIntent", "slots": {"query": {"value": "por que o céu é azul"}}}
        with mock.patch.object(
            relay_app,
            "ask_llm",
            return_value=("O céu é azul por causa da luz do sol.", []),
        ) as mocked:
            resp = self._post(self._body("IntentRequest", "pt-BR", intent))
        mocked.assert_called_once_with("por que o céu é azul", locale="pt_BR", conversation_history=[])
        text = self._speech(resp)
        self.assertTrue(_looks_portuguese(text))

    def test_ask_anything_success_en_us(self):
        intent = {"name": "AskAnythingIntent", "slots": {"query": {"value": "why is the sky blue"}}}
        with mock.patch.object(
            relay_app,
            "ask_llm",
            return_value=("Because of how sunlight scatters.", []),
        ) as mocked:
            resp = self._post(self._body("IntentRequest", "en-US", intent))
        mocked.assert_called_once_with("why is the sky blue", locale="en_US", conversation_history=[])
        self.assertEqual(self._speech(resp), "Because of how sunlight scatters.")

    # -- Language-compliance failure mode (§7): a pt-BR request must not
    # silently pass if the "LLM" (mocked here) answers in English despite
    # the Portuguese system prompt. This test documents/asserts that the
    # heuristic *would* catch that -- it does not by itself prove a real
    # model won't do this; that's Phase 3 live-Echo testing (out of scope
    # here, see plan §Phase 3 and docs/plans/0004's "Out of scope" note).
    def test_language_compliance_heuristic_flags_english_reply_to_pt_br_request(self):
        intent = {"name": "AskAnythingIntent", "slots": {"query": {"value": "por que o céu é azul"}}}
        with mock.patch.object(
            relay_app,
            "ask_llm",
            return_value=("The sky is blue because of Rayleigh scattering.", []),
        ):
            resp = self._post(self._body("IntentRequest", "pt-BR", intent))
        text = self._speech(resp)
        self.assertFalse(
            _looks_portuguese(text),
            "heuristic should flag this as non-Portuguese, proving it would catch the leak",
        )

    # -- Fallback paths, both locales ------------------------------------
    def test_rate_limit_fallback_pt_br(self):
        intent = {"name": "AskAnythingIntent", "slots": {"query": {"value": "oi"}}}
        with mock.patch.object(relay_app, "ask_llm", side_effect=relay_app.OpenRouterRateLimited()):
            resp = self._post(self._body("IntentRequest", "pt-BR", intent))
        text = self._speech(resp)
        self.assertEqual(text, relay_app.RESPONSE_TEXTS["pt_BR"]["RATE_LIMIT"])
        self.assertTrue(_looks_portuguese(text))

    def test_timeout_fallback_pt_br(self):
        import requests

        intent = {"name": "AskAnythingIntent", "slots": {"query": {"value": "oi"}}}
        with mock.patch.object(relay_app, "ask_llm", side_effect=requests.exceptions.Timeout()):
            resp = self._post(self._body("IntentRequest", "pt-BR", intent))
        text = self._speech(resp)
        self.assertEqual(text, get_messages("pt_BR")["TIMEOUT"])
        self.assertTrue(_looks_portuguese(text))

    def test_generic_error_fallback_pt_br(self):
        intent = {"name": "AskAnythingIntent", "slots": {"query": {"value": "oi"}}}
        with mock.patch.object(relay_app, "ask_llm", side_effect=RuntimeError("boom")):
            resp = self._post(self._body("IntentRequest", "pt-BR", intent))
        text = self._speech(resp)
        self.assertEqual(text, get_messages("pt_BR")["GENERIC_ERROR"])
        self.assertTrue(_looks_portuguese(text))

    def test_quota_exhausted_fallback_pt_br(self):
        intent = {"name": "AskAnythingIntent", "slots": {"query": {"value": "oi"}}}
        with mock.patch.object(relay_app, "check_and_increment_quota", return_value=False):
            resp = self._post(self._body("IntentRequest", "pt-BR", intent))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], get_messages("pt_BR")["QUOTA_EXHAUSTED"])
        self.assertTrue(data["response"]["shouldEndSession"])

    # -- Unknown intent falls back to locale-appropriate generic error ---
    def test_unknown_intent_pt_br(self):
        intent = {"name": "SomeUnhandledIntent"}
        resp = self._post(self._body("IntentRequest", "pt-BR", intent))
        text = self._speech(resp)
        self.assertEqual(text, get_messages("pt_BR")["GENERIC_ERROR"])
        self.assertTrue(_looks_portuguese(text))


if __name__ == "__main__":
    unittest.main()
