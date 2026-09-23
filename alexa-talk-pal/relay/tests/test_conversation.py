"""Tests for ../conversation.py (docs/plans/0005-phase4-llm-conversational-intelligence.md).

stdlib unittest only, no network -- pure-function module, no Flask app
needed. Run from relay/: `python3 -m unittest tests.test_conversation -v`
(or `pytest` from relay/).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conversation as conv


def _pair(n):
    """n complete user/assistant pairs as a flat message list, content
    tagged with the pair index so trim/order bugs are visible in
    assertions."""
    messages = []
    for i in range(n):
        messages.append({"role": "user", "content": "user question {}".format(i)})
        messages.append({"role": "assistant", "content": "assistant answer {}".format(i)})
    return messages


class ExtractConversationHistoryTests(unittest.TestCase):
    def test_missing_session_attributes_returns_empty(self):
        self.assertEqual(conv.extract_conversation_history(None), [])
        self.assertEqual(conv.extract_conversation_history({}), [])

    def test_non_dict_session_attributes_returns_empty(self):
        self.assertEqual(conv.extract_conversation_history("not a dict"), [])
        self.assertEqual(conv.extract_conversation_history(["also not a dict"]), [])

    def test_missing_conversation_history_key_returns_empty(self):
        self.assertEqual(conv.extract_conversation_history({"other_key": "value"}), [])

    def test_non_list_conversation_history_returns_empty(self):
        self.assertEqual(
            conv.extract_conversation_history({"conversation_history": "not a list"}), []
        )
        self.assertEqual(
            conv.extract_conversation_history({"conversation_history": {"a": 1}}), []
        )

    def test_valid_history_round_trips(self):
        history = [
            {"role": "user", "content": "What is the capital of France?"},
            {"role": "assistant", "content": "The capital of France is Paris."},
        ]
        result = conv.extract_conversation_history({"conversation_history": history})
        self.assertEqual(result, history)

    def test_skips_malformed_entries(self):
        raw = [
            {"role": "user", "content": "good entry"},
            "not a dict",
            {"role": "bogus_role", "content": "bad role"},
            {"role": "user"},  # missing content
            {"content": "missing role"},
            {"role": "assistant", "content": ""},  # empty content
            {"role": "assistant", "content": "   "},  # whitespace-only content
            {"role": "assistant", "content": 12345},  # non-string content
            {"role": "assistant", "content": "another good entry"},
        ]
        result = conv.extract_conversation_history({"conversation_history": raw})
        self.assertEqual(
            result,
            [
                {"role": "user", "content": "good entry"},
                {"role": "assistant", "content": "another good entry"},
            ],
        )

    def test_never_raises_on_garbage(self):
        garbage_inputs = [
            None,
            {},
            {"conversation_history": None},
            {"conversation_history": [None, 1, 2.5, True, "str"]},
            {"conversation_history": [{}]},
        ]
        for g in garbage_inputs:
            with self.subTest(g=g):
                conv.extract_conversation_history(g)  # must not raise


class TrimConversationHistoryTests(unittest.TestCase):
    def test_none_history_returns_empty(self):
        self.assertEqual(conv.trim_conversation_history(None), [])

    def test_empty_history_returns_empty(self):
        self.assertEqual(conv.trim_conversation_history([]), [])

    def test_under_cap_unchanged(self):
        history = _pair(3)
        self.assertEqual(conv.trim_conversation_history(history, max_turns=5), history)

    def test_exactly_at_cap_unchanged(self):
        history = _pair(5)
        self.assertEqual(conv.trim_conversation_history(history, max_turns=5), history)

    def test_over_cap_keeps_last_n_turns(self):
        history = _pair(7)  # 14 messages
        trimmed = conv.trim_conversation_history(history, max_turns=5)
        self.assertEqual(len(trimmed), 10)
        # Must keep the most recent turns (2..6), not the oldest.
        self.assertEqual(trimmed, _pair(7)[-10:])
        self.assertEqual(trimmed[0]["content"], "user question 2")
        self.assertEqual(trimmed[-1]["content"], "assistant answer 6")

    def test_zero_max_turns_returns_empty(self):
        history = _pair(3)
        self.assertEqual(conv.trim_conversation_history(history, max_turns=0), [])

    def test_default_max_turns_is_five(self):
        history = _pair(6)
        trimmed = conv.trim_conversation_history(history)
        self.assertEqual(len(trimmed), 10)


class BuildMessagesWithHistoryTests(unittest.TestCase):
    def test_no_history_gives_system_plus_user(self):
        messages = conv.build_messages_with_history(
            "What's the weather?", conversation_history=None, system_prompt="SYS"
        )
        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "What's the weather?"},
            ],
        )

    def test_empty_list_history_same_as_none(self):
        messages = conv.build_messages_with_history(
            "query", conversation_history=[], system_prompt="SYS"
        )
        self.assertEqual(
            messages, [{"role": "system", "content": "SYS"}, {"role": "user", "content": "query"}]
        )

    def test_history_inserted_between_system_and_current_query(self):
        history = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ]
        messages = conv.build_messages_with_history(
            "follow-up question", conversation_history=history, system_prompt="SYS"
        )
        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
                {"role": "user", "content": "follow-up question"},
            ],
        )

    def test_does_not_mutate_input_history(self):
        history = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
        original_len = len(history)
        conv.build_messages_with_history("new query", conversation_history=history, system_prompt="SYS")
        self.assertEqual(len(history), original_len)


class AppendToHistoryTests(unittest.TestCase):
    def test_append_to_empty_history(self):
        result = conv.append_to_history([], "What's Pi?", "Pi is about 3.14.")
        self.assertEqual(
            result,
            [
                {"role": "user", "content": "What's Pi?"},
                {"role": "assistant", "content": "Pi is about 3.14."},
            ],
        )

    def test_append_to_none_history(self):
        result = conv.append_to_history(None, "q", "a")
        self.assertEqual(result, [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}])

    def test_append_preserves_prior_turns(self):
        history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
        result = conv.append_to_history(history, "q2", "a2")
        self.assertEqual(
            result,
            [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
                {"role": "assistant", "content": "a2"},
            ],
        )

    def test_does_not_mutate_input_history(self):
        history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
        original = list(history)
        conv.append_to_history(history, "q2", "a2")
        self.assertEqual(history, original)


class EstimateTokensTests(unittest.TestCase):
    def test_empty_messages_zero_tokens(self):
        self.assertEqual(conv.estimate_tokens([]), 0)
        self.assertEqual(conv.estimate_tokens(None), 0)

    def test_rough_four_chars_per_token(self):
        messages = [{"role": "user", "content": "a" * 400}]
        self.assertEqual(conv.estimate_tokens(messages), 100)

    def test_sums_across_messages(self):
        messages = [
            {"role": "system", "content": "a" * 40},
            {"role": "user", "content": "b" * 40},
        ]
        self.assertEqual(conv.estimate_tokens(messages), 20)

    def test_handles_missing_or_none_content(self):
        messages = [{"role": "user"}, {"role": "assistant", "content": None}]
        self.assertEqual(conv.estimate_tokens(messages), 0)


class ShouldClearHistoryTests(unittest.TestCase):
    def test_launch_request_clears(self):
        self.assertTrue(conv.should_clear_history("LaunchRequest"))

    def test_session_ended_request_clears(self):
        self.assertTrue(conv.should_clear_history("SessionEndedRequest"))

    def test_intent_request_does_not_clear(self):
        self.assertFalse(conv.should_clear_history("IntentRequest"))

    def test_unknown_type_does_not_clear(self):
        self.assertFalse(conv.should_clear_history("SomeOtherRequestType"))
        self.assertFalse(conv.should_clear_history(None))


class SessionAttributesFromHistoryTests(unittest.TestCase):
    def test_wraps_history_in_expected_key(self):
        history = [{"role": "user", "content": "q"}]
        self.assertEqual(
            conv.session_attributes_from_history(history), {"conversation_history": history}
        )

    def test_none_history_becomes_empty_list(self):
        self.assertEqual(conv.session_attributes_from_history(None), {"conversation_history": []})

    def test_empty_history_stays_empty_list(self):
        self.assertEqual(conv.session_attributes_from_history([]), {"conversation_history": []})

    def test_never_returns_none(self):
        # session_attributes_from_history's result is meant to be passed
        # straight to alexa_response(session_attributes=...), which treats
        # None specially (omits the key) -- this function must never
        # accidentally produce that.
        self.assertIsNotNone(conv.session_attributes_from_history(None))
        self.assertIsNotNone(conv.session_attributes_from_history([]))


if __name__ == "__main__":
    unittest.main()
