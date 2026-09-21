# 0001. No local LLM inference on the netbook

- Status: accepted
- Date: 2026-09-20
- Deciders: solo (Luiz Wagner)

## Context

The original idea was to run a small local LLM on the netbook itself so Alexa
could talk to it without any cloud dependency. Hardware was verified via SSH:
Intel Atom N270 @ 1.6GHz (1 physical core), 32-bit `i686` userspace, SSE/SSE2/SSSE3
only (no AVX/AVX2/FMA), 2GB RAM with ~1.5GB available (already shared with
Syncthing and `digital_frame.sh`). See `../architecture.md` §1 for the full
breakdown.

## Decision

The netbook will not run any model. It runs only a thin HTTP/JSON relay
(tens of MB, sub-second CPU per request). All inference happens at a cloud
provider (see [0002](0002-openrouter-cloud-inference-over-local-ollama.md)).

## Consequences

### Positive
- Matches what this specific hardware can actually do; no fighting 32-bit
  toolchains or missing SIMD instructions.
- Relay footprint (~55–95MB RSS) coexists comfortably with the existing apps.

### Negative / trade-offs
- Introduces an external dependency (network + cloud provider) that a
  fully local setup wouldn't have.
- Prompts leave the house — see privacy note in `../architecture.md` §9.6.

### Follow-ups
- None beyond what's tracked in `../architecture.md`.

## Alternatives considered

- **Local inference on the netbook** — rejected outright; 32-bit + no AVX +
  2GB RAM is roughly a decade below the floor for any GGUF/llama.cpp-class
  runtime.
- **Local inference on the Windows PC** — this is what the earlier prototype
  at `/mnt/e/dev/alexa-talk-pal` did (Ollama + `qwen2.5-coder:7b-instruct`).
  Rejected as the *default* this time — see
  [0002](0002-openrouter-cloud-inference-over-local-ollama.md) for why.
