# 0007. Use apt's `python3-cryptography` via venv `--system-site-packages`, not a pip build

- Status: accepted
- Date: 2026-09-21
- Deciders: solo (Luiz Wagner)

## Context

ADR 0003 flagged the `cryptography` package (needed for Alexa request
signature verification) as the plan's single biggest unresolved risk:
possibly no prebuilt wheel for 32-bit i686 on Python 3.6, forcing a slow
source build on the Atom N270, with no fallback since Lambda was rejected.

Live SSH check on the netbook (2026-09-21):

- `python3-cryptography` is **already installed via apt**: version 2.1.4,
  native i386 binary package — no compile needed, ever.
- All submodules the relay needs for signature verification import cleanly:
  `cryptography.x509`, `hazmat.backends`, `hazmat.primitives.asymmetric.padding`,
  `hazmat.primitives.hashes`, `x509.oid.NameOID`.
- `python3 -m venv --system-site-packages` fails on this box with
  `ensurepip is not available` (python3-venv's pip bootstrap is broken/
  missing); `python3 -m venv --system-site-packages --without-pip` works
  and the venv correctly sees the apt-installed `cryptography` 2.1.4.
- `gcc`/`cc` are present anyway, so a source build remains possible if ever
  needed for something else.
- 1498MB RAM free of 2002MB at idle — plenty of headroom for the relay.

## Decision

The relay's Python environment is a venv created with
`python3 -m venv --system-site-packages --without-pip`. `cryptography` is
**not** installed via pip at all — the venv inherits it from the apt
package `python3-cryptography` already on the system. Other pure-Python
deps (Flask, waitress) are installed into the venv with pip as normal
(system pip 9.0.1, or a `get-pip.py` bootstrap into the venv if needed).

## Consequences

### Positive
- Eliminates the plan's top-risk unknown entirely — no source build, no
  wheel availability gamble, verified working today.
- Removes the last open Phase 0 blocker from ADR 0003's follow-ups.

### Negative / trade-offs
- `cryptography` is pinned to whatever apt ships (2.1.4, from 2018) — old,
  but sufficient for the SHA1withRSA + X.509 chain validation Alexa
  signature verification needs; not independently upgradable via pip
  without reintroducing the build risk.
- The venv is no longer fully isolated (`--system-site-packages` exposes
  all system dist-packages, not just `cryptography`); acceptable trade-off
  for a single-purpose relay service.
- `ensurepip`/`python3-venv`'s pip bootstrap is broken on this box — any
  future venv creation must remember `--without-pip`.

### Follow-ups
- None — this closes the Phase 0 top risk from ADR 0003. Phase 1 relay
  build can proceed directly to implementation.

## Alternatives considered

- **pip-install `cryptography` into an isolated venv** — the originally
  planned approach; unnecessary now that apt already provides a working
  native package, and pip 9.0.1 (2016) may not even fetch modern PyPI
  wheels/sdists reliably.
- **Go-relay fallback** (architecture.md §5.1) — moot; only needed if
  `cryptography` packaging had failed, which it didn't.
