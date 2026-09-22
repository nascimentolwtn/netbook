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

1. **[2026-09-21] Before Phase 2 live cutover: review/harden signature verification's missing root-CA path-building**
   Do instead: `relay/app.py`'s `_verify_chain_signatures` checks internal chain-signature consistency, per-cert dates, SAN, and pins `SignatureCertChainUrl` to `s3.amazonaws.com`, but doesn't build a path to a locally trusted Amazon root (apt's `cryptography` 2.1.4 predates that API — see guardrail below). Decide explicitly whether the current mitigations are sufficient before wiring the real Alexa endpoint, or upgrade the crypto story.

2. **[2026-09-21] Phase 2: switch the reused skill's endpoint from Lambda ARN to HTTPS, edit interaction model off `TalkIntent` toward ADR 0005's shape**
   Do instead: expect several "Save Model" failures (ADR 0005); keep the two-turn fallback (LaunchRequest asks the question, then captures the follow-up) ready if the single-shot slot won't validate. Endpoint switch is per ADR 0003/0009 — same Skill ID, new endpoint config. The stable HTTPS endpoint to enter is `https://viscous-landlady-reappoint.ngrok-free.dev/alexa` (backlog item 1 closed — see CHANGELOG 2026-09-22).

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

6. **[2026-09-22] Local llama.cpp server (192.168.4.55:11434, `LFM2.5-2.6B-Q4_K_M.gguf`) is wired in as `INFERENCE_BACKEND=local` — needs `LOCAL_LLM_MAX_TOKENS=500`, not OpenRouter's 150**
   Do instead: its hybrid reasoning shares the same token budget as the spoken answer and truncates (or returns empty) at 150 — fixed by raising `max_tokens` for local calls only, no server changes needed (ADR 0012/0013). Don't assume 8B-A1B is still loaded — check `GET /api/tags` or `/v1/models` before testing if the model might have changed again.

7. **[2026-09-21] apt's `cryptography` 2.1.4 (ADR 0007) predates path-building APIs — signature verification can't build a trust path to a local Amazon root store**
   Do instead: `relay/app.py`'s verification pins `SignatureCertChainUrl` to `s3.amazonaws.com`/`/echo.api/`, checks per-cert validity dates, the leaf SAN (`echo-api.amazon.com`), and chain-internal signatures — but not root-of-trust path validation. Don't rediscover this while debugging signature rejects; see backlog item 1 for the explicit go/no-go decision.

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
