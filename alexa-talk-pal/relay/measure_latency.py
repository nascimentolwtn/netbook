"""Latency probe for candidate LLM backends -- OpenRouter :free models
(Phase 1) or any OpenAI-compatible local server, e.g. llama.cpp on the
Windows PC (Phase 4, napkin backlog item 4).

Standalone script, not part of the request path -- run manually against
real candidates, then throw the numbers into ADR/backlog notes. Reuses
app.py's exact request shape (timeouts, system prompt, max_tokens) so the
measured latency matches what the relay will actually see in production.

Plan 0005 §6a: app.py's `_call_chat_completions` now takes a `messages`
list instead of a bare `query` string (built via
`conversation.build_messages_with_history`, same as `ask_llm`), so this
script builds the same request shape rather than a single-turn-only one.
It also gained a multi-turn replay mode -- the only way to get real
numbers for the "N-turn history" latency question that
docs/plans/0005-phase4-llm-conversational-intelligence.md §2.3/§6 leaves
explicitly unverified. Plan 0004 §6 Phase 0.2 added --questions-file (so
the same script can be pointed at a Portuguese query set without a code
fork) and --locale (so the measured request carries the same locale-
selected system prompt app.py would actually send).

Usage:
  python3 measure_latency.py <model1> [<model2> ...]
      Single-turn mode (default): each query in QUERIES (or
      --questions-file) is sent as an independent, isolated request --
      matches today's production single-turn shape.

  python3 measure_latency.py --multi-turn <model1> [<model2> ...]
      Multi-turn replay mode: queries are sent as ONE growing conversation
      per model -- each response is fed back in as history for the next
      call, same as a real multi-turn Alexa session (app.py's
      conversation.append_to_history / trim_conversation_history). Use
      2-5 queries (via --questions-file or the default QUERIES list) to
      match the plan's "5-turn history" scenario.

  python3 measure_latency.py --base-url http://HOST:PORT/v1/chat/completions <model>
      Tests against any OpenAI-compatible endpoint, e.g. a local llama.cpp
      server. No --api-key needed if the server doesn't require auth.

  python3 measure_latency.py --questions-file myqueries.txt <model>
      Loads queries from a file (one per line, blank lines and lines
      starting with # ignored) instead of the hardcoded QUERIES list.

  python3 measure_latency.py --questions-file pt_br_queries.txt --locale pt_BR <model>
      Measures against a Portuguese query set using the pt_BR system
      prompt, matching what app.py sends for a real pt-BR request
      (docs/plans/0004 §6 Phase 0.2).
"""
import argparse
import statistics
import sys
import time

from app import (
    CONVERSATIONAL_SYSTEM_PROMPTS,
    DEFAULT_LOCALE,
    OPENROUTER_API_KEY,
    OPENROUTER_URL,
    _call_chat_completions,
)
from conversation import append_to_history, build_messages_with_history, trim_conversation_history

REQUEST_SPACING_SECONDS = 2.0  # stay under OpenRouter's 20/min burst limit

QUERIES = [
    "What's the capital of France?",
    "How many moons does Jupiter have?",
    "What year did the Berlin Wall fall?",
    "Who wrote Pride and Prejudice?",
    "What's the boiling point of water in Fahrenheit?",
    "Name three primary colors.",
    "Roughly how far is the Moon from Earth?",
    "What's the largest ocean on Earth?",
    "Who painted the Mona Lisa?",
    "What's the chemical symbol for gold?",
]


def _system_prompt_for_locale(locale):
    return CONVERSATIONAL_SYSTEM_PROMPTS.get(locale, CONVERSATIONAL_SYSTEM_PROMPTS[DEFAULT_LOCALE])


def load_questions_from_file(path):
    """One query per line; blank lines and lines starting with # are
    skipped. Raises the same way `open()` would on a missing/unreadable
    file -- caller decides how to report that."""
    questions = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            questions.append(line)
    return questions


def _do_call(base_url, api_key, model, messages):
    resp = _call_chat_completions(base_url, model, messages, api_key=api_key)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def measure(base_url, api_key, model, queries, locale=DEFAULT_LOCALE):
    """Single-turn mode: each query is an independent request (no history
    carried between them) -- matches today's production single-turn
    request shape. `locale` (docs/plans/0004) selects which system prompt
    is sent, matching what app.py would actually send for that locale."""
    system_prompt = _system_prompt_for_locale(locale)
    latencies = []
    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(REQUEST_SPACING_SECONDS)
        start = time.monotonic()
        ok = False
        try:
            messages = build_messages_with_history(
                query, conversation_history=None, system_prompt=system_prompt
            )
            _do_call(base_url, api_key, model, messages)
            ok = True
        except Exception as exc:
            print("  ERROR: {}".format(exc))
        elapsed = time.monotonic() - start
        # Only successful calls count as a latency sample -- a fast 429/error
        # rejection isn't a response-time measurement.
        if ok:
            latencies.append(elapsed)
        print("  {:.2f}s  {}  {!r}".format(elapsed, "OK" if ok else "FAIL", query))
    return latencies


def measure_multi_turn(base_url, api_key, model, queries, locale=DEFAULT_LOCALE):
    """Multi-turn replay mode (plan 0005 §6a): all `queries` are sent as
    ONE growing conversation -- each response is fed back in as history
    for the next call, same shape as a real multi-turn Alexa session
    (trim_conversation_history caps it at 5 turns, same as app.py).
    `locale` (docs/plans/0004) selects which system prompt is sent."""
    system_prompt = _system_prompt_for_locale(locale)
    latencies = []
    history = []
    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(REQUEST_SPACING_SECONDS)
        history = trim_conversation_history(history)
        start = time.monotonic()
        ok = False
        answer = None
        try:
            messages = build_messages_with_history(
                query, conversation_history=history, system_prompt=system_prompt
            )
            answer = _do_call(base_url, api_key, model, messages)
            ok = True
        except Exception as exc:
            print("  ERROR: {}".format(exc))
        elapsed = time.monotonic() - start
        if ok:
            latencies.append(elapsed)
            history = append_to_history(history, query, answer)
        print(
            "  turn {} ({} history messages)  {:.2f}s  {}  {!r}".format(
                i + 1, len(history), elapsed, "OK" if ok else "FAIL", query
            )
        )
    return latencies


def summarize(model, latencies):
    if not latencies:
        print("\n{} -- no data".format(model))
        return
    sorted_lat = sorted(latencies)
    p90 = sorted_lat[min(len(sorted_lat) - 1, int(len(sorted_lat) * 0.9))]
    print(
        "\n{}\n  min={:.2f}s avg={:.2f}s p90={:.2f}s max={:.2f}s".format(
            model, min(latencies), statistics.mean(latencies), p90, max(latencies)
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="+", help="model identifiers to test")
    parser.add_argument(
        "--base-url",
        default=OPENROUTER_URL,
        help="OpenAI-compatible /chat/completions endpoint (default: OpenRouter)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Bearer token. Omit for backends needing no auth (e.g. local "
        "llama.cpp). Defaults to OPENROUTER_API_KEY (.env) when --base-url "
        "is left at the OpenRouter default.",
    )
    parser.add_argument(
        "--multi-turn",
        action="store_true",
        help="Replay the query list as a single growing conversation per "
        "model instead of independent single-turn requests (plan 0005 §6a).",
    )
    parser.add_argument(
        "--questions-file",
        default=None,
        help="Load queries from this file (one per line, # comments "
        "allowed) instead of the hardcoded QUERIES list.",
    )
    parser.add_argument(
        "--locale",
        default=DEFAULT_LOCALE,
        choices=["en_US", "pt_BR"],
        help="Internal locale key (docs/plans/0004) selecting which system "
        "prompt is sent alongside each query, so the measured request shape "
        "matches production for that locale. Default: en_US.",
    )
    args = parser.parse_args()

    api_key = args.api_key
    if api_key is None and args.base_url == OPENROUTER_URL:
        api_key = OPENROUTER_API_KEY

    if args.questions_file:
        queries = load_questions_from_file(args.questions_file)
        if not queries:
            print("No usable queries found in {}".format(args.questions_file))
            sys.exit(1)
    else:
        queries = QUERIES

    run = measure_multi_turn if args.multi_turn else measure

    results = {}
    for model in args.models:
        print(
            "\n=== {} ({}) [{}, locale={}] ===".format(
                model, args.base_url, "multi-turn" if args.multi_turn else "single-turn", args.locale
            )
        )
        results[model] = run(args.base_url, api_key, model, queries, locale=args.locale)

    print("\n=== SUMMARY (8s Alexa deadline) ===")
    for model, latencies in results.items():
        summarize(model, latencies)
