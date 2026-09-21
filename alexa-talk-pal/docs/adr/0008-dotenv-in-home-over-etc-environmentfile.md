# 0008. Secrets in `alexa-talk-pal/.env` (inside `/home`), not `/etc/talkpal/talkpal.env`

- Status: accepted
- Date: 2026-09-21
- Deciders: solo (Luiz Wagner)

## Context

`../architecture.md` §9.1 originally specified the OpenRouter API key live
at `/etc/talkpal/talkpal.env`, mode 600, loaded by the relay's systemd unit
via `EnvironmentFile=`. The stated reason: `/home` on the netbook is
synced to the Windows PC by Syncthing (see repo root README), so any
secret placed under `/home` — including inside this repo's working copy
on the netbook — would be silently replicated to a second machine.

When asked how to supply the key, the user opted for a plain `.env` file
(`alexa-talk-pal/.env`, gitignored, loaded via `python-dotenv`) instead,
and confirmed this is acceptable despite the Syncthing exposure noted
above.

## Decision

The relay reads its secrets from `alexa-talk-pal/.env` on the netbook
(inside `/home`, inside this repo's working copy), not from an
`/etc/talkpal/` `EnvironmentFile`. `.env` is gitignored; `.env.example`
documents the required keys (`OPENROUTER_API_KEY`, `ALEXA_SKILL_ID`,
`OPENROUTER_MODEL`) and is committed.

## Consequences

### Positive
- Simpler: one file, colocated with the code, no `/etc` setup step, no
  root-owned config to manage separately from the app.
- Matches an extremely common, well-understood convention (`.env` +
  `.gitignore` + `.env.example`) rather than a bespoke systemd pattern.

### Negative / trade-offs
- **The OpenRouter API key will be replicated by Syncthing to the Windows
  PC's `/home` backup**, exactly the exposure `architecture.md` §9.1 called
  out and tried to avoid. Accepted knowingly by the user — the PC is
  considered trusted.
- If the API key needs rotating because of this (or any) exposure, remember
  the copy on the Windows PC backup is also stale/needs manual cleanup;
  Syncthing doesn't revoke, it only replicates.
- Diverges from `architecture.md` §9.1's text — that section should be
  read as superseded by this ADR, not re-applied literally during Phase 1
  build-out.

### Follow-ups
- None blocking. If exposure ever becomes a concern, migrating to
  `/etc/talkpal/talkpal.env` later is a small, reversible change (just move
  the file and point `EnvironmentFile=`/`python-dotenv` at the new path).

## Alternatives considered

- **`/etc/talkpal/talkpal.env` + systemd `EnvironmentFile=`** — the
  original `architecture.md` §9.1 plan; rejected by the user in favor of
  the simpler, more familiar `.env` pattern, accepting the Syncthing
  replication trade-off.
