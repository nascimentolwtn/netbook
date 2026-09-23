# Changelog — alexa-talk-pal

Completed planning/decision milestones. Build-phase work (see
`docs/architecture.md` §11) moves here from the napkin backlog as each item
finishes.

## 2026-09-23

- **Closed backlog item 1: OpenRouter reasoning-leak bug fixed.** The bug
  found in Phase 2 console testing (relay spoke raw reasoning text instead
  of a clean answer) turned out to be ADR 0012's bug again, on a different
  backend: `liquid/lfm-2.5-2.6b:free` has *mandatory* hybrid reasoning on
  OpenRouter's free endpoint (`reasoning: {enabled: false}` is rejected
  outright), and it was consistently burning ~148-150 of the hardcoded
  150-token budget, leaving `content` empty with `finish_reason: "length"`.
  Fixed by raising `OPENROUTER_MAX_TOKENS` to an env-configurable 500 (same
  value ADR 0013 chose for the local backend), plus a shared
  `_strip_leaked_reasoning` helper used by both `ask_openrouter` and
  `ask_local_llm` as defense-in-depth against an inline `<think>` leak.
  Verified live against multiple repro queries: `finish_reason` now `"stop"`,
  clean short answers, latency still 0.8-1.3s. Deployed to the netbook,
  service restarted, `relay/tests/test_signature.py` still 11/11 (unrelated
  code, unaffected). See
  [ADR 0015](docs/adr/0015-openrouter-max-tokens-500-plus-reasoning-strip.md).
  Phase 2 console reconfiguration + this fix together close out everything
  blocking Plan 0003's live-Echo test.

## 2026-09-22

- **Closed backlog item 2 (Phase 2, console side): skill endpoint switched to
  HTTPS, interaction model replaced, console simulator gets real relay
  replies.** Followed `docs/plans/0002-phase2-alexa-console-reconfiguration.md`.
  Endpoint is `https://viscous-landlady-reappoint.ngrok-free.dev/alexa`
  (wildcard SSL option); interaction model is Plan A's `AskAnythingIntent` +
  `query` slot after two re-pastes to clear wizard-scaffold leftovers
  (`HelloWorldIntent`, an `AMAZON.FallbackIntent` the relay doesn't handle,
  wrong invocation name) and a stray bare `{query}` sample. Build succeeds;
  Test-tab simulator gets real relay replies for launch, a question, help,
  and stop. Utterance Profiler threw "Internal Server Error" twice on a
  correctly-built model (an Amazon-side glitch, not ours) and was skipped in
  favor of the simulator, which fully passed.
  **Correction to ADR 0009**: the original "English Talk Pal" skill turned
  out to be under a different Amazon account than the one the Echo uses, so
  it was recreated from scratch rather than reused — invocation name and
  locale carried over as planned, but the Skill ID is new. See
  [ADR 0009's 2026-09-22 update](docs/adr/0009-reuse-prior-skill-id-and-invocation-name.md).
  **Bug found, not fixed here** (napkin backlog item 1): on "tell me why the
  sky is blue," the relay spoke OpenRouter's raw reasoning text instead of a
  clean answer — the default model (`liquid/lfm-2.5-2.6b:free`) has the same
  hybrid-reasoning-leak behavior ADR 0012/0013 fixed for the local backend,
  but `ask_openrouter` does no reasoning-content handling. Routing itself
  worked correctly (proves the endpoint + interaction model are fine).
  Console reconfiguration is otherwise ready for the Phase 3 live-Echo test,
  once that leak is fixed.
- **Closed backlog item 1: signature verification now anchors to a real
  trust root via the system `openssl verify` CLI, before the Phase 2
  endpoint switch.** Planning (`docs/plans/0001-signature-verification-root-ca-decision.md`)
  found two problems with the original "accept current checks, pin
  Amazon's root fingerprint later" plan: the `/echo.api/` path check could
  be bypassed with a percent-encoded `..` (safe on the netbook today only
  by accident, via its older urllib3 version), and the planned pin design
  would have pinned an intermediate cert, not a root (`certs[-1]` is never
  the root in a correctly served chain). Implemented option (c) instead:
  URL hardening (reject `%`/dot-segments in `SignatureCertChainUrl`, no
  redirects, re-check the actual prepared URL) plus a new
  `_verify_chain_anchor` that shells out to `openssl verify -CAfile
  /etc/ssl/certs/ca-certificates.crt` on every cert-chain cache miss,
  fail-closed on any non-zero exit, timeout, or missing binary. No
  `cryptography`/Python upgrade needed or possible on this hardware (ADR
  0007's reasoning holds). New `relay/tests/test_signature.py` (11 tests,
  stdlib `unittest`, no network): 11/11 pass locally and on the netbook
  relay venv (cryptography 2.1.4 needed explicit `backend=default_backend()`
  in fixture generation that newer versions default automatically).
  `openssl verify`'s exit-code/`: OK` contract confirmed by hand on the
  netbook against a real chain (openrouter.ai) and a self-signed cert.
  Deployed to `~/talkpal-relay/`, service restarted, smoke-tested with
  `DEBUG_SKIP_SIGNATURE` off: `/health` 200 (local + public tunnel), an
  unsigned POST to `/alexa` correctly gets `400 signature verification
  failed`. No live Alexa traffic yet (still on the old Lambda ARN), so the
  restart carried no live-request risk. See
  [ADR 0014](docs/adr/0014-openssl-system-ca-anchor-for-signature-chain.md).
  Phase 2 follow-up (capture the real chain, optionally narrow to a pinned
  `echo-api-roots.pem`) stays blocked on the endpoint switch.
- **Closed backlog item 3: local llama.cpp is now a switchable backend
  option (`INFERENCE_BACKEND=local`), OpenRouter stays the default**.
  The fix for the truncation bug ADR 0012 found: raise `max_tokens` for
  local calls only (`LOCAL_LLM_MAX_TOKENS=500` vs. OpenRouter's 150) --
  reasoning shares the same token budget as the spoken answer and was
  getting starved at 150. Tested 15/15 clean at 500 (5x the repro
  question, then the full 10-query set), latency barely moved since the
  model naturally stops around 100-240 tokens on its own. `app.py`'s
  `_call_chat_completions` now takes `max_tokens` as a parameter instead
  of a hardcoded value; new `ask_local_llm`/`ask_llm` dispatch, default
  path (`ask_openrouter`) is byte-for-byte unchanged. Deployed and
  smoke-tested live via the public tunnel. See
  [ADR 0013](docs/adr/0013-configurable-local-backend-openrouter-stays-default.md).
- **Retested local llama.cpp with reasoning reverted back on**: same
  Moon-distance repro query as the earlier reasoning-off attempt.
  `reasoning_content` is correctly separated from `content` again in this
  config, but the underlying bug is confirmed present here too, and worse
  than the original test: `content` came back completely empty (`""`),
  `finish_reason: "length"`, all 150 tokens went to `reasoning_content`
  (619 chars). Corrected a napkin/ADR 0012 note that had implied this
  reverted state was already re-tested and ruled out -- it hadn't been;
  only the reasoning-off attempt had. Decision unchanged (OpenRouter
  primary); still needs an untried `--reasoning-format none`-style launch
  flag before local is viable.
- **Closed backlog item 1 (Phase 1): systemd units for relay + tunnel,
  reboot-tested for a stable hostname**. `ngrok` re-downloaded (`linux/386`
  v3.39.11, same build as before — the earlier `/tmp/ngrok` copy had been
  cleared, likely by a reboot) to `/usr/local/bin/ngrok`. Two systemd units
  added on the netbook: `talkpal-relay.service` (waitress via the venv,
  `WorkingDirectory=~/talkpal-relay`) and `talkpal-tunnel.service`
  (`ngrok http 5040 --url=https://viscous-landlady-reappoint.ngrok-free.dev`,
  `Requires=talkpal-relay.service`). Both `enabled` + verified `active`
  before and after a real `sudo reboot` (back up in ~40s); `curl
  https://viscous-landlady-reappoint.ngrok-free.dev/health` returned a
  clean `200 {"status":"ok"}` both times, same hostname, no interstitial
  warning page on the GET. Fixes the exact rotating-hostname failure mode
  the prior prototype documented. Domain itself was already claimed
  (~5 months old, likely an undocumented step from that same prior
  prototype attempt) — ngrok's free tier only allows one claimed domain
  per account, confirmed while trying to reserve a second one. See
  [ADR 0004's 2026-09-22 update](docs/adr/0004-ngrok-free-tier-for-public-ingress.md).

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
