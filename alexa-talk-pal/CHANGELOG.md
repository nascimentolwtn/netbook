# Changelog — alexa-talk-pal

Completed planning/decision milestones. Build-phase work (see
`docs/architecture.md` §11) moves here from the napkin backlog as each item
finishes.

## 2026-09-21

- **Re-tested local llama.cpp after a server restart "without the thinking
  flag"**: did not fix the truncation bug from ADR 0012. Reasoning no
  longer comes back as a separate `reasoning_content` field, but the raw
  `<think>...</think>` block is now inlined directly into `content`
  instead -- still eats the shared token budget (`finish_reason: "length"`
  on the same repro query), and would now get read aloud verbatim on top
  of that. Decision unchanged (OpenRouter stays primary); documented in
  ADR 0012's Follow-ups.
- **Generalized the relay's LLM call and compared OpenRouter vs. local
  llama.cpp (backlog item 4, Phase 4 option)**: `app.py`'s
  `_call_openrouter` was hardcoded to OpenRouter's URL/auth; split out a
  generic `_call_chat_completions(base_url, model, query, api_key=None)`
  and gave `measure_latency.py` a `--base-url`/`--api-key` flag so the
  same probe can target any OpenAI-compatible backend.
  Ran both from the netbook against the same model weights
  (`LFM2.5-2.6B-Q4_K_M.gguf` locally on the Windows PC at
  192.168.4.55:11434, `liquid/lfm-2.5-2.6b:free` on OpenRouter): local was
  3-5x faster and jitter-free (p90 1.30s vs. 6.08s), but the local
  server runs LFM2.5 with hybrid reasoning enabled by default and no
  per-request flag cleanly disables it — on a normal question, reasoning
  ate the shared `max_tokens=150` budget and truncated the spoken answer
  mid-sentence. Decided to keep OpenRouter as the only wired backend until
  the Windows PC server is relaunched with reasoning disabled
  server-side. See
  [ADR 0012](docs/adr/0012-openrouter-stays-primary-local-llama-cpp-not-yet-viable.md).
- **Picked the default OpenRouter model (backlog item 1, Phase 1 latency
  measurement)**: `liquid/lfm-2.5-2.6b:free` as `OPENROUTER_MODEL`,
  `openrouter/free` (the free-models router) as
  `OPENROUTER_FALLBACK_MODEL`. Tested via `relay/measure_latency.py`
  (reuses `app.py`'s real request shape) against four live candidates:
  `nvidia/nemotron-3.5-lightning:free` was disqualified by dashboard data
  alone (40–80s time-to-first-token); `google/gemma-4-26b-a4b-it:free` and
  `qwen/qwen3.8-27b:free` both 429'd on 100% of calls across repeated
  spaced-out runs (provider capacity, not our pacing); `liquid/lfm-2.5-2.6b:free`
  succeeded on 17/19 calls across two runs, p90 1.55–3.20s, comfortably
  under the 8s Alexa deadline. See
  [ADR 0011](docs/adr/0011-lfm2.5-default-model-openrouter-free-fallback.md).
  **Also discovered while checking quota**: the account already has the
  $10 lifetime credit applied, so the real daily cap is 1,000/day, not
  50/day — this also resolves the old "revisit 50/day after a week"
  backlog concern; removed from the backlog as no longer a live risk.
  **Still needs a manual step**: set `OPENROUTER_MODEL` and
  `OPENROUTER_FALLBACK_MODEL` in the real `.env` — not done by the
  assistant, per the standing `.env` access rule (ADR 0008).
- **Decided the OpenRouter free-endpoint training opt-in (backlog item 1)**:
  opted in — full `:free` model catalog stays available for Phase 1's
  latency-based model selection, mitigated by household behavior (no
  private questions on this Echo) rather than a restricted model list.
  $0-spend-limit key already created, training toggle confirmed enabled
  in the OpenRouter account dashboard. See
  [ADR 0010](docs/adr/0010-opt-in-to-free-endpoint-training.md).
- **Wrote the three fallback spoken lines**: timeout, daily-quota-exhausted,
  and a generic-error catch-all (a literal "tunnel down" line isn't
  speakable by the relay — if the tunnel's down, Alexa can never reach it).
  `alexa-talk-pal/relay/fallback_messages.py`.
- **Built and curl-verified `talkpal-relay`**: Flask app with `/health` and
  `/alexa` (LaunchRequest, SessionEndedRequest, Stop/Cancel/Help,
  `AskAnythingIntent`), OpenRouter call with a (2s connect, 5s read)
  timeout, one-shot 429 fallback to `OPENROUTER_FALLBACK_MODEL`, a daily
  quota guard persisted to disk, and real Alexa signature verification
  (not just a debug stub) gated by `DEBUG_SKIP_SIGNATURE`.
  `alexa-talk-pal/relay/app.py`, `requirements.txt`. Verified live on the
  netbook: venv built per ADR 0007, `/health` and `LaunchRequest` returned
  200, `AskAnythingIntent` hit real OpenRouter with a dummy key (got a
  genuine 401) and returned the graceful fallback line rather than a crash
  or 500, and signature rejection was confirmed with the debug bypass off.
  **Known gap**: signature verification doesn't build a path to a locally
  trusted Amazon root CA — apt's `cryptography` 2.1.4 predates that API.
  Mitigated by pinning `SignatureCertChainUrl` to `s3.amazonaws.com`, SAN
  and validity-date checks, and chain-internal signature verification;
  tracked as an open backlog item to explicitly accept or harden before
  the real Alexa endpoint goes live.
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
