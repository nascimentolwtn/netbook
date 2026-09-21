# 0003. Self-hosted HTTPS relay vs. AWS Lambda ARN endpoint

- Status: accepted
- Date: 2026-09-21
- Deciders: solo (Luiz Wagner)

## Context

`../architecture.md` (§4, §6) currently plans a **"Provision your own"
HTTPS endpoint**: Alexa POSTs through a tunnel (ngrok) straight to a Flask
relay on the netbook. Amazon requires any such self-hosted endpoint to
independently verify the request signature — fetch and cache Amazon's
`SignatureCertChainUrl` cert chain, validate it, verify a SHA1withRSA
signature against the *raw* request body, and reject stale timestamps. This
is called out in `../architecture.md` §4.2 as real, non-optional work and
"the highest-risk unknown in the plan" (compounded by a real risk that the
`cryptography` Python package has no prebuilt wheel for 32-bit i686, forcing
a slow source build on the Atom).

The earlier prototype at `/mnt/e/dev/alexa-talk-pal` used a **different**
endpoint type: an **AWS Lambda ARN** (`alexa-skill/lambda-relay.mjs`,
Node.js, deployed via the Lambda console, README.md Step 4–5). Skills backed
by a Lambda ARN are trusted by Amazon through IAM/resource-based policy —
**no manual signature verification is needed at all** in that shape. The
Lambda function there was a thin relay that simply forwarded the raw Alexa
event to the PC's ngrok URL and passed the JSON reply straight back.

The user reports that prototype was "very difficult to configure inside
[the] Alexa account," despite it not requiring any signature-verification
code. The prototype's own `docs/` folder contains no Alexa-specific
troubleshooting notes (it turned out to hold unrelated leftover docs from
other projects), so the exact cause of that difficulty is not confirmed from
notes — but the prototype's own README.md (Step 4) is telling: it walks
through creating the Lambda function, setting the `BACKEND_URL` env var, and
copying the ARN into the Alexa console, but **never mentions adding an
"Alexa Skills Kit" trigger** to the Lambda function with the skill ID
entered. That trigger is what actually grants Alexa's service permission to
invoke the Lambda; skipping it produces a silent authorization failure with
an unhelpful error in the Alexa console — a well-known first-timer gotcha
specific to Lambda-backed skills, and one that matches "difficult to
configure inside the Alexa account" (UI/permissions friction) far better
than a code-level problem would.

## Decision

**(a) Self-hosted HTTPS relay + own signature verification**, as already
planned in `../architecture.md` §4/§6. AWS Lambda is not used.

Rationale: the user's own assessment, on reflection, is that Lambda felt
like the harder path — consistent with the missing-trigger-step finding
above. Removing AWS entirely also removes IAM/trigger-linking as a category
of failure; the Alexa-console-side work becomes just Alexa's own endpoint
screen (paste URL, pick the trusted-CA option). What's left — implementing
and packaging Amazon's request-signature verification on 32-bit i686 — is a
different *kind* of difficulty: a debuggable coding/packaging problem
instead of an opaque console permission that can be silently wrong. It's
already flagged as the top risk to de-risk in Phase 0
(`../architecture.md` §4.2, §11), so it isn't being underestimated.

## Consequences

### Positive
- No AWS account, no IAM, no trigger-linking step — removes the specific
  category of friction the prototype's README suggests it likely hit.
- Matches `../architecture.md`'s "netbook is the home server" framing (§3).
- The failure mode, if any, will be a code exception or packaging error
  with a stack trace — not a silent authorization rejection.

### Negative / trade-offs
- Keeps the plan's current "highest risk unknown" (§4.2): full signature
  verification (cert-chain fetch/cache/validate, SHA1withRSA check,
  timestamp replay check) must be implemented and must package cleanly on
  32-bit i686 / Python 3.6.
- Not confirmed to be the *actual* fix for the prior difficulty, since the
  prototype's real blocker was never directly recorded — this decision
  rests on the missing-trigger-step inference plus the user's own recall,
  not a confirmed root cause.

### Follow-ups
- Phase 0 (`../architecture.md` §11) must verify the `cryptography` package
  installs cleanly (or resolve the apt/Go fallback) **before** any other
  work — this is now the plan's single biggest remaining risk with no
  fallback-via-Lambda left to fall back to.

## Alternatives considered

- **(b) AWS Lambda ARN relay**, like the prior prototype — rejected. Avoids
  signature-verification code, but reintroduces the AWS console/IAM
  trigger-linking step that most plausibly caused the prior difficulty in
  the first place.
- **Alexa-Hosted skill (Amazon manages the Lambda for you)** — rejected in
  `../architecture.md` §4.1: forces Lambda hosting model and removes the
  "own the whole stack" property without removing any of the actual
  console-configuration steps.
