# 0004. ngrok free tier (persistent domain) for public ingress

- Status: accepted
- Date: 2026-09-20
- Deciders: solo (Luiz Wagner)

## Context

Alexa requires a stable public HTTPS hostname with a valid CA cert. The
earlier prototype at `/mnt/e/dev/alexa-talk-pal` also used ngrok, but on a
plain free tunnel with no reserved domain — its own README.md documents the
resulting pain directly: "Free ngrok URLs change every restart," and its
troubleshooting table lists "ngrok URL changed → Update `BACKEND_URL` in
Lambda env vars after each restart" as an expected, recurring problem.

`../architecture.md` §6 separately evaluated ngrok vs. Cloudflare Tunnel and
found Cloudflare's free tier requires an owned domain (~$10/yr) for a
*named* (stable) tunnel, while ngrok's free plan includes one auto-assigned
dev domain that persists across restarts at true $0.

## Decision

Use ngrok's free plan, but explicitly **claim the persistent free dev
domain** in the ngrok dashboard rather than running an unclaimed tunnel.
This directly fixes the restart-instability problem the prior prototype hit
and documented.

## Consequences

### Positive
- Fixes a real, previously-documented pain point (rotating URL requiring a
  manual downstream config update every restart).
- Still $0; systemd-managed like the relay and Syncthing.

### Negative / trade-offs
- Still dependent on ngrok's free tier continuing to offer a persistent dev
  domain.
- Hostname is auto-assigned and not pretty — cosmetic only, typed into the
  Alexa console once.
- Unverified whether the free-tier interstitial warning page (shown for
  browser traffic) affects Alexa's POST requests — flagged as a Phase 2
  check in `../architecture.md` §6.1.
- A 32-bit (`linux/386`) ngrok build must exist — flagged as the top Phase 0
  risk in `../architecture.md` §11.

### Follow-ups
- Verify `linux/386` build and interstitial behavior in Phase 0/2 before
  relying on this.

## Alternatives considered

- **Unclaimed/rotating free ngrok tunnel** — rejected; this is exactly the
  setup that caused the prior prototype's documented restart problem.
- **Cloudflare Tunnel** — kept as Option B in `../architecture.md` §6.2 if a
  domain is already owned or ngrok's 386 build/interstitial turns out to be
  a blocker.
- **Port-forwarding the home router** — rejected (exposes home IP, requires
  serving modern TLS from Xubuntu 18.04's aging OpenSSL).
