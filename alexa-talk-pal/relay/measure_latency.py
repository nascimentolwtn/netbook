"""Latency probe for candidate LLM backends -- OpenRouter :free models
(Phase 1) or any OpenAI-compatible local server, e.g. llama.cpp on the
Windows PC (Phase 4, napkin backlog item 4).

Standalone script, not part of the request path -- run manually against
real candidates, then throw the numbers into ADR/backlog notes. Reuses
app.py's exact request shape (timeouts, system prompt, max_tokens) so the
measured latency matches what the relay will actually see in production.

Usage:
  python3 measure_latency.py <model1> [<model2> ...]
      Tests against OpenRouter (default), using OPENROUTER_API_KEY from .env.

  python3 measure_latency.py --base-url http://HOST:PORT/v1/chat/completions <model>
      Tests against any OpenAI-compatible endpoint, e.g. a local llama.cpp
      server. No --api-key needed if the server doesn't require auth.
"""
import argparse
import statistics
import sys
import time

from app import OPENROUTER_API_KEY, OPENROUTER_URL, _call_chat_completions

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


def measure(base_url, api_key, model, queries):
    latencies = []
    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(REQUEST_SPACING_SECONDS)
        start = time.monotonic()
        ok = False
        try:
            resp = _call_chat_completions(base_url, model, query, api_key=api_key)
            resp.raise_for_status()
            resp.json()["choices"][0]["message"]["content"]
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
    args = parser.parse_args()

    api_key = args.api_key
    if api_key is None and args.base_url == OPENROUTER_URL:
        api_key = OPENROUTER_API_KEY

    results = {}
    for model in args.models:
        print("\n=== {} ({}) ===".format(model, args.base_url))
        results[model] = measure(args.base_url, api_key, model, QUERIES)

    print("\n=== SUMMARY (8s Alexa deadline) ===")
    for model, latencies in results.items():
        summarize(model, latencies)
