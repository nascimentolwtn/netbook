# Plan 0001 (proposed ADR 0014). Signature verification: anchor the cert chain to the system CA store before live cutover, pin Amazon's root later

- Status: proposed (plan only. Not yet approved, not yet implemented)
- Date: 2026-09-22
- Deciders: solo (Luiz Wagner)
- Resolves (once accepted): napkin backlog item 1, guardrail 7
- Depends on: the separate Alexa Skill endpoint-switch plan (ADR 0003/0009), which is needed only for the Phase 2 capture step, not for the main fix

> **Short version:** the working hypothesis was option (a): accept the current
> checks now and pin Amazon's root fingerprint once a live request can be
> captured. While checking that, I found two problems with it. First, the
> `/echo.api/` path check can be bypassed with a percent-encoded `..`. It is
> safe on the netbook today only because of the installed urllib3 version.
> Second, an anchor to a trusted root is possible **now**, with no library
> upgrade and without knowing Amazon's root in advance: shell out to the
> system `openssl verify` (OpenSSL 1.1.1) against the system CA bundle. The
> recommendation is option (c): harden the URL check and add the OpenSSL
> anchor **before** the endpoint switch, then use the first live request to
> confirm it and (optionally) narrow the trusted roots.

---

## Context

### What `relay/app.py` does today (lines ~163-293)

`verify_alexa_signature` runs these checks in order:

1. `_validate_cert_chain_url`: scheme `https`, host `s3.amazonaws.com`
   (case-insensitive), port 443, and `os.path.normpath(parsed.path)` must
   start with `/echo.api/`.
2. `_get_cert_chain`: `requests.get(url)` with default TLS verification.
   Parses every PEM block and caches the result per URL forever (dates are
   rechecked on each request, so the unbounded cache is fine).
3. `_verify_chain_dates_and_san`: every cert must be inside
   `not_valid_before..not_valid_after`, and the leaf SAN must include
   `echo-api.amazon.com`.
4. `_verify_chain_signatures`: each cert's signature must verify against the
   **next cert in the same file**. This proves the file is internally
   consistent. It does **not** prove the chain ends at anything we trust.
5. `_verify_body_signature`: SHA1withRSA over the raw body with the leaf key.
6. `_verify_request_timestamp`: ±150 s.
7. (In the `/alexa` route) `applicationId == ALEXA_SKILL_ID`.

### What is missing, and why it matters more than it first appears

There is no root of trust. Any chain that is internally consistent, has
valid dates and has the right SAN passes. An attacker can mint one in about
ten lines with a self-signed "root" whose CN is anything, because SANs are
not checked by anyone unless a CA signs them. Two things then stand between
an attacker and a working forgery:

- **Where the chain is fetched from.** The design assumes that only Amazon
  can put content under `https://s3.amazonaws.com/echo.api/`, because the
  path-style bucket `echo.api` belongs to Amazon.
- **`applicationId`.** This does not help against a forgery. The field is in
  the attacker-controlled body, so it only works as a guess-resistant secret.
  It is not in git (checked: no `amzn1.ask.skill` string is tracked), but it
  is obscurity, not authentication.

So the whole security argument rests on the fetch location. That location
is weaker than it looks.

### Finding: the `/echo.api/` prefix check can be bypassed with an encoded `..`

`_validate_cert_chain_url` runs `normpath` on the **still-percent-encoded**
path. `requests` then decodes the unreserved characters before sending,
because `.` is unreserved and `%2e` becomes `.`. Reproduced on 2026-09-22:

```
URL:                    https://s3.amazonaws.com/echo.api/%2e%2e/evilbucket/c.pem
our normpath check:     /echo.api/%2e%2e/evilbucket/c.pem   -> startswith('/echo.api/') == True  (passes)
requests prepared URL:  https://s3.amazonaws.com/echo.api/../evilbucket/c.pem
```

What happens next depends on urllib3:

| Environment | urllib3 | Path actually sent | Effect |
|---|---|---|---|
| WSL dev box | 2.6.3 | `/evilbucket/c.pem` (dot segments removed, RFC 3986) | **Fetches from an attacker-owned bucket.** The forged chain passes every current check. |
| Netbook relay venv (`/home/netbook/talkpal-relay/venv`) | **1.22** (apt `/usr/lib/python3/dist-packages`, via `--system-site-packages`) | `/echo.api/../evilbucket/c.pem` (sent literally) | Most likely safe, because S3 treats `../evilbucket/c.pem` as a literal key in the `echo.api` bucket. This is **unverified**. |

The netbook is safe today only by accident. Nothing pins urllib3, and any
change that pulls in urllib3 ≥ 1.25 (a pip install into the venv, or a
different requests pin) silently opens the hole. The hole also depends on S3
still serving path-style requests for a newly created us-east-1 bucket. AWS
has announced deprecation plans for that, so it may already be closed on
Amazon's side. I did not test it against real S3 and would not rely on
either result. `requests.get` also follows redirects by default, which is
another way the fetch could land somewhere other than `echo.api`.

A real root-of-trust check closes this whole class of bug: a chain served
from anywhere is useless unless a trusted CA issued a cert with
`echo-api.amazon.com` in its SAN. The missing check is the one doing the
heavy lifting, not decoration.

### What we know about the environment (verified over SSH, 2026-09-22)

- `python3-cryptography` 2.1.4 (apt, i386). It has
  `Certificate.fingerprint()`, `public_key().verify()`, `issuer`/`subject`
  comparison and extensions. It has **no** path-building / `x509.verification`
  API.
- `/usr/bin/openssl`: **OpenSSL 1.1.1 (11 Sep 2018)**. It does full RFC 5280
  path building and validation (`openssl verify -CAfile … -untrusted …`).
- `ca-certificates` **20230311ubuntu0.18.04.1**, 137 roots in
  `/etc/ssl/certs/ca-certificates.crt`. This includes **Amazon Root CA 1-4**,
  **DigiCert Global Root CA / G2 / G3** and the **Starfield** roots, which
  are the plausible anchors for a current `echo-api.amazon.com` chain.
- `certifi` in the relay venv is the apt 2018.01.18 shim, and it points
  `certifi.where()` at that same system bundle. That is the bundle that
  already authenticates the TLS fetch to S3.
- `pyOpenSSL` is **not installed**.
- The relay is not yet receiving real Alexa requests, because the skill still
  points at the old Lambda ARN (ADR 0009). No live chain has been captured.
  The commonly cited `echo-api-cert-4.pem` is a dead artifact: 2 certs, both
  expired (2017 / 2023), Symantec/VeriSign-issued. The current date check
  correctly rejects it.
- Threat model: unpublished, development-mode household skill (ADR 0006). The
  realistic attacker is opportunistic, meaning someone who finds the ngrok
  hostname and wants a free LLM proxy billed to our OpenRouter key. Daily cap
  `DAILY_CAP=45` limits the damage. We are not defending against a
  nation-state MITM on S3.

---

## Options

### (a) Accept the current checks now, pin Amazon's root fingerprint in Phase 2

This is the working hypothesis. Ship as is, switch the endpoint, capture the
real chain, then pin the root.

- **Pro:** no work now. The pin needs no library change.
- **Con:** it leaves the encoded-traversal bypass open, which is safe only
  thanks to an unpinned urllib3 version.
- **Con:** the first live requests, and every request until someone gets
  round to Phase 2, run with no anchor at all.
- **Con: the pin as described ("compare `certs[-1]` to a fingerprint") would
  not work.** Correctly configured chains **omit the root**. The stale
  `cert-4` file did too (leaf + intermediate only). `certs[-1]` will almost
  certainly be an **intermediate**, which Amazon/DigiCert rotate far more
  often than roots. A true root pin has to check that `certs[-1]` was
  **signed by** a stored root cert, not compare it to one. That needs the
  root's public key on disk, not just a fingerprint. It is still cheap, but it
  is a different check from the one planned.

### (b) Upgrade `cryptography` to get real path validation

The API that would do this is `cryptography.x509.verification`
(`PolicyBuilder` / `Store`). It first appeared in **cryptography 42.0**
(2024). That creates a chain of blockers:

- cryptography 42 requires Python **≥ 3.7**. The netbook's system Python is
  **3.6**, and the last cryptography release supporting 3.6 is the 40.x line,
  which lacks the API. **A library upgrade alone is impossible. It needs a
  Python upgrade first.**
- A newer Python on Bionic i386 is not realistic: deadsnakes has no i386
  builds for current versions, so it would mean building CPython from source
  on the Atom.
- cryptography ≥ 35 needs a **Rust toolchain** to build from source. PyPI has
  no i686 wheels for these versions (worth confirming if this path is ever
  reopened), so this is a Rust + OpenSSL-headers build on an N270. That is
  the exact risk ADR 0007 was accepted to avoid, now much bigger.
- It would bring back the `ensurepip`-broken venv problem and pip 9.0.1's
  inability to fetch modern metadata.

**Verdict:** not viable on this box. Only revisit if the relay moves to
different hardware.

### (c) Anchor to the system CA store with `openssl verify`, and harden URL checks (recommended)

Two independent pieces, both implementable today:

**(c1) URL hardening:**

- Reject any `%` in the path. Real Amazon URLs have none, so this is the
  simplest correct rule.
- Reject any `.` or `..` path segment.
- Keep the existing `normpath` + prefix check as a second layer.
- Use `requests.get(..., allow_redirects=False)` and require
  `status_code == 200`.
- Optionally check that the URL `requests` will actually send
  (`requests.Request('GET', url).prepare().url`) still starts with
  `https://s3.amazonaws.com/echo.api/`, so the check tests what goes on the
  wire and not what we parsed.

**(c2) Trust anchor:** after parsing the chain, write it to a temp file and
run:

```
openssl verify -no-CApath -CAfile /etc/ssl/certs/ca-certificates.crt \
               -untrusted <intermediates.pem> <leaf.pem>
```

This uses `subprocess.run([...], timeout=…)` with a fixed argv and no shell,
and it passes the check only on exit code 0 **and** stdout ending in `: OK`.
OpenSSL does the real path building, including basicConstraints, pathLen,
key usage, name constraints and validity. That is the thing apt's
cryptography can't do.

- It runs only on a **cache miss**, which is roughly once per Amazon cert
  rotation. The fork/exec cost on the Atom (tens of ms) never lands on the
  normal per-request path.
- It needs no knowledge of Amazon's root. Any chain that ends in a
  Mozilla-trusted root and has the right SAN is accepted. That is exactly
  Amazon's documented requirement, and it is what `ask-sdk-webservice-support`
  does (via certifi + pyOpenSSL).
- The existing Python checks (dates, SAN, chain signatures, body signature,
  timestamp) all stay as defense in depth. Nothing is removed.

**Pros:**

- Closes the root-of-trust gap **before** the first live request.
- Makes the traversal class of bug harmless even if c1 misses a variant.
- Uses tools already on the box, with no new packages.
- Survives Amazon cert rotation with no code change.

**Cons:**

- Depends on the `openssl` CLI and its output/exit-code behaviour. OpenSSL
  ≥ 1.1.0 returns non-zero on failure, but that must be confirmed on this box
  by the tests below.
- The trust store is "every root Mozilla trusts" (137 of them), which is
  looser than a single pinned root. That is standard practice, and it is
  tightened in Phase 2.
- The system bundle ages with Bionic, which is ESM-only. That only matters if
  Amazon moves to a root newer than 2023, and a failure would be loud
  (fail-closed), not silent.

### (d) Other options considered and ranked below (c)

- **(d1) Hand-rolled one-step anchor in Python.** Find the bundle cert whose
  `subject == certs[-1].issuer`, verify `certs[-1]`'s signature with its key,
  and check `BasicConstraints(ca=True)` on every non-leaf. This is doable
  with cryptography 2.1.4 and avoids a subprocess. But it is more hand-rolled
  crypto logic in the one place §9.2 calls the front door lock, and it would
  miss pathLen, keyUsage and name constraints unless each is added by hand.
  **Keep it as the fallback** if `openssl verify` turns out to be unreliable
  on the box.
- **(d2) apt `python3-openssl` (pyOpenSSL 17.5) `X509StoreContext`.** This is
  real OpenSSL validation in-process. But 17.5 has no untrusted-chain
  parameter (added in 20.0), so intermediates would have to go into the trust
  store. That is workable but subtle, and it adds a new system package. It
  offers no advantage over c2.
- **(d3) Pin a captured root only (option (a)'s end state) without the
  system store.** This is the strictest check, but it breaks on a root
  change and needs the capture first. It is better as a **Phase 2 layer on
  top of c2** than as a replacement.

---

## Decision (proposed)

Adopt **(c)**:

1. Implement URL hardening (c1) and the `openssl verify` system-store anchor
   (c2) **before** the Alexa endpoint is switched to the relay, and fail
   closed.
2. After the switch, run **Phase 2**: capture the real chain from the first
   live request, confirm c2 accepts it, and add a narrow pin on the actual
   Amazon anchor root(s) on top of c2.
3. Record that a `cryptography` upgrade (b) is not viable on this hardware
   and Python version.

This supersedes the session's working hypothesis (a). The reasons are the
encoded-traversal finding, the fact that a real anchor is available now at
low cost, and the `certs[-1]`-is-an-intermediate correction to the pin
design.

---

## Consequences

### Positive

- The relay's front door has a real root of trust from the first live
  request, with no library upgrade, no Python upgrade and no Rust build (ADR
  0007 stands).
- The traversal bypass is closed twice: once in URL parsing, and once because
  a forged chain can't pass the anchor even if it is fetched.
- Amazon cert rotation needs no action. Phase 2's pin adds strictness without
  becoming a prerequisite for going live.
- The first live requests (endpoint switch) double as the acceptance test for
  the anchor.

### Negative / trade-offs

- A new runtime dependency on `/usr/bin/openssl` and on the exact CLI
  contract (exit code + `: OK`). It is covered by tests, and it fails closed
  if the binary is missing.
- A subprocess plus temp files on the cache-miss path. It needs a timeout
  (suggest 3 s, inside the ~8 s Alexa budget) and a `try/finally` cleanup.
- Fail-closed means the skill goes silent (HTTP 400, "there was a problem
  with the requested skill's response") if the chain doesn't validate. This
  could happen on day one if Amazon's served chain is incomplete or anchors to
  a root missing from the 2023 bundle. That is the correct failure mode for a
  security check, and the endpoint-switch test catches it immediately.
- The system CA bundle ages with Bionic ESM. Revisit if Amazon changes its
  root CA.

### Follow-ups

- Phase 2 hardening (below).
- If the endpoint switch shows a rejection, look at the logged `openssl`
  stderr first (see the logging step). Do not add a bypass. The
  `DEBUG_SKIP_SIGNATURE` switch exists only for local testing and must stay
  off.
- Decide whether to pin `urllib3` in `requirements.txt`. With c1 + c2 this
  no longer matters for security, but it documents the environment.
- Out of scope, noted only: each distinct valid-looking `SignatureCertChainUrl`
  costs one outbound fetch (≤ 7 s, one waitress thread). This is small and
  bounded by c1, so no action is planned.

---

## Implementation sketch (for the implementing session, not done here)

All changes are in `relay/app.py`, in the signature-verification block.

- `_validate_cert_chain_url(url)`: add the `%` / dot-segment rejections and
  optionally the prepared-URL re-check (c1). Keep the existing checks.
- `_get_cert_chain(url)`: pass `allow_redirects=False`, require status 200,
  then call `_verify_chain_anchor(certs)` **before caching**, so only anchored
  chains are ever cached. On a cache miss, log at INFO the URL and, for each
  cert, its subject, issuer, not_after and
  `cert.fingerprint(hashes.SHA256()).hex()`. These are public certs, not
  secrets. Utterances are never logged.
- New `_verify_chain_anchor(certs, ca_file=SYSTEM_CA_BUNDLE, at_time=None)`:
  1. Write `certs[0]` to `leaf.pem` and `certs[1:]` to `untrusted.pem` in a
     `tempfile.TemporaryDirectory()`.
  2. Run `openssl verify -no-CApath -CAfile <ca_file> [-attime <epoch>]
     [-untrusted untrusted.pem] leaf.pem` with `timeout=3`.
  3. On a non-zero exit, a timeout, a missing binary, or stdout that is not
     `leaf.pem: OK`, raise `SignatureVerificationError` and log stderr.

  `ca_file` and `at_time` are parameters **only** so the tests can use a
  throwaway CA and a frozen clock. Production always uses the system bundle
  and the current time.
- Update the comment in `_verify_chain_signatures` so it no longer says there
  is no root validation.
- Docs (after approval, by the orchestrating session): turn this plan into
  ADR 0014. Update architecture.md §4.2 step 3 to say "validated with
  `openssl verify` against the system CA bundle". Close napkin backlog item 1
  into CHANGELOG, and rewrite guardrail 7.

### Tests (new `relay/tests/test_signature.py`, stdlib `unittest`, run on the netbook with the relay venv)

There is no test suite yet, so this creates one. Fixtures are generated at
test time with cryptography 2.1.4's `CertificateBuilder`. No network is
needed.

Anchor (c2), using a throwaway test CA written to a temp `ca_file`:

1. Test root → intermediate → leaf (SAN `echo-api.amazon.com`), root in
   `ca_file`: **passes**.
2. **Forged chain:** an attacker's self-signed root (not in `ca_file`) →
   intermediate → leaf, all internally consistent, valid dates, correct SAN,
   and a correctly signed body: **rejected by the anchor**. It still passes
   the old checks. This is the key regression test, because it is exactly the
   attack the current code accepts.
3. The same forged chain with the attacker root **included** in the PEM file:
   **rejected**. A root sent inside the chain must not be trusted.
4. Intermediate missing from the chain: **rejected**.
5. Intermediate without `BasicConstraints(ca=True)` issuing the leaf:
   **rejected**. This proves OpenSSL, not our code, enforces CA-ness.
6. Expired intermediate, via `at_time` past its `not_after`: **rejected**.
7. `openssl` not on PATH (monkeypatch the binary path to a non-existent
   file): **rejected**, i.e. fails closed.
8. Exit-code contract: a real `openssl verify` failure returns non-zero on
   this box. This test pins the assumption from the "Negative" list above.

URL hardening (c1). Each of these is **rejected**:

- `…/echo.api/%2e%2e/evilbucket/c.pem`
- `%2E%2E` (uppercase)
- `…/echo.api/../x`
- `…/echo.api/./../x`
- `%2F`
- `http://…`
- `:8443`
- `Echo.api` (path is case-sensitive per Amazon's spec)
- `https://s3.amazonaws.com@evil.com/echo.api/x`

These are **accepted**:

- `https://s3.amazonaws.com/echo.api/echo-api-cert-N.pem`
- `https://S3.AMAZONAWS.COM:443/echo.api/…` (host is case-insensitive)

Redirects: stub `requests.get` to return a 301 and check that the fetch is
**rejected**.

---

## Phase 2 hardening: capture, confirm, pin

Blocked on the endpoint switch (separate plan). The Developer Console **Test**
tab sends real signed requests, so a physical Echo is not needed.

1. **Capture.** After the switch, send one test utterance. The cache-miss INFO
   log from the step above records the real `SignatureCertChainUrl` and each
   cert's subject, issuer and SHA-256. No debug hook is needed: this logging
   stays in permanently, and it fires once per rotation. From the WSL box,
   `curl -o` the same URL to get the PEM, and commit it as
   `relay/tests/fixtures/echo-api-chain-<YYYYMMDD>.pem`. The certs are
   public.
2. **Confirm c2 on real data.** The live request succeeded, so the anchor
   accepted it. Also run `openssl verify -show_chain` by hand on the netbook
   against the fixture, and record which bundle root anchored it (expect
   Amazon Root CA 1 or a DigiCert Global Root). Add a test that verifies the
   fixture against the **system** bundle, with `at_time` frozen inside its
   validity window, so it stays green after the certs expire.
3. **Pin (optional strictness on top of c2).** Make the trust set narrower
   than 137 roots. **Don't** compare `certs[-1]` to a fingerprint, because it
   is an intermediate. Two correct ways:
   - **Preferred:** instead of the full system bundle, pass
     `-CAfile relay/trust/echo-api-roots.pem`. That file holds just the one
     or two root certs observed in step 2, copied from
     `/etc/ssl/certs/<Name>.pem`, plus their Amazon/DigiCert siblings for
     rotation headroom. Alongside it, store a `SHA256` fingerprint list that a
     startup check compares against the file, so a tampered trust file is
     detected. It is the same code path as c2, with a smaller store.
   - **Alternative:** keep the system bundle, and after success require that
     the anchor printed by `-show_chain` has a SHA-256 in a hardcoded
     allowlist.
4. **Pin tests:**
   - The captured real chain against the pinned file (frozen `at_time`):
     **passes**.
   - A valid, publicly trusted chain that is not Amazon's, for example a
     fixture of some other public site's chain whose root is in the system
     bundle but not in the pin file, with its SAN hacked in via a test CA
     so that only the root differs: **rejected under the pin, accepted under
     the plain system bundle.** This proves the pin adds strictness.
   - Test-CA chain (from the anchor tests): **rejected** under the pin.
   - Pin file with one byte changed: the startup check **refuses to start**
     (or rejects all requests).
5. **Rotation runbook line (napkin guardrail):** "Skill suddenly rejected with
   'chain anchor' in the log → Amazon moved roots; capture the new chain,
   confirm the new root is legit, add it to `echo-api-roots.pem`, and never
   disable verification."

---

## Checklist

**Before the endpoint switch (this plan):**

- [ ] Human reviews and approves this plan, or picks (a)/(d1) instead.
- [ ] On the netbook, confirm the `openssl verify` exit-code / `: OK`
      contract by hand, using a known-good chain (e.g. openrouter.ai's) and a
      self-signed cert.
- [ ] Implement c1 (URL hardening + `allow_redirects=False`).
- [ ] Implement c2 (`_verify_chain_anchor`, before caching, fail-closed,
      3 s timeout).
- [ ] Add cache-miss INFO logging of URL + per-cert subject/issuer/SHA-256.
- [ ] Add `relay/tests/test_signature.py` (anchor tests 1-8, URL cases,
      redirect case). Run it on the netbook in the relay venv.
- [ ] Deploy to `/home/netbook/talkpal-relay/`, restart the service, and
      smoke test with `DEBUG_SKIP_SIGNATURE` **off**. Expect a clean 400 for
      unsigned requests.
- [ ] Orchestrator: write ADR 0014 from this plan. Update architecture.md
      §4.2 step 3, the napkin (close backlog item 1 to CHANGELOG, rewrite
      guardrail 7) and CHANGELOG.

**After the endpoint switch (Phase 2):**

- [ ] Send one Test-tab utterance. Confirm it succeeds and the cache-miss log
      shows the chain.
- [ ] Commit the fixture PEM and add the real-chain test.
- [ ] Record the anchor root (`-show_chain`) in ADR 0014's follow-ups.
- [ ] Optional: switch to the pinned `echo-api-roots.pem` and add the pin
      tests.
- [ ] Add the rotation runbook line to the napkin.

---

## Risks and open unknowns

- **Is the traversal exploitable against real S3?** It is unverified in both
  directions: whether path-style access for newly created buckets still
  works, and whether S3 ever normalizes `..`. I deliberately did not test it
  live. The plan doesn't depend on the answer.
- **Is the real chain complete?** Does Amazon's current PEM include every
  intermediate needed to reach a bundle root? The Test tab shows this right
  after the switch. If an intermediate is missing, the fix is to ship that
  intermediate as an extra `-untrusted` file, **not** to relax the check.
- **Is the current Amazon anchor in the 2023 bundle?** It is very likely
  (Amazon Root CA 1-4 and the DigiCert roots are present), but it can't be
  proven until capture.
- **`openssl verify` exit-code behaviour on this exact build** has to be
  confirmed by hand. Anchor test 8 then guards it.
- **The claims about cryptography versions and Python-support** (42.0 for
  `x509.verification`, no Python 3.6 support after 40.x, no i686 wheels) are
  from memory of the upstream changelog, not checked live against PyPI today.
  They only strengthen a "not viable" verdict that already stands on the
  Python 3.6 + Rust-build-on-Atom grounds.
- **Residual risk after (c).** A fraudulent cert from any of 137 trusted
  roots with SAN `echo-api.amazon.com` is the same risk class as the TLS
  fetch itself. It is acceptable for a household dev-mode skill, and Phase 2
  pinning narrows it further.

## Alternatives considered

- **(a) Accept the current checks and pin the root in Phase 2.** Rejected as
  the *primary* plan. It leaves the encoded-traversal bypass open (safe only
  through an unpinned urllib3 1.22), it runs the first live traffic with no
  anchor, and its pin design (`certs[-1]` fingerprint) would pin an
  intermediate. Its end state survives as Phase 2 step 3.
- **(b) Upgrade `cryptography` for `x509.verification`.** Rejected. It needs
  cryptography ≥ 42, which needs Python ≥ 3.7, plus a Rust source build on
  an i686 Atom. ADR 0007's reasoning applies even more strongly.
- **(d1) Hand-rolled one-step anchor in Python.** Held as the fallback if the
  `openssl` CLI proves unreliable. It is more custom crypto logic, and it
  misses constraints OpenSSL enforces.
- **(d2) apt pyOpenSSL `X509StoreContext`.** Rejected. It is a new package,
  and 17.5 lacks the untrusted-chain parameter. It offers nothing over c2.
- **(d3) Root pin only, without the system store.** Rejected as a first
  step, because it needs a capture that can't happen yet. It is kept as an
  optional Phase 2 layer.
