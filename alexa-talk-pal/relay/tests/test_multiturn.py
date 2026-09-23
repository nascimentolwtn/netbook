"""Multi-turn / session.attributes integration tests for ../app.py
(docs/plans/0005-phase4-llm-conversational-intelligence.md).

Exercises the Flask app end-to-end via its test client with mock Alexa
request bodies, mocking only the LLM backend call (`app.ask_openrouter`)
and the daily-quota gate -- everything else (signature bypass via
DEBUG_SKIP_SIGNATURE, applicationId check, intent dispatch, session
attribute threading) runs for real.

Covers plan §2.6's table (every non-session-ending response path must
echo session.attributes -- unchanged, except the success path which
appends the new turn) and §2.5's AMAZON.FallbackIntent routing (option 3).

Run from relay/: `python3 -m unittest tests.test_multiturn -v` (or pytest).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import app as relay_app

TEST_SKILL_ID = "amzn1.ask.skill.test-skill-id"


def _alexa_request(request_body, session_attributes=None):
    body = {
        "version": "1.0",
        "session": {
            "new": False,
            "sessionId": "amzn1.echo-api.session.test",
            "application": {"applicationId": TEST_SKILL_ID},
            "attributes": session_attributes or {},
        },
        "request": request_body,
    }
    return body


def _launch_request():
    return _alexa_request({"type": "LaunchRequest", "requestId": "req-launch"})


def _intent_request(name, slots=None, session_attributes=None):
    intent = {"name": name}
    if slots is not None:
        intent["slots"] = slots
    return _alexa_request(
        {"type": "IntentRequest", "requestId": "req-intent", "intent": intent},
        session_attributes=session_attributes,
    )


def _ask_anything_request(query_text, session_attributes=None):
    slots = {"query": {"name": "query", "value": query_text}} if query_text else {"query": {"name": "query"}}
    return _intent_request(
        "AskAnythingIntent", slots=slots, session_attributes=session_attributes
    )


class MultiTurnTestCase(unittest.TestCase):
    """Common setup: signature bypass on, applicationId matched, quota
    always granted, real daily_counter.json never touched."""

    def setUp(self):
        self.client = relay_app.app.test_client()

        self._patches = [
            mock.patch.object(relay_app, "DEBUG_SKIP_SIGNATURE", True),
            mock.patch.object(relay_app, "ALEXA_SKILL_ID", TEST_SKILL_ID),
            mock.patch.object(relay_app, "check_and_increment_quota", return_value=True),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def post(self, body):
        return self.client.post("/alexa", json=body)


class LaunchRequestTests(MultiTurnTestCase):
    def test_launch_ignores_stored_history_and_omits_session_attributes(self):
        stored = {"conversation_history": [{"role": "user", "content": "old turn"}]}
        body = _launch_request()
        body["session"]["attributes"] = stored
        resp = self.post(body)
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.LAUNCH_GREETING)
        self.assertFalse(data["response"]["shouldEndSession"])
        self.assertNotIn("sessionAttributes", data)


class AskAnythingSuccessTests(MultiTurnTestCase):
    def test_first_turn_no_prior_history_appends_new_turn(self):
        with mock.patch.object(relay_app, "ask_openrouter", return_value="Paris is the capital."):
            resp = self.post(_ask_anything_request("What's the capital of France?"))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], "Paris is the capital.")
        self.assertFalse(data["response"]["shouldEndSession"])
        history = data["sessionAttributes"]["conversation_history"]
        self.assertEqual(
            history,
            [
                {"role": "user", "content": "What's the capital of France?"},
                {"role": "assistant", "content": "Paris is the capital."},
            ],
        )

    def test_second_turn_appends_onto_existing_history(self):
        prior = {
            "conversation_history": [
                {"role": "user", "content": "What's the capital of France?"},
                {"role": "assistant", "content": "Paris."},
            ]
        }
        with mock.patch.object(relay_app, "ask_openrouter", return_value="It's known for the Eiffel Tower."):
            resp = self.post(_ask_anything_request("Tell me more about it.", session_attributes=prior))
        data = resp.get_json()
        history = data["sessionAttributes"]["conversation_history"]
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0], {"role": "user", "content": "What's the capital of France?"})
        self.assertEqual(history[2], {"role": "user", "content": "Tell me more about it."})
        self.assertEqual(history[3], {"role": "assistant", "content": "It's known for the Eiffel Tower."})

    def test_history_passed_to_llm_call(self):
        prior = {
            "conversation_history": [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
            ]
        }
        with mock.patch.object(relay_app, "ask_openrouter", return_value="a2") as mock_ask:
            self.post(_ask_anything_request("q2", session_attributes=prior))
        (messages_arg,), _ = mock_ask.call_args
        self.assertEqual(
            messages_arg,
            [
                {"role": "system", "content": relay_app.CONVERSATIONAL_SYSTEM_PROMPT},
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
            ],
        )


class NoQueryTextTests(MultiTurnTestCase):
    def test_empty_slot_echoes_history_unchanged(self):
        prior = {"conversation_history": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}
        resp = self.post(_ask_anything_request(None, session_attributes=prior))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.NO_QUERY_TEXT)
        self.assertFalse(data["response"]["shouldEndSession"])
        self.assertEqual(data["sessionAttributes"], prior)

    def test_empty_slot_first_turn_echoes_empty_history(self):
        resp = self.post(_ask_anything_request(None))
        data = resp.get_json()
        self.assertEqual(data["sessionAttributes"], {"conversation_history": []})


class RateLimitFallbackTests(MultiTurnTestCase):
    def test_rate_limit_echoes_history_unchanged_and_does_not_end_session(self):
        prior = {"conversation_history": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}
        with mock.patch.object(relay_app, "ask_openrouter", side_effect=relay_app.OpenRouterRateLimited()):
            resp = self.post(_ask_anything_request("q2", session_attributes=prior))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.RATE_LIMIT_FALLBACK)
        self.assertFalse(data["response"]["shouldEndSession"])
        self.assertEqual(data["sessionAttributes"], prior)


class TimeoutFallbackTests(MultiTurnTestCase):
    def test_timeout_echoes_history_unchanged(self):
        prior = {"conversation_history": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}
        with mock.patch.object(relay_app, "ask_openrouter", side_effect=requests.exceptions.Timeout()):
            resp = self.post(_ask_anything_request("q2", session_attributes=prior))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.TIMEOUT_FALLBACK)
        self.assertFalse(data["response"]["shouldEndSession"])
        self.assertEqual(data["sessionAttributes"], prior)


class GenericErrorFallbackTests(MultiTurnTestCase):
    def test_unexpected_exception_echoes_history_unchanged(self):
        prior = {"conversation_history": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}
        with mock.patch.object(relay_app, "ask_openrouter", side_effect=ValueError("boom")):
            resp = self.post(_ask_anything_request("q2", session_attributes=prior))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.GENERIC_ERROR_FALLBACK)
        self.assertFalse(data["response"]["shouldEndSession"])
        self.assertEqual(data["sessionAttributes"], prior)

    def test_fallback_never_appended_to_history_as_assistant_turn(self):
        """A quota/timeout/error turn must never poison history with a
        canned fallback line masquerading as a real answer (plan §3.2)."""
        prior = {"conversation_history": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}
        with mock.patch.object(relay_app, "ask_openrouter", side_effect=requests.exceptions.Timeout()):
            resp = self.post(_ask_anything_request("q2", session_attributes=prior))
        data = resp.get_json()
        contents = [m["content"] for m in data["sessionAttributes"]["conversation_history"]]
        self.assertNotIn(relay_app.TIMEOUT_FALLBACK, contents)
        self.assertNotIn("q2", contents)  # the failed query itself isn't appended either


class HelpIntentTests(MultiTurnTestCase):
    def test_help_echoes_history_unchanged(self):
        prior = {"conversation_history": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}
        resp = self.post(_intent_request("AMAZON.HelpIntent", session_attributes=prior))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.HELP_TEXT)
        self.assertFalse(data["response"]["shouldEndSession"])
        self.assertEqual(data["sessionAttributes"], prior)


class UnrecognizedIntentTests(MultiTurnTestCase):
    def test_unrecognized_intent_echoes_history_unchanged(self):
        prior = {"conversation_history": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}
        resp = self.post(_intent_request("SomeUnknownIntent", session_attributes=prior))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.GENERIC_ERROR_FALLBACK)
        self.assertFalse(data["response"]["shouldEndSession"])
        self.assertEqual(data["sessionAttributes"], prior)


class SessionEndingTests(MultiTurnTestCase):
    def test_stop_intent_ends_session_no_session_attributes_needed(self):
        prior = {"conversation_history": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}
        resp = self.post(_intent_request("AMAZON.StopIntent", session_attributes=prior))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.GOODBYE_TEXT)
        self.assertTrue(data["response"]["shouldEndSession"])

    def test_cancel_intent_ends_session(self):
        resp = self.post(_intent_request("AMAZON.CancelIntent"))
        data = resp.get_json()
        self.assertTrue(data["response"]["shouldEndSession"])

    def test_quota_exhausted_ends_session_regardless_of_history(self):
        prior = {"conversation_history": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}
        with mock.patch.object(relay_app, "check_and_increment_quota", return_value=False):
            resp = self.post(_ask_anything_request("q2", session_attributes=prior))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.QUOTA_EXHAUSTED_FALLBACK)
        self.assertTrue(data["response"]["shouldEndSession"])


class FallbackIntentTests(MultiTurnTestCase):
    def test_fallback_intent_with_no_history_gets_no_query_text(self):
        """No stored history means there's nothing to continue -- can't be
        a follow-up to anything (plan §2.5 option 3)."""
        resp = self.post(_intent_request("AMAZON.FallbackIntent"))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.NO_QUERY_TEXT)
        self.assertFalse(data["response"]["shouldEndSession"])
        self.assertEqual(data["sessionAttributes"], {"conversation_history": []})

    def test_fallback_intent_with_history_routes_to_llm_using_continuation_query(self):
        prior = {
            "conversation_history": [
                {"role": "user", "content": "What's the capital of France?"},
                {"role": "assistant", "content": "Paris."},
            ]
        }
        with mock.patch.object(relay_app, "ask_openrouter", return_value="Paris is on the Seine river.") as mock_ask:
            resp = self.post(_intent_request("AMAZON.FallbackIntent", session_attributes=prior))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], "Paris is on the Seine river.")
        self.assertFalse(data["response"]["shouldEndSession"])

        (messages_arg,), _ = mock_ask.call_args
        # The synthetic continuation query, not a real user utterance
        # (FallbackIntent never carries one), gets sent as the current turn.
        self.assertEqual(messages_arg[-1], {"role": "user", "content": relay_app.FALLBACK_CONTINUATION_QUERY})

        history = data["sessionAttributes"]["conversation_history"]
        self.assertEqual(len(history), 4)
        self.assertEqual(history[2], {"role": "user", "content": relay_app.FALLBACK_CONTINUATION_QUERY})
        self.assertEqual(history[3], {"role": "assistant", "content": "Paris is on the Seine river."})

    def test_fallback_intent_error_with_history_echoes_it_unchanged(self):
        prior = {"conversation_history": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}
        with mock.patch.object(relay_app, "ask_openrouter", side_effect=requests.exceptions.Timeout()):
            resp = self.post(_intent_request("AMAZON.FallbackIntent", session_attributes=prior))
        data = resp.get_json()
        self.assertEqual(data["response"]["outputSpeech"]["text"], relay_app.TIMEOUT_FALLBACK)
        self.assertEqual(data["sessionAttributes"], prior)


class HistoryTrimmingTests(MultiTurnTestCase):
    def test_history_over_five_turns_trimmed_before_llm_call(self):
        long_history = []
        for i in range(7):  # 7 turns = 14 messages, over the 5-turn/10-message cap
            long_history.append({"role": "user", "content": "q{}".format(i)})
            long_history.append({"role": "assistant", "content": "a{}".format(i)})
        prior = {"conversation_history": long_history}

        with mock.patch.object(relay_app, "ask_openrouter", return_value="final answer") as mock_ask:
            resp = self.post(_ask_anything_request("newest question", session_attributes=prior))

        (messages_arg,), _ = mock_ask.call_args
        # system + 10 trimmed history messages + current query = 12
        self.assertEqual(len(messages_arg), 12)
        # Oldest surviving turn should be q2/a2 (turns 0 and 1 dropped).
        self.assertEqual(messages_arg[1], {"role": "user", "content": "q2"})

        data = resp.get_json()
        # Trimmed to 10, then this turn's user+assistant messages appended
        # -- 12 stored messages (one turn over the 5-turn cap). The *next*
        # request's extract+trim brings it back down to 10; append_to_history
        # itself doesn't re-trim (plan §2.4 only lists a trim step before
        # building messages, not after appending).
        self.assertEqual(len(data["sessionAttributes"]["conversation_history"]), 12)


class RepromptTests(MultiTurnTestCase):
    """Without a `reprompt`, `shouldEndSession: false` opens the mic but
    Alexa has nothing to say if it doesn't hear anything -- the session
    times out silently (~8s) instead of re-asking, indistinguishable from
    the skill having exited (Plan 0002 §10 follow-up (c)). Every
    non-session-ending response must carry one; every session-ending
    response must not."""

    def test_launch_includes_reprompt(self):
        resp = self.post(_launch_request())
        data = resp.get_json()
        self.assertEqual(data["response"]["reprompt"]["outputSpeech"]["text"], relay_app.REPROMPT_TEXT)

    def test_ask_anything_success_includes_reprompt(self):
        with mock.patch.object(relay_app, "ask_openrouter", return_value="Paris."):
            resp = self.post(_ask_anything_request("What's the capital of France?"))
        data = resp.get_json()
        self.assertEqual(data["response"]["reprompt"]["outputSpeech"]["text"], relay_app.REPROMPT_TEXT)

    def test_no_query_text_includes_reprompt(self):
        resp = self.post(_ask_anything_request(None))
        data = resp.get_json()
        self.assertEqual(data["response"]["reprompt"]["outputSpeech"]["text"], relay_app.REPROMPT_TEXT)

    def test_help_includes_reprompt(self):
        resp = self.post(_intent_request("AMAZON.HelpIntent"))
        data = resp.get_json()
        self.assertEqual(data["response"]["reprompt"]["outputSpeech"]["text"], relay_app.REPROMPT_TEXT)

    def test_fallback_intent_no_history_includes_reprompt(self):
        resp = self.post(_intent_request("AMAZON.FallbackIntent"))
        data = resp.get_json()
        self.assertEqual(data["response"]["reprompt"]["outputSpeech"]["text"], relay_app.REPROMPT_TEXT)

    def test_unrecognized_intent_includes_reprompt(self):
        resp = self.post(_intent_request("SomeUnknownIntent"))
        data = resp.get_json()
        self.assertEqual(data["response"]["reprompt"]["outputSpeech"]["text"], relay_app.REPROMPT_TEXT)

    def test_timeout_fallback_includes_reprompt(self):
        with mock.patch.object(relay_app, "ask_openrouter", side_effect=requests.exceptions.Timeout()):
            resp = self.post(_ask_anything_request("q"))
        data = resp.get_json()
        self.assertEqual(data["response"]["reprompt"]["outputSpeech"]["text"], relay_app.REPROMPT_TEXT)

    def test_stop_intent_has_no_reprompt(self):
        resp = self.post(_intent_request("AMAZON.StopIntent"))
        data = resp.get_json()
        self.assertNotIn("reprompt", data["response"])

    def test_quota_exhausted_has_no_reprompt(self):
        with mock.patch.object(relay_app, "check_and_increment_quota", return_value=False):
            resp = self.post(_ask_anything_request("q"))
        data = resp.get_json()
        self.assertNotIn("reprompt", data["response"])


if __name__ == "__main__":
    unittest.main()
