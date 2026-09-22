# Napkin Runbook

## Curation Rules
- Re-prioritize on every read.
- Keep recurring, high-value notes only.
- Max 10 items per category.
- Each item includes date + "Do instead".
- **Backlog category holds OPEN items only** — when a backlog item is
  completed, move it to the relevant component's `CHANGELOG.md` (e.g.
  `alexa-talk-pal/CHANGELOG.md`) instead of marking it done here.

## Backlog — alexa-talk-pal (highest priority first)
Source of truth for phase detail: `alexa-talk-pal/docs/architecture.md` §11.
Decisions behind these steps: `alexa-talk-pal/docs/adr/`.

1. **[2026-09-21] Decide the OpenRouter free-endpoint training opt-in before creating the account/key**
   Do instead: explicitly choose whether to allow providers to log/train on `:free` prompts (architecture.md §9.6); set the key's spend limit to $0 regardless of the choice.

2. **[2026-09-21] Phase 1: measure real latency across 2–3 candidate `:free` OpenRouter models before picking the default**
   Do instead: run ~10 timed queries per candidate (mind the 50/day free cap while testing), pick based on tail latency against the 8s Alexa deadline — not the table in architecture.md §8.2.

3. **[2026-09-21] Phase 1: install systemd units for relay + tunnel (place ngrok permanently, e.g. `/usr/local/bin`), reboot-test for a stable hostname**
   Do instead: confirm the tunnel survives a reboot with the *same* hostname before touching the Alexa console at all — a rotating hostname was the prior prototype's documented failure mode.

4. **[2026-09-21] Before Phase 2 live cutover: review/harden signature verification's missing root-CA path-building**
   Do instead: `relay/app.py`'s `_verify_chain_signatures` checks internal chain-signature consistency, per-cert dates, SAN, and pins `SignatureCertChainUrl` to `s3.amazonaws.com`, but doesn't build a path to a locally trusted Amazon root (apt's `cryptography` 2.1.4 predates that API — see guardrail below). Decide explicitly whether the current mitigations are sufficient before wiring the real Alexa endpoint, or upgrade the crypto story.

5. **[2026-09-21] Phase 2: switch the reused skill's endpoint from Lambda ARN to HTTPS, edit interaction model off `TalkIntent` toward ADR 0005's shape**
   Do instead: expect several "Save Model" failures (ADR 0005); keep the two-turn fallback (LaunchRequest asks the question, then captures the follow-up) ready if the single-shot slot won't validate. Endpoint switch is per ADR 0003/0009 — same Skill ID, new endpoint config.

6. **[2026-09-21] Phase 3: after a week of real Echo usage, revisit whether 50 requests/day is enough**
   Do instead: check the OpenRouter dashboard for actual queries/day vs. the cap; if tight, the cheapest escape hatch is a one-time $10 credit purchase (permanently unlocks 1,000/day).

7. **[2026-09-21] Phase 4 option: add config/CLI flag to switch relay backend from OpenRouter to local llama.cpp on Windows PC**
   Do instead: wire the relay to support both backends via env var or CLI arg (e.g. `INFERENCE_BACKEND=openrouter` vs. `INFERENCE_BACKEND=local_llama:http://192.168.4.55:11434`). Run `llama.cpp` on the PC host WSL and measure latency over LAN to see if it meets the 8s Alexa deadline without the free-tier limits (50/day cap, training opt-in). Keeps OpenRouter as the default and tested path, but lets v2+ use a private inference backend if desired (architecture.md §1.2, ADR candidate).

*(Phase 4 polish items — session-memory, progressive response, root README update to a four-app ecosystem, local-LLM-on-PC option — are explicitly optional "only if v1 earns it" per architecture.md §11 Phase 4; not tracked here until Phase 3 ships.)*

## Domain Behavior Guardrails
1. **[2026-09-21] Netbook hardware rules out any local LLM inference — already verified, don't re-check**
   Do instead: point to `alexa-talk-pal/docs/adr/0001-no-local-llm-inference-on-netbook.md` (Atom N270, 32-bit i686, no AVX, 2GB RAM, verified via live SSH) instead of re-deriving hardware feasibility.

2. **[2026-09-21] This user's ADR template (reused across their projects) is NNNN-title.md**
   Do instead: Status/Date/Deciders/Context/Decision/Consequences(Positive/Negative/Follow-ups)/Alternatives-considered — match `alexa-talk-pal/docs/adr/template.md` for any new ADR in this repo, don't invent a new format.

3. **[2026-09-21] A prior abandoned alexa-talk-pal prototype exists outside this repo, at `/mnt/e/dev/alexa-talk-pal` (Windows PC)**
   Do instead: check it before re-deriving prior-attempt context (Ollama+FastAPI+ngrok+AWS Lambda; interaction-model.json and lambda-relay.mjs already exist there) — but note the new plan deliberately drops Lambda (ADR 0003), so don't reuse that piece. That folder's `docs/` also holds unrelated leftover docs from other projects (a portfolio app, an RTC drift utility) — don't mistake those for alexa-talk-pal notes.

4. **[2026-09-21] `python3 -m venv --system-site-packages` alone fails on the netbook (ensurepip missing)**
   Do instead: always create relay venvs with `python3 -m venv --system-site-packages --without-pip` (ADR 0007) — confirmed this correctly exposes the apt-installed `cryptography` 2.1.4.

5. **[2026-09-21] `architecture.md` §9.1's `/etc/talkpal/talkpal.env` secret location is superseded — don't re-apply it**
   Do instead: secrets live in `alexa-talk-pal/.env` (gitignored, `python-dotenv`), per ADR 0008. User knowingly accepted that this gets replicated by Syncthing to the Windows PC `/home` backup.

6. **[2026-09-21] Local llama.cpp model on Windows PC (192.168.4.55:11434) — LFM2.5-8B-A1B is very fast but hallucinates**
   Do instead: during Phase 4 evaluation (backlog §8), benchmark alternative models that trade some speed for accuracy. The current model works for latency testing but may not be suitable for production v2. Model selection (speed vs. accuracy vs. context length) is part of the Phase 4 latency measurement.

7. **[2026-09-21] apt's `cryptography` 2.1.4 (ADR 0007) predates path-building APIs — signature verification can't build a trust path to a local Amazon root store**
   Do instead: `relay/app.py`'s verification pins `SignatureCertChainUrl` to `s3.amazonaws.com`/`/echo.api/`, checks per-cert validity dates, the leaf SAN (`echo-api.amazon.com`), and chain-internal signatures — but not root-of-trust path validation. Don't rediscover this while debugging signature rejects; see backlog item 4 for the explicit go/no-go decision.

## User Directives
1. **[2026-09-21] New components for this ecosystem live nested inside this repo, not as sibling repos**
   Do instead: default new component work to a subfolder of `netbook/` (e.g. `netbook/alexa-talk-pal/`) unless told otherwise — user explicitly rejected a sibling `~/git/alexa-talk-pal` location.

2. **[2026-09-21] Napkin backlog tracks OPEN items only**
   Do instead: when a backlog item completes, move its line to the relevant component's `CHANGELOG.md` with the date — never leave a "done" marker in the napkin backlog.
