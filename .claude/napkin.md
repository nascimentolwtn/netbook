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

1. **[2026-09-21] Phase 0: confirm a current `linux/386` ngrok build exists and runs on the netbook**
   Do instead: download ngrok, run `ngrok version` on the netbook before building anything on top of it. If missing → try `cloudflared` 386 (needs an owned domain, ADR 0004 Option B) → else Option C (tunnel agent on the Windows PC).

2. **[2026-09-21] Decide the OpenRouter free-endpoint training opt-in before creating the account/key**
   Do instead: explicitly choose whether to allow providers to log/train on `:free` prompts (architecture.md §9.6); set the key's spend limit to $0 regardless of the choice.

3. **[2026-09-21] Decide skill locale + language (pt-BR vs en-US) and invocation name before building the interaction model**
   Do instead: pick these first — locale isn't trivially changed later and drives the interaction model, system-prompt language, and console setup (architecture.md §10.8, §10.1).

4. **[2026-09-21] Write the three fallback spoken lines up front (timeout / daily quota exhausted / tunnel down)**
   Do instead: draft the exact copy before the Phase 1 relay is built, so error paths return real text, not placeholders (architecture.md §10.9).

5. **[2026-09-21] Phase 1: build `talkpal-relay` (Flask, venv `--system-site-packages --without-pip`) + OpenRouter call + quota guard, verify via curl**
   Do instead: build with a debug signature-bypass flag first; wire real signature verification next — crypto packaging is resolved (ADR 0007), nothing blocks this anymore.

6. **[2026-09-21] Phase 1: measure real latency across 2–3 candidate `:free` OpenRouter models before picking the default**
   Do instead: run ~10 timed queries per candidate (mind the 50/day free cap while testing), pick based on tail latency against the 8s Alexa deadline — not the table in architecture.md §8.2.

7. **[2026-09-21] Phase 1: install systemd units for relay + tunnel, reboot-test for a stable hostname**
   Do instead: confirm the tunnel survives a reboot with the *same* hostname before touching the Alexa console at all — a rotating hostname was the prior prototype's documented failure mode.

8. **[2026-09-21] Phase 2: build the Alexa interaction model — budget iteration time for `AMAZON.SearchQuery` validator rejections**
   Do instead: expect several "Save Model" failures (ADR 0005); keep the two-turn fallback (LaunchRequest asks the question, then captures the follow-up) ready if the single-shot slot won't validate.

9. **[2026-09-21] Phase 3: after a week of real Echo usage, revisit whether 50 requests/day is enough**
   Do instead: check the OpenRouter dashboard for actual queries/day vs. the cap; if tight, the cheapest escape hatch is a one-time $10 credit purchase (permanently unlocks 1,000/day).

*(Phase 4 polish items — session-memory, progressive response, root README update to a four-app ecosystem — are explicitly optional "only if v1 earns it" per architecture.md §11 Phase 4; not tracked here until Phase 3 ships.)*

## Domain Behavior Guardrails
1. **[2026-09-21] Netbook hardware rules out any local LLM inference — already verified, don't re-check**
   Do instead: point to `alexa-talk-pal/docs/adr/0001-no-local-llm-inference-on-netbook.md` (Atom N270, 32-bit i686, no AVX, 2GB RAM, verified via live SSH) instead of re-deriving hardware feasibility.

2. **[2026-09-21] This user's ADR template (reused across their projects) is NNNN-title.md**
   Do instead: Status/Date/Deciders/Context/Decision/Consequences(Positive/Negative/Follow-ups)/Alternatives-considered — match `alexa-talk-pal/docs/adr/template.md` for any new ADR in this repo, don't invent a new format.

3. **[2026-09-21] A prior abandoned alexa-talk-pal prototype exists outside this repo, at `/mnt/e/dev/alexa-talk-pal` (Windows PC)**
   Do instead: check it before re-deriving prior-attempt context (Ollama+FastAPI+ngrok+AWS Lambda; interaction-model.json and lambda-relay.mjs already exist there) — but note the new plan deliberately drops Lambda (ADR 0003), so don't reuse that piece. That folder's `docs/` also holds unrelated leftover docs from other projects (a portfolio app, an RTC drift utility) — don't mistake those for alexa-talk-pal notes.

4. **[2026-09-21] `python3 -m venv --system-site-packages` alone fails on the netbook (ensurepip missing)**
   Do instead: always create relay venvs with `python3 -m venv --system-site-packages --without-pip` (ADR 0007) — confirmed this correctly exposes the apt-installed `cryptography` 2.1.4.

## User Directives
1. **[2026-09-21] New components for this ecosystem live nested inside this repo, not as sibling repos**
   Do instead: default new component work to a subfolder of `netbook/` (e.g. `netbook/alexa-talk-pal/`) unless told otherwise — user explicitly rejected a sibling `~/git/alexa-talk-pal` location.

2. **[2026-09-21] Napkin backlog tracks OPEN items only**
   Do instead: when a backlog item completes, move its line to the relevant component's `CHANGELOG.md` with the date — never leave a "done" marker in the napkin backlog.
