# 0014. Anchor the Alexa signature cert chain to the system CA store via `openssl verify`, harden the chain-URL check

- Status: accepted
- Date: 2026-09-22
- Deciders: solo (Luiz Wagner)
- Resolves: napkin backlog item 1, guardrail 7
- See: `docs/plans/0001-signature-verification-root-ca-decision.md` for the full options analysis

## Context

`relay/app.py`'s Alexa signature verification checked internal chain-signature
consistency, per-cert validity dates, the leaf SAN, and pinned
`SignatureCertChainUrl` to `s3.amazonaws.com`/`/echo.api/` — but never built a
path to a locally trusted root. Apt's `cryptography` 2.1.4 (ADR 0007) predates
the `x509.verification` path-building API, so this had been an open question
since the relay was first built.

While evaluating the working hypothesis (accept the current checks, pin
Amazon's root fingerprint once Phase 2 captures a live chain), two problems
surfaced:

1. `_validate_cert_chain_url` normalized the still-percent-encoded path before
   checking the `/echo.api/` prefix. `requests` decodes unreserved characters
   (`%2e` → `.`) before sending, so `…/echo.api/%2e%2e/evilbucket/c.pem`
   passed the check but could resolve to `…/evilbucket/c.pem` on the wire,
   depending on the installed urllib3 version (reproduced: WSL's urllib3 2.6.3
   strips the dot segment per RFC 3986; the netbook's apt urllib3 1.22 sends
   it literally and was safe only by accident).
2. The planned pin design (compare `certs[-1]` to a stored fingerprint) was
   wrong. A correctly served chain omits the root, so `certs[-1]` is an
   intermediate — one Amazon/DigiCert rotate far more often than roots. A real
   pin has to check that `certs[-1]` was *signed by* a stored root, not
   compare it to one.

Checking the netbook confirmed a path to real path validation exists today,
with no library or Python upgrade: `/usr/bin/openssl` is OpenSSL 1.1.1, which
does full RFC 5280 path building, and `ca-certificates` 20230311ubuntu0.18.04.1
ships 137 roots including Amazon Root CA 1-4 and the DigiCert/Starfield roots
that plausibly anchor `echo-api.amazon.com`.

## Decision

Anchor to the system CA store now, via the `openssl` CLI, rather than waiting
for Phase 2's live-capture pin or attempting a `cryptography` upgrade.

Two pieces, both in `relay/app.py`:

- **URL hardening** (`_validate_cert_chain_url`): reject any `%` or `.`/`..`
  path segment outright, keep the existing normpath+prefix check as a second
  layer, fetch with `allow_redirects=False` + require HTTP 200, and re-check
  the *prepared* URL `requests` will actually send (not just the parsed one)
  before trusting the response.
- **Trust anchor** (`_verify_chain_anchor`): on every cache miss, before
  caching, write the leaf and intermediates to temp PEM files and run
  `openssl verify -no-CApath -CAfile /etc/ssl/certs/ca-certificates.crt
  -untrusted <intermediates.pem> <leaf.pem>` via `subprocess.run` (fixed
  argv, no shell, 3s timeout). Pass only on exit code 0 **and** stdout ending
  `: OK`; fail closed (raise `SignatureVerificationError`) on any non-zero
  exit, timeout, or missing binary.

`cryptography` ≥ 42 (needed for `x509.verification`) requires Python ≥ 3.7;
the netbook's system Python is 3.6, and building a newer `cryptography` from
source needs a Rust toolchain with no i686 wheels available — the exact risk
class ADR 0007 was accepted to avoid, now larger. **Not viable on this
hardware**, confirmed again here rather than revisited later.

## Consequences

### Positive
- The relay has a real root of trust from the first live request, with no
  library upgrade, no Python upgrade, and no Rust build on the Atom.
- The encoded-traversal bypass is closed twice: once in URL parsing, once
  because a forged chain can't pass the anchor even if fetched.
- Amazon cert rotation needs no code change — any chain ending in a
  Mozilla-trusted root with the right SAN is accepted, same as
  `ask-sdk-webservice-support`'s own approach (certifi + pyOpenSSL).
- The existing checks (dates, SAN, chain-internal signatures, body signature,
  timestamp) stay as defense in depth; nothing was removed.
- Tested locally (11/11) and on the netbook relay venv (11/11, cryptography
  2.1.4 needed explicit `backend=default_backend()` in test fixtures that
  newer versions default automatically). `openssl verify`'s exit-code/`: OK`
  contract confirmed by hand on the netbook against a real chain
  (openrouter.ai) and a self-signed cert before relying on it in tests.
  Deployed to `~/talkpal-relay/`, service restarted, smoke-tested with
  `DEBUG_SKIP_SIGNATURE` off: `/health` 200 locally and via the public tunnel,
  unsigned POST to `/alexa` correctly gets `400 signature verification
  failed`.

### Negative / trade-offs
- New runtime dependency on `/usr/bin/openssl` and its exit-code/`: OK`
  output contract — covered by tests, fails closed if the binary is missing.
- A subprocess + temp files on the cache-miss path (roughly once per Amazon
  cert rotation); bounded by a 3s timeout inside the ~8s Alexa budget.
- Fail-closed means the skill goes silent (HTTP 400) if a served chain
  doesn't validate — correct behavior for a security check, but something to
  watch for on the very first live request in Phase 2.
- Trust set is "every root Mozilla trusts" (137), looser than a single pinned
  root. Standard practice; narrowed by the Phase 2 pin below.
- The system CA bundle ages with Bionic ESM; revisit if Amazon ever moves to
  a root newer than the 2023 bundle.

### Follow-ups
- **Phase 2 (blocked on the endpoint switch, separate work)**: capture the
  real chain from the first live signed request (the cache-miss INFO log now
  records subject/issuer/SHA-256 per cert), confirm the anchor accepted it,
  commit the fixture PEM, and optionally narrow the trust set to a small
  `echo-api-roots.pem` with a startup fingerprint check — **never** compare
  `certs[-1]` to a fingerprint directly, since it's an intermediate.
- If a live signature rejection ever shows "chain anchor" in the log: Amazon
  likely moved roots. Capture the new chain, confirm the new root is
  legitimate, add it to the (eventual) pinned file. Never disable
  verification to work around it.
- Consider pinning `urllib3` in `requirements.txt` to document the
  environment, even though it's no longer load-bearing for security now that
  c1+c2 are both in place.

## Alternatives considered

- **Accept current checks now, pin Amazon's root fingerprint in Phase 2.**
  Rejected as the primary plan — leaves the encoded-traversal bypass open
  (safe only by an unpinned urllib3 version), runs the first live traffic
  with no anchor at all, and its pin design would have pinned an
  intermediate, not a root. Its end state survives as an optional Phase 2
  layer on top of the system-store anchor.
- **Upgrade `cryptography` to get `x509.verification`.** Rejected — needs
  Python ≥ 3.7 (netbook has 3.6) plus a Rust source build with no i686
  wheels. ADR 0007's reasoning applies even more strongly here.
- **Hand-rolled one-step anchor in Python** (find the bundle cert whose
  subject matches the top cert's issuer, verify its signature and
  `BasicConstraints(ca=True)` by hand). Kept as a fallback only if `openssl
  verify` proves unreliable — not needed, since the CLI's contract was
  confirmed on the netbook.
- **apt `pyOpenSSL` 17.5 `X509StoreContext`.** Rejected — no untrusted-chain
  parameter until 20.0, so intermediates would need to go into the trust
  store itself; a new system package for no advantage over the `openssl` CLI
  approach.
