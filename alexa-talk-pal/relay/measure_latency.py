"""Phase 1 latency probe for candidate :free OpenRouter models.

Standalone script, not part of the request path -- run manually against
real candidates, then throw the numbers into ADR/backlog notes. Reuses
app.py's exact request shape (timeouts, system prompt, max_tokens) so the
measured latency matches what the relay will actually see in production.

Usage: python3 measure_latency.py <model1> [<model2> ...]
"""
import statistics
import sys
import time

from app import _call_openrouter

REQUEST_SPACING_SECONDS = 2.0  # stay under the 20/min burst limit

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


def measure(model, queries):
    latencies = []
    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(REQUEST_SPACING_SECONDS)
        start = time.monotonic()
        ok = False
        try:
            resp = _call_openrouter(model, query)
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
    candidates = sys.argv[1:]
    if not candidates:
        print("Usage: python3 measure_latency.py <model1> [<model2> ...]")
        sys.exit(1)

    results = {}
    for model in candidates:
        print("\n=== {} ===".format(model))
        results[model] = measure(model, QUERIES)

    print("\n=== SUMMARY (8s Alexa deadline) ===")
    for model, latencies in results.items():
        summarize(model, latencies)
