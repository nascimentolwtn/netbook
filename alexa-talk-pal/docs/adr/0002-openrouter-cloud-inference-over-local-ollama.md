# 0002. OpenRouter cloud inference over local Ollama-on-PC

- Status: accepted
- Date: 2026-09-20
- Deciders: solo (Luiz Wagner)

## Context

An earlier prototype exists at `/mnt/e/dev/alexa-talk-pal` (Windows PC). It
ran Ollama (`qwen2.5-coder:7b-instruct`) locally on the PC, fronted by a
FastAPI server on port 8000, tunneled with ngrok, and reached from Alexa via
an AWS Lambda relay. That design ties availability to the Windows PC being
powered on and Ollama running — the PC is not an always-on device the way
the netbook is (the netbook already runs 24/7 for Syncthing).

The user has an OpenRouter account. OpenRouter exposes `:free` model
variants at genuinely $0 cost (rate-limited — see `../architecture.md` §8).

## Decision

Use OpenRouter's cloud API for inference instead of a locally-run Ollama
model. The netbook (always-on) becomes the home-server component; no
dependency on the Windows PC's power state.

## Consequences

### Positive
- Works whenever the netbook is up, which is effectively always — no need
  to remember to start Ollama/FastAPI on the PC before asking Alexa anything.
- One API key, many swappable models, vs. managing local model files/updates.
- Genuinely $0 on `:free` models (see [0004](0004-ngrok-free-tier-for-public-ingress.md)
  cost posture in `../architecture.md` §1.1).

### Negative / trade-offs
- No longer fully local/offline — prompts go to OpenRouter and its
  downstream provider, some of which train on `:free`-tier prompts unless
  explicitly opted out (`../architecture.md` §9.6).
- Free tier caps at 50 requests/day (20/min) until $10 lifetime credit is
  purchased, vs. no such cap on a self-hosted model.
- Variable latency on free endpoints (best-effort capacity) working against
  Alexa's ~8s response deadline — was not a concern with a dedicated PC GPU.

### Follow-ups
- Revisit after a week of real usage whether 50/day is enough (open
  question, `../architecture.md` §10.6).

## Alternatives considered

- **Keep Ollama on the Windows PC**, as the prior prototype did — rejected
  as the default: ties the whole system to the PC being on, and the prior
  attempt does not appear to have reached a working end-to-end Echo
  interaction with this setup.
- **Local inference on the netbook** — rejected, see
  [0001](0001-no-local-llm-inference-on-netbook.md).
