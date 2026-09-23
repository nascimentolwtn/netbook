# CLAUDE.md — alexa-talk-pal

Alexa skill relay: Echo → Alexa Skills Kit → ngrok tunnel → `relay/app.py` (Flask, on the
netbook) → OpenRouter (or local llama.cpp). See `docs/architecture.md` and `docs/adr/` for
design decisions.

## Deploy after changing relay code

`relay/` on the netbook (`~/talkpal-relay/` on `netbook@192.168.4.36`) is a **plain file copy,
not a git checkout** — committing and pushing in this repo does not update what's actually
running. Any change to `relay/*.py` is inert until deployed:

1. Copy the changed files to the netbook, e.g.:
   ```
   scp relay/app.py relay/conversation.py relay/fallback_messages.py \
       netbook@192.168.4.36:~/talkpal-relay/
   ```
   (match whichever files actually changed — don't overwrite `.env`, `daily_counter.json`, or
   `venv/`, none of which are part of this repo.)
2. Restart the service: `ssh netbook@192.168.4.36 "sudo systemctl restart talkpal-relay"`
3. Verify: `ssh netbook@192.168.4.36 "journalctl -u talkpal-relay -n 20"` should show a fresh
   startup line with no errors, and `curl https://viscous-landlady-reappoint.ngrok-free.dev/health`
   should return `{"status":"ok"}`.

An agent that edits `relay/` code and stops at "tests pass, committed" has not finished the
task — the live skill is still running whatever was deployed last, which can be commits behind
(confirmed 2026-09-23: the deployed `app.py` predated the multi-turn/pt-BR merge by several
commits, with no record of that gap anywhere). Always deploy and verify before reporting relay
work as done, the same way UI work isn't done until exercised in a browser.

## Monitoring live traffic

`ssh netbook@192.168.4.36 "journalctl -u talkpal-relay -f"` tails the relay's logs in real
time — the only way to see what a real Echo request actually did, since the console's Test tab
JSON Input/Output only covers simulator traffic, not a physical device. Success paths aren't
logged (only warnings/exceptions), so a request that produced no log line at all but still
failed on-device usually means it never reached the relay (tunnel/ngrok/console-endpoint issue,
not a relay bug) — check `systemctl status talkpal-tunnel` and the `/health` endpoint next.
