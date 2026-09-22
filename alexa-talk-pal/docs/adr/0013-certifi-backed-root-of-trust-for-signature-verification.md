# 0013. Build the Alexa signature chain's missing root-of-trust hop by hand, using certifi

- Status: accepted
- Date: 2026-09-22
- Deciders: solo (Luiz Wagner)

## Context

Backlog item 2 asked for an explicit go/no-go before Phase 2's live cutover
on the known gap from the first `app.py` build (see CHANGELOG 2026-09-21,
guardrail 7): `_verify_chain_signatures` checks that each cert in the
fetched `SignatureCertChainUrl` chain was signed by the next cert up
(leaf → intermediate → …), but never checked that the topmost fetched cert
itself chains to a root actually trusted locally. apt's `cryptography`
2.1.4 (ADR 0007) predates the library's own path-building APIs (added
~2.5+), which is why that hop was previously left as an open decision
rather than code.

Two things made a `cryptography`-version upgrade unnecessary to close this:

1. `x509.Name` in 2.1.4 already implements `__eq__`/`__hash__` (verified by
   reading `cryptography-2.1.4`'s `src/cryptography/x509/name.py` directly)
   — enough to index a small set of candidate roots by subject name without
   needing the newer path-building convenience API.
2. `certifi` is already an effective dependency: it ships transitively with
   `requests` (pinned in `requirements.txt`), and architecture.md §9.5
   already calls for keeping it current for outbound TLS to OpenRouter. So
   it's a trust store the relay already has on disk, not a new one to
   provision.

## Decision

Add `_verify_chain_root_of_trust` to `app.py`, called from
`verify_alexa_signature` right after `_verify_chain_signatures`. It:

1. Parses certifi's CA bundle once per process into a `subject -> [certs]`
   index (not per-request — an Atom N270 doing ~150 RSA/EC verifications
   per request would blow the 8s deadline, §5.3).
2. Takes the topmost cert in the fetched chain and looks up certifi roots
   sharing its issuer name.
3. Cryptographically verifies the topmost cert's signature against each
   matching candidate's public key (same `padding.PKCS1v15()` +
   `signature_hash_algorithm` pattern `_verify_chain_signatures` already
   uses), rejecting if none verify.

This is intentionally the same one-hop technique as the existing
chain-internal check, just anchored at a trust store instead of at the
next cert down. It handles both the common case (Amazon's chain response
omits the self-signed root, so the topmost fetched cert is an intermediate
whose issuer is the root) and the edge case (chain includes the self-signed
root itself) the same way, because signature verification — not name or
byte comparison — is what makes a same-named forgery fail: only the real
root's private key produces a signature its stored public key accepts.

Pinned `certifi==2021.10.8` explicitly in `requirements.txt` (previously
only an implicit transitive pin via `requests`), since it now backs a
security check, not just TLS convenience.

## Consequences

### Positive
- Closes the one deliberately-left-open gap from the first `app.py` build
  without a `cryptography` upgrade (which would risk ADR 0007's verified
  no-source-build state) and without a second library dependency.
- No new runtime dependency: `certifi` was already installed in the venv.
- No measurable latency cost: the certifi bundle parses once at first use
  and is cached for the process lifetime; each request only touches the
  handful of candidates sharing one issuer name, not all ~150 bundled
  roots.

### Negative / trade-offs
- Trust anchor is now certifi's general-purpose web CA bundle, not an
  Amazon-specific pinned root cert. Broader than strictly necessary, but
  matches how normal TLS clients validate and needs no separate
  Amazon-root file to source, verify, and keep updated by hand.
- Not exercised against a real Alexa request yet — this was written and
  reasoned through in a remote session with no LAN/SSH access to the
  netbook or a live `SignatureCertChainUrl` response to test against.
  **Follow-up:** before Phase 2's live cutover, run a real request (or the
  cached PEM chain from `https://s3.amazonaws.com/echo.api/...`) through
  `verify_alexa_signature` on the netbook and confirm it still accepts a
  genuine Amazon chain and rejects a tampered one.
- If certifi ever drops a root Amazon's chain depends on before the relay's
  pinned version is bumped, verification would start failing closed (safe
  direction, but worth knowing as the failure mode).

### Follow-ups
- Live-test on the netbook per the note above; this closes backlog item 2
  and guardrail 7 as *implemented*, not yet as *field-verified*.

## Alternatives considered

- **Upgrade `cryptography` past 2.1.4 for its real path-building API** —
  rejected: reopens ADR 0007's resolved no-source-build risk on 32-bit
  i686/Python 3.6 for a hop this approach closes without it.
- **Hand-bundle a single pinned Amazon/Starfield root PEM in the repo** —
  rejected: narrower trust surface than certifi's bundle, but adds a file
  to source correctly, keep updated, and re-verify if Amazon ever
  re-keys/cross-signs — more manual maintenance than reusing a trust store
  the relay already carries.
- **Accept the gap as-is and ship without root validation** — rejected:
  the explicit ask behind backlog item 2 was to close this or consciously
  accept it before live traffic; closing it was no harder than accepting
  it in writing, given certifi was already on hand.
