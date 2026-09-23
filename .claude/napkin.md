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

1. **[2026-09-23] Make Alexa conversational with LLM-powered understanding (Alexa+ style)**
   Do instead: Currently tightly bound to Alexa Skill command parsing. Add OpenRouter/local LLM layer to understand fluid conversation, maintain multi-turn context, and generate contextual responses beyond rigid slot-filling. Requires architecture design (token budget, latency SLA) and integration into relay. Phase 4 candidate per architecture.md §11 ("only if v1 earns it").

2. **[2026-09-23] Add Portuguese-BR (pt-BR) language support**
   Do instead: Extend Echo request locale handling and response generation to pt-BR. Affects: Alexa Skill locale routing, LLM prompt language selection, and TTS voice selection if speech output is added. Phase 4 candidate.

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

6. **[2026-09-23] `liquid/lfm-2.5-2.6b:free`'s hybrid reasoning is mandatory and shares `max_tokens` with the spoken answer on *both* backends**
   Do instead: OpenRouter's free endpoint for this model rejects `reasoning: {enabled: false}` with "Reasoning is mandatory for this endpoint," and consistently burned ~148-150 of a 150-token budget, leaving `content` empty (`finish_reason: "length"`) — the same failure ADR 0012 found on the local llama.cpp backend (192.168.4.55:11434), now confirmed on OpenRouter too. Fixed by raising `OPENROUTER_MAX_TOKENS` to 500 (env-configurable, same value as `LOCAL_LLM_MAX_TOKENS`, ADR 0012/0013/0015), plus a shared `_strip_leaked_reasoning` helper in `relay/app.py` as defense-in-depth against an inline `<think>` leak. Don't assume 8B-A1B is still loaded on the local server — check `GET /api/tags` or `/v1/models` before testing if the model might have changed again.

7. **[2026-09-22] Signature verification is anchored to a real trust root via the system `openssl verify` CLI, not `cryptography`**
   Do instead: apt's `cryptography` 2.1.4 (ADR 0007) still predates the `x509.verification` path-building API, so `relay/app.py`'s `_verify_chain_anchor` shells out to `openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt` on every cert-chain cache miss (ADR 0014) — fails closed on any non-zero exit/timeout/missing binary. Combined with URL hardening (reject `%`/dot-segments in `SignatureCertChainUrl`, no redirects) this closes both the missing-root-of-trust gap and an encoded-`../` traversal bypass. Don't rediscover this while debugging signature rejects; a real rejection means look at the logged `openssl` stderr, not add a bypass.

8. **[2026-09-21] The OpenRouter account already has the $10 credit applied — daily free-tier cap is 1,000/day, not 50/day**
   Do instead: confirmed via `GET /api/v1/key` (`free_model_daily_requests` field) — don't assume the 50/day figure from architecture.md §8.1 still applies, and don't re-litigate whether to buy the $10 credit (already done). Check remaining quota the same way (small throwaway script on the netbook using the real `.env`, never printing the key itself) rather than guessing.

9. **[2026-09-21] Larger `:free` OpenRouter models (20B+ class) 429 far more than small ones — this is provider capacity, not our request pacing**
   Do instead: `google/gemma-4-26b-a4b-it:free` and `qwen/qwen3.8-27b:free` both 429'd 100% of live test calls even with 2s spacing between requests, while `liquid/lfm-2.5-2.6b:free` (2.6B) succeeded consistently (ADR 0011). Prefer small models for the primary pick and always pair with `openrouter/free` as the fallback for this exact failure mode.

10. **[2026-09-22] Netbook has passwordless sudo; relay + tunnel are systemd-managed at a stable domain**
    Do instead: `talkpal-relay.service` (waitress, `~/talkpal-relay`) and `talkpal-tunnel.service` (`/usr/local/bin/ngrok http 5040 --url=https://viscous-landlady-reappoint.ngrok-free.dev` — current flag is `--url=`, not `--domain`) are both `enabled`, reboot-tested. ngrok free tier = 1 claimed domain per account (confirmed by dashboard UI, not docs) — don't try to claim a second one, reuse this one or use Cloudflare Tunnel (ADR 0004).

## User Directives
1. **[2026-09-21] New components for this ecosystem live nested inside this repo, not as sibling repos**
   Do instead: default new component work to a subfolder of `netbook/` (e.g. `netbook/alexa-talk-pal/`) unless told otherwise — user explicitly rejected a sibling `~/git/alexa-talk-pal` location.

2. **[2026-09-21] Napkin backlog tracks OPEN items only**
   Do instead: when a backlog item completes, move its line to the relevant component's `CHANGELOG.md` with the date — never leave a "done" marker in the napkin backlog.
