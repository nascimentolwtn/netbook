# Changelog — alexa-talk-pal

Completed planning/decision milestones. Build-phase work (see
`docs/architecture.md` §11) moves here from the napkin backlog as each item
finishes.

## 2026-09-21

- **Decided locale + invocation name (backlog item 2)**: reusing the prior
  prototype's Alexa skill — Skill ID, invocation name `"english talk pal"`,
  en-US locale. See
  [ADR 0009](docs/adr/0009-reuse-prior-skill-id-and-invocation-name.md).
  Skill's endpoint (Lambda ARN) and interaction model (`TalkIntent`) still
  need updating to match the new architecture — not functional for testing
  yet.
- **Confirmed ngrok runs on the netbook**: `linux/386` build (v3.39.11,
  statically-linked ELF, no dynamic deps) downloaded and ran `ngrok
  version` successfully via SSH. No fallback to `cloudflared`/Option C
  needed (ADR 0004). Binary currently at `/tmp/ngrok` on the netbook;
  permanent placement + systemd unit is Phase 1 work.
- **Resolved Phase 0 top risk**: `cryptography` is already installed via
  apt (`python3-cryptography` 2.1.4, native i386 package) on the netbook —
  no source build needed. All submodules required for Alexa signature
  verification import cleanly. Recorded as
  [ADR 0007](docs/adr/0007-cryptography-via-apt-not-pip-build.md), which
  also documents that venv creation needs `--system-site-packages
  --without-pip` on this box (ensurepip is broken). No blockers remain for
  Phase 1.
- **Reviewed prior prototype** at `/mnt/e/dev/alexa-talk-pal` (Windows PC:
  Ollama + FastAPI + ngrok + AWS Lambda relay). Identified the likely cause
  of its reported "very difficult to configure inside the Alexa account"
  pain: the README's Lambda setup steps never mention adding an "Alexa
  Skills Kit" trigger to the Lambda function — the step that grants Alexa
  permission to invoke it. Missing it causes a silent authorization
  failure.
- **Decided and recorded 6 ADRs** in `docs/adr/`:
  - 0001 — No local LLM inference on the netbook (hardware-blocked: i686,
    no AVX, 2GB RAM, Atom N270 — verified via SSH).
  - 0002 — OpenRouter cloud inference over local Ollama-on-PC (netbook is
    always-on; PC isn't).
  - 0003 — Self-hosted HTTPS relay + own signature verification, **not**
    AWS Lambda ARN (avoids the AWS/IAM trigger-linking step that most
    likely caused the prior difficulty).
  - 0004 — ngrok free tier with a claimed persistent dev domain (fixes the
    prior prototype's documented "URL changes every restart" problem).
  - 0005 — Single catch-all intent + `AMAZON.SearchQuery` slot (same shape
    the prior prototype used; flagged as the fiddliest console step either
    way).
  - 0006 — Skill stays unpublished / development mode.

## 2026-09-20

- **Hardware feasibility investigated** via direct SSH to the netbook:
  Intel Atom N270 @ 1.6GHz, 32-bit `i686`, SSE/SSE2/SSSE3 only (no AVX),
  2GB RAM (~1.5GB available). Confirmed local LLM inference is not viable
  on this hardware (see ADR 0001).
- **`docs/architecture.md` written**: full request flow (Echo → Alexa
  Skills Kit → tunnel → netbook relay → OpenRouter → back), component
  table, $0/month free-tier cost posture, relay design, resource footprint
  estimate, model-choice guidance, security considerations, open
  questions, and a phased build order (Phase 0–4).
