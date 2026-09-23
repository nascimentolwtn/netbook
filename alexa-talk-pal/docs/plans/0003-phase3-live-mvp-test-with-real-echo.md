# Plan 0003: Phase 3 live MVP test with a real Echo

- Status: proposed (planning only, nothing here has been run yet)
- Date: 2026-09-22
- Scope: `architecture.md` §11 Phase 3, first live run. This is the first time
  the full path (real Echo → Alexa cloud → **real signed request** → ngrok →
  `talkpal-relay` → OpenRouter → back) is used end to end.
- Executor: the human, at the Echo, with an SSH terminal open to the netbook.
  A later assistant session may help read logs, but it **must not read `.env`**
  (ADR 0008). Every step below that touches `.env` is marked **[HUMAN ONLY]**.

---

## 0. Hard dependencies (do not start until these are true)

This plan starts at: *"the skill points at the relay and has a validated
interaction model."* Two parallel planning efforts own the steps before that:

| Dependency | Owned by | What must be true before this plan runs |
|---|---|---|
| **Console reconfiguration** (Phase 2, napkin backlog item 2) | parallel plan | Endpoint type is **HTTPS**, URL is exactly `https://viscous-landlady-reappoint.ngrok-free.dev/alexa` (with the `/alexa` path). SSL certificate option is set (for ngrok's `*.ngrok-free.dev` cert this is normally the **wildcard sub-domain** option). Interaction model has `AskAnythingIntent` with a `query` slot of type `AMAZON.SearchQuery` (**the slot name must be exactly `query`**, because `_handle_ask_anything` reads `slots["query"]`), plus the Help/Stop/Cancel built-ins. The model has been saved and built with no errors. Test tab has **"Skill testing is enabled in: Development"**. |
| **Signature root-CA decision** (napkin backlog item 1) | parallel plan | The current mitigations are accepted for this test: URL pinning, validity dates, SAN, and chain-internal signatures, with no root-of-trust path building. This plan does not revisit that decision. |

**Write down before starting:**
- The **final invocation name**. ADR 0009 says `english talk pal`. Check that the
  Phase 2 edit did not change it.
- The **final `AskAnythingIntent` sample utterances**, meaning the carrier
  phrases such as `ask {query}` and `tell me {query}`. You need them in
  §2 to phrase questions the model will route correctly.
- The **Skill ID** shown in the console, `amzn1.ask.skill.…`. You compare it
  against the relay's config in §1.

---

## 1. Pre-flight checklist

Run these top to bottom. Every command runs from the Windows PC/WSL shell unless
noted. Stop at the first failure and fix it (see §4) before moving on.

### 1.1 Services are up

```bash
ssh netbook@192.168.4.36 'systemctl is-active talkpal-relay talkpal-tunnel; systemctl is-enabled talkpal-relay talkpal-tunnel'
```
Expect `active` `active` `enabled` `enabled`.

### 1.2 The relay answers locally, then publicly

```bash
ssh netbook@192.168.4.36 'curl -s -w " %{http_code}\n" http://127.0.0.1:5040/health'
curl -s -w " %{http_code}\n" https://viscous-landlady-reappoint.ngrok-free.dev/health
```
Both should print `{"status":"ok"} 200`. Local OK but public failing means a
tunnel problem (§4.4). Check that the public body is JSON and **not** an ngrok
HTML page.

### 1.3 Confirm the signature gate is really ON (`DEBUG_SKIP_SIGNATURE` unset)

`DEBUG_SKIP_SIGNATURE` **must be unset or false** for this test. With it on,
nothing real is being tested and the endpoint is an open LLM proxy.

**(a) [HUMAN ONLY] Static check.** This prints no secrets. Adjust the path if
the deployed `.env` lives elsewhere. `load_dotenv()` finds it by walking up
from `app.py`'s directory.
```bash
ssh netbook@192.168.4.36 'grep -iE "^DEBUG_SKIP_SIGNATURE" ~/talkpal-relay/.env || echo "not set (good)"'
ssh netbook@192.168.4.36 'systemctl show talkpal-relay -p Environment'   # make sure the unit does not set it either
```
Expect `not set (good)`, or a value of `0`, `false`, or empty. The `Environment=`
line should not mention it.

**(b) Runtime proof.** This is the check that counts, and it's safe for anyone
to run because it reads no files. Send an unsigned fake request through the
public URL:
```bash
curl -s -w " %{http_code}\n" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"version":"1.0","session":{"application":{"applicationId":"amzn1.ask.skill.fake"}},"request":{"type":"LaunchRequest","timestamp":"2026-01-01T00:00:00Z"}}' \
  https://viscous-landlady-reappoint.ngrok-free.dev/alexa
```
| Response | Meaning |
|---|---|
| `{"error":"signature verification failed"} 400` | **Correct.** The gate is on. |
| `{"error":"applicationId mismatch"} 400` | **Bypass is ON.** Signature checking was skipped and the request only failed the appId check. **Stop.** Unset it in `.env` **[HUMAN ONLY]**, then `sudo systemctl restart talkpal-relay` and re-run this check. |
| Spoken-text JSON with `LAUNCH_GREETING` | Should be impossible (bypass on *and* appId matched). Stop and investigate. |

The journal line `Signature verification failed: Missing SignatureCertChainUrl or Signature header`
should also appear for this request (§1.6 shows how to watch it).

### 1.4 [HUMAN ONLY] The rest of the `.env` is correct

Only variable **names** and **non-secret values** are printed here. The API key is never shown.
```bash
ssh netbook@192.168.4.36 'cd ~/talkpal-relay && grep -oE "^[A-Z_]+=" .env'   # names only
ssh netbook@192.168.4.36 'cd ~/talkpal-relay && grep -E "^(OPENROUTER_MODEL|OPENROUTER_FALLBACK_MODEL|INFERENCE_BACKEND|DAILY_CAP)=" .env'
ssh netbook@192.168.4.36 'cd ~/talkpal-relay && grep -cE "^OPENROUTER_API_KEY=sk-or-" .env; grep -cE "^ALEXA_SKILL_ID=amzn1\.ask\.skill\." .env'
```
Expect:
- `OPENROUTER_MODEL=liquid/lfm-2.5-2.6b:free` and `OPENROUTER_FALLBACK_MODEL=openrouter/free` (ADR 0011).
- `INFERENCE_BACKEND` either **absent or `openrouter`**. `local` depends on the
  Windows PC being on, and this test is for the default path (ADR 0013).
- The two counts print `1` and `1`. Then open the file in an editor on the
  netbook and check that the `ALEXA_SKILL_ID` value **exactly** matches the
  console's Skill ID. A mismatch is the most likely first-contact failure.
- If you changed anything, run `sudo systemctl restart talkpal-relay`. `.env`
  is read once at startup. Note that `talkpal-tunnel` has
  `Requires=talkpal-relay.service`, so a relay restart **also restarts the
  tunnel**. Wait about 10 s and re-run §1.2.

### 1.5 Clock, quota, and OpenRouter health

```bash
# Clock: signature verification rejects requests more than 150 s from the netbook's clock
ssh netbook@192.168.4.36 'timedatectl | grep -E "Universal time|synchronized"'
```
Expect `System clock synchronized: yes` and a UTC time within a few seconds of
real time. If it's off, run `sudo timedatectl set-ntp true` and wait. A skewed
clock makes **every** real request fail with `Request timestamp outside allowed window`.

```bash
# The relay's own quota guard: today's count vs. DAILY_CAP (default 45)
ssh netbook@192.168.4.36 'cat ~/talkpal-relay/daily_counter.json; echo'
```
(The path is `daily_counter.json` next to the deployed `app.py`.) Earlier curl
smoke tests from today are already counted. This plan uses about **8 to 15**
`AskAnythingIntent` calls. Only calls that reach the LLM step count; Launch,
Help, Stop, and empty-slot turns do not. If `count` is above about 30, expect to
hit the quota line mid-test. That isn't a bug, and the count resets at the
netbook's **local** midnight. The real OpenRouter ceiling is **1,000/day**
because the $10 credit is already applied (napkin guardrail 8), so the only
limit that can bind today is the relay's `DAILY_CAP=45`.

Optional **[HUMAN ONLY]** check that OpenRouter actually answers with the real
key. It prints only the answer and doesn't touch `daily_counter.json`:
```bash
ssh netbook@192.168.4.36 'systemctl cat talkpal-relay | grep ExecStart'   # find the venv python
ssh netbook@192.168.4.36 'cd ~/talkpal-relay && <venv>/bin/python -c "from app import ask_openrouter; print(ask_openrouter(\"Say hello in five words.\"))"'
```

### 1.6 Open the observation windows (leave them running for the whole test)

**Terminal A**: follow the live journal:
```bash
ssh netbook@192.168.4.36 'journalctl -u talkpal-relay -u talkpal-tunnel -f -o short-iso'
```
> **Important:** a *successful* request writes **nothing** to the relay
> journal. Flask's logger only emits WARNING and above, and there is no access
> log. A quiet journal means either success or the request never arrived. Use
> Terminal B to tell the two apart.

**Terminal B**: ngrok's request inspector, which shows each request's status
code and **duration**. The ngrok agent serves it on the netbook's
`127.0.0.1:4040` by default. Confirm this during pre-flight:
```bash
ssh -N -L 4040:127.0.0.1:4040 netbook@192.168.4.36
# then open http://localhost:4040 in a browser on the PC
```
Or without a browser:
```bash
ssh netbook@192.168.4.36 'curl -s "http://127.0.0.1:4040/api/requests/http?limit=5"' | python3 -m json.tool | grep -E '"(uri|status_code|duration)"|Signature'
```
The §1.3 fake request should show up there as a `POST /alexa` → 400. **Do not
use the inspector's "Replay" button** on real Alexa requests. Replays past
150 s fail the timestamp check, and replays inside that window consume quota.

**Also keep open:** the Alexa Developer Console → **Test** tab. Its "Device Log"
checkbox and JSON panes help, and the **Alexa phone app → More → Activity →
Voice History** shows what ASR actually heard.

### 1.7 Optional warm-up in the console simulator (recommended)

If the Phase 2 plan didn't already do it, type `open english talk pal` into the
Test tab simulator. The simulator sends **real signed requests**, so this
exercises signature verification without needing voice. It also warms the
relay's in-memory cert-chain cache: the first signed request after any relay
restart downloads the chain from `s3.amazonaws.com`, which adds latency. Expected
reply: `Hi, what would you like to ask?`. If this fails, debug here (§4) before
talking to the Echo.

**Pre-flight done** when 1.1 through 1.6 all pass (1.7 recommended).

---

## 2. Live test script (speak to the Echo)

Rules:
- Use the confirmed invocation name. This script assumes **"english talk pal"**.
- Phrase questions with a **carrier word that matches a real sample utterance**
  (e.g. "ask …", "tell me …"). A bare question may be routed to
  `AMAZON.FallbackIntent` instead of `AskAnythingIntent` (see ADR 0005).
- Wait for each reply to finish. After each answer the mic stays open briefly
  because `shouldEndSession=false`. The relay sends no reprompt, so if you say
  nothing the session just closes quietly. That is expected.
- After every step, glance at Terminal B for status and duration, and at
  Terminal A for any new line. Write down each duration.

| # | Say | Expected spoken reply (exact relay text) | Expected in logs | Counts toward quota? |
|---|---|---|---|---|
| T1 | "Alexa, open english talk pal" | "Hi, what would you like to ask?" (mic stays open) | Inspector: `POST /alexa 200`. Journal: nothing. | No |
| T2 | (in the same session) "ask why the sky is blue" | A 2–4 sentence plain-prose answer from the model | 200, duration about 1–4 s (ADR 0011 p90 was 1.55–3.20 s over curl). Journal: nothing. `daily_counter.json` count +1. | Yes |
| T3 | (same session, right away) "tell me how many moons Jupiter has" | Another short answer | 200. This is the "ask twice in a row" check, confirming the session stays open and a second LLM call works. | Yes |
| T4 | "help" | "You can ask me pretty much anything -- just ask a question and I'll do my best to answer." | 200 (`AMAZON.HelpIntent`) | No |
| T5 | "stop" | "Goodbye." and the session ends (blue ring off) | 200 for StopIntent, possibly followed by a `SessionEndedRequest` → 200 with an empty body | No |
| T6 | "Alexa, open english talk pal" → "cancel" | Greeting, then "Goodbye." | 200, 200 (`AMAZON.CancelIntent`) | No |
| T7 | **One-shot**: "Alexa, ask english talk pal what the capital of France is" | A direct answer with no greeting | 200 (single request). If ASK can't match the tail to a sample, Alexa may just open the skill ("Hi, what would you like to ask?") or route elsewhere. **Note which happens.** This is a finding, not a failure. | Yes, if it routes to AskAnything |
| T8 | **Edge, bare question**: open the skill, then say just "why is grass green" with no carrier word | Either an answer (ASK's matching was lenient) or **"Sorry, I couldn't reach my brain just now. Try again?"** | If you hear the brain line **and** the journal has **no** `LLM call failed` line **and** the counter did **not** go up, the utterance went to `FallbackIntent` or another unhandled intent (see §3). That's a known model/routing gap, not a relay failure. | Only if routed to AskAnything |
| T9 | **Edge, empty or very short**: open the skill, then "ask" (nothing after it), or a one-word mumble | "Sorry, I didn't catch a question. What would you like to ask?" (`NO_QUERY_TEXT`), or the brain line if it routed to Fallback | 200. `NO_QUERY_TEXT` doesn't count toward quota. | No |
| T10 | **Longer question**: "ask explain how a rainbow forms and why it is curved" | An answer that still sounds complete, not cut off mid-sentence (`max_tokens=150`) | Duration still well under 8 s. Note if the answer sounds truncated. | Yes |
| T11 | **Cold path re-check** (optional): `sudo systemctl restart talkpal-relay`, wait about 15 s, re-run §1.2, then repeat T1+T2 | Same as T1 and T2 | The first request after a restart re-downloads the cert chain, so compare its duration. With T1 (Launch) first, the cert fetch never stacks on top of an LLM call. | Yes (1) |

Quota budget for the full script: about 5 to 8 counted calls, plus retries.
Well inside `DAILY_CAP`.

**Optional fault drills.** These are only for confirming graceful failure is
actually *heard*. They aren't needed for MVP sign-off:
- **Quota line** **[HUMAN ONLY]**: temporarily set `DAILY_CAP` in `.env` to the
  current count, restart the relay, open the skill, and ask. You should hear
  "I've used up my questions for today. Ask me again tomorrow." and the session
  ends. **Revert `DAILY_CAP` and restart afterwards.**
- **Relay/tunnel down**: `sudo systemctl stop talkpal-relay`. Because of
  `Requires=`, this stops the tunnel too. Invoking the skill should produce
  Alexa's own generic error, not a relay line. Restart with
  `sudo systemctl start talkpal-relay talkpal-tunnel` and re-run §1.2.

---

## 3. What each outcome sounds like and what it means

### 3.1 Lines the relay itself speaks (a 200 reached Alexa)

If you hear one of these, **the whole chain worked**: signature, appId, tunnel,
and response format are all fine. Only the step behind the relay had a problem.

| You hear | Source constant | Triggered by | Counter +1? | Journal shows | Check next |
|---|---|---|---|---|---|
| "Hi, what would you like to ask?" | `LAUNCH_GREETING` | LaunchRequest | no | nothing | Success. |
| The model's answer | n/a | AskAnything → LLM OK | yes | nothing | Success. |
| "Sorry, I didn't catch a question. What would you like to ask?" | `NO_QUERY_TEXT` | AskAnythingIntent with an empty `query` slot | no | nothing | The slot didn't fill. Check Voice History to see what ASR heard. If it happens on normal questions, check that the slot is **named `query`** in the model. |
| "I'm getting a lot of questions right now. Try again in a minute?" | `RATE_LIMIT_FALLBACK` | Primary **and** `openrouter/free` fallback both returned 429 | yes | nothing (not logged) | §4.6 |
| "Sorry, that took me too long to think through. Mind trying again?" | `TIMEOUT_FALLBACK` (`fallback_messages.py`) | `requests` Timeout: 2 s connect or 5 s read on the LLM call (either model) | yes | nothing (not logged) | §4.7 |
| "I've used up my questions for today. Ask me again tomorrow." (session ends) | `QUOTA_EXHAUSTED_FALLBACK` | `daily_counter.json` count ≥ `DAILY_CAP` | no (already at cap) | nothing | §4.8 |
| "Sorry, I couldn't reach my brain just now. Try again?" | `GENERIC_ERROR_FALLBACK` | **Ambiguous. Five different causes, listed below** | depends | depends | §4.5 |
| "Goodbye." | `GOODBYE_TEXT` | Stop/Cancel | no | nothing | Success. |
| "You can ask me pretty much anything…" | `HELP_TEXT` | HelpIntent | no | nothing | Success. |

**The five causes of the generic "couldn't reach my brain" line**, and how to
tell them apart:
1. **LLM exception** (401 bad key, 5xx, a non-429 error on the fallback, DNS or
   connection error, unexpected JSON shape). The journal shows
   `LLM call failed (backend=openrouter): …` with a traceback, and the counter went up.
2. **LLM returned empty content.** Counter went up, **no** journal line.
3. **Unhandled intent** such as `AMAZON.FallbackIntent` or
   `AMAZON.NavigateHomeIntent`. The relay only handles Stop, Cancel, Help, and
   AskAnything, and everything else gets this line. **No** journal line, counter
   **unchanged**. The console's Device Log or JSON pane shows the intent name.
4. **Unknown request type**, for example a non-Launch/Intent/SessionEnded type.
   No journal line, counter unchanged.
5. **Relay bug** inside the `/alexa` try block. The journal shows
   `Unhandled error in /alexa: …` with a traceback.

### 3.2 Alexa's own error (no valid 200 from the relay)

If you hear **Alexa's voice saying something like *"There was a problem with the
requested skill's response"*** (the wording varies), the relay did **not** return
a usable 200 in time. **From the Echo alone, all of these sound identical**, so
the logs decide:

| Inspector (Terminal B) shows | Journal (Terminal A) shows | Meaning | Go to |
|---|---|---|---|
| `POST /alexa 400` | `Signature verification failed: <reason>` | Real Alexa request rejected by the signature gate | §4.2 |
| `POST /alexa 400` | `applicationId mismatch: got 'amzn1.ask.skill.…'` | `ALEXA_SKILL_ID` in `.env` is wrong or empty | §4.3 |
| `POST /alexa 500` | `Exception on /alexa [POST]` + traceback | Uncaught error, most likely in the signature path (§4.2 item 5) | §4.2 |
| `POST /alexa 200`, duration **> ~8 s** | nothing | Too slow: Amazon gave up before the 200 arrived | §4.7 |
| `POST /alexa 502` / ngrok error page | nothing from the relay | Tunnel is up, relay is down | §4.4 |
| **Nothing at all** | nothing | The request never reached the netbook: console, account, or tunnel side | §4.1 |

### 3.3 Alexa doesn't open the skill at all

It answers with a generic Alexa response, a web search, "I don't know that",
or opens a different skill. The problem is invocation or recognition, or skill
availability on the device. Go to §4.1 (step 1).

---

## 4. Troubleshooting runbook (most likely cause first)

Shared shortcuts:
```bash
NB='ssh netbook@192.168.4.36'
$NB 'systemctl status talkpal-relay talkpal-tunnel --no-pager'
$NB 'journalctl -u talkpal-relay --since "15 min ago" --no-pager | grep -E "Signature verification failed|applicationId mismatch|LLM call failed|Unhandled error|Exception on|DEBUG_SKIP_SIGNATURE"'
$NB 'journalctl -u talkpal-tunnel --since "15 min ago" --no-pager | tail -40'
$NB 'curl -s "http://127.0.0.1:4040/api/requests/http?limit=10"' | python3 -m json.tool | less
$NB 'cat ~/talkpal-relay/daily_counter.json'
```

### Layer isolation (use this first when unsure)

Work bottom-up. The first layer that fails is where the problem is.

1. **Relay process**: `$NB 'curl -s http://127.0.0.1:5040/health'` should return 200. If it fails, the problem is on the relay side.
2. **Tunnel/public**: `curl -s https://viscous-landlady-reappoint.ngrok-free.dev/health` from the PC should return 200. If it fails, the problem is the tunnel or network.
3. **Signature gate**: run the §1.3(b) fake POST. It should return 400 `signature verification failed`.
4. **Alexa → netbook**: does the Echo's request appear in the inspector? If not, the problem is on the Alexa console, account, or device side.
5. **Relay's handling**: inspector status plus journal line. See the §3.2 table.

### 4.1 Nothing arrives at the netbook (the inspector is empty)

1. **Recognition**: Alexa app → Voice History. Did ASR hear "english talk pal"?
   If not, speak more slowly or try "open english talk pal" without "Alexa, …"
   mid-conversation. A two-word-plus name like this can get mangled.
2. **Skill not enabled on the device or account**: Console Test tab must show
   "Development". The Echo must be signed into the **same Amazon account** as the
   developer console (ADR 0006). Alexa app → More → Skills → Your Skills → **Dev**
   should list the skill.
3. **Endpoint typo**: Console → Build → Endpoint must be exactly
   `https://viscous-landlady-reappoint.ngrok-free.dev/alexa`. Check the host,
   **`.dev`** not `.app`, and that the `/alexa` path is present. A missing path
   would hit `/`, which has no route, so the inspector *would* show a 404. That's
   a clue too.
4. **SSL certificate type** setting on the endpoint. If Amazon rejects the TLS
   handshake, nothing reaches ngrok. Try the wildcard sub-domain option.
5. **Model not rebuilt / endpoint not saved**: hit "Save Endpoints" and
   "Build Model" again, and wait for the build to succeed.
6. Run the simulator test (§1.7). If the simulator reaches the relay but the
   Echo doesn't, the device or account is the problem, not the relay.

### 4.2 `Signature verification failed: <reason>` on a real Alexa request

This is the first time real Amazon signatures hit this code. Match the exact reason string:

| Reason in journal | Most likely cause | Action |
|---|---|---|
| `Request timestamp outside allowed window (possible replay)` | Netbook clock drift | `$NB 'timedatectl'`, then `sudo timedatectl set-ntp true`, and fix the time. **Most likely cause on this old box.** |
| `Missing or invalid request timestamp` | Amazon's `request.timestamp` format differs from the parser's `%Y-%m-%dT%H:%M:%SZ` (e.g. fractional seconds) | Look at the raw body in the inspector. If the format differs, that needs a code fix. **Log it as a follow-up and stop the test**, since this plan doesn't change code. |
| `Missing SignatureCertChainUrl or Signature header` | Amazon sent a different header set, e.g. only `Signature-256` and no SHA-1 `Signature` | Check the request headers in the inspector. If only `Signature-256` is present, the relay needs SHA-256 support. That's a **code follow-up**, so stop the test. |
| `SignatureCertChainUrl host/port/path …` | Unexpected cert URL from Amazon | Inspector → header value. Should be `https://s3.amazonaws.com/echo.api/…`. |
| `Leaf certificate SAN missing echo-api.amazon.com` / `Certificate in chain is expired…` / `Certificate chain signature verification failed` | Amazon rotated its cert chain format, or a real problem | Capture the `SignatureCertChainUrl` value and hand it to the root-CA planning thread. Don't bypass. |
| `Request body signature verification failed` | Body bytes altered in transit (unlikely via ngrok), or a SHA-1 vs. SHA-256 mismatch | Check whether a `Signature-256` header exists, as above. |

**Also check these, which show up as a 500 with a traceback, not a 400:**
- **Uncaught exceptions in the verification path.** Only
  `SignatureVerificationError` is caught. A network error fetching the cert
  from S3 (`requests` exception, `raise_for_status` HTTPError), or a
  non-RSA (EC) cert in Amazon's chain (`verify()` called with RSA padding
  arguments raises `TypeError`), escapes as a Flask 500 logged as `Exception
  on /alexa [POST]`. The traceback names the function. Treat it as a code
  follow-up.

**Never "fix" a signature failure by turning on `DEBUG_SKIP_SIGNATURE`** for
the live test. That turns the test into a no-op and opens the endpoint.

### 4.3 `applicationId mismatch: got '…'`

The journal prints the ID Alexa actually sent. **[HUMAN ONLY]** compare it with
`ALEXA_SKILL_ID` in `.env` (and with the console). Fix `.env`, then run
`sudo systemctl restart talkpal-relay` (the tunnel restarts too) and re-run §1.2.
`got None` means the body had no `session`. That's unexpected for real requests,
so inspect it.

### 4.4 Tunnel or network side (public `/health` fails, 502s, or ngrok errors)

1. `$NB 'systemctl is-active talkpal-relay talkpal-tunnel'`. If the relay is down, the tunnel may still be up and return 502 / `ERR_NGROK_8012`.
2. `$NB 'journalctl -u talkpal-tunnel -n 50 --no-pager'`: look for auth errors, "endpoint already online" (another agent is using the domain), or reconnect loops.
3. `$NB 'ping -c2 8.8.8.8; getent hosts openrouter.ai'`: is the netbook's internet up?
4. Restart order: `sudo systemctl restart talkpal-relay`. `Requires=` pulls the tunnel along, but also run `sudo systemctl restart talkpal-tunnel` if it stays failed. Then run public `/health` again.
5. Check the ngrok dashboard for free-tier quota (20k requests/month): not a realistic limit today, but it's the one to check.

### 4.5 Generic "couldn't reach my brain" line

Work through the five causes in §3.1:
1. Journal has `LLM call failed (backend=openrouter)`. Read the exception:
   - `401`/`403`: bad or revoked `OPENROUTER_API_KEY` **[HUMAN ONLY]**. Check the key in the OpenRouter dashboard.
   - `402`: the key's $0 spend limit blocked it, meaning the model ID isn't actually a `:free` one. Recheck `OPENROUTER_MODEL`.
   - `404`/model not found: `liquid/lfm-2.5-2.6b:free` has been withdrawn (the catalog churns, architecture.md §8.2). Pick a new one with `measure_latency.py` as a follow-up.
   - `ConnectionError`/DNS: the netbook's outbound network (§4.4 step 3).
   - `backend=local`: `INFERENCE_BACKEND` is set to `local` by mistake (§1.4).
2. Counter went up but there's no journal line: the model returned empty text. Retry once. If it repeats, log it as a follow-up.
3. Counter unchanged and no journal line: an **unhandled intent** (usually `AMAZON.FallbackIntent` on a bare or odd utterance). Use carrier words. Log "relay treats FallbackIntent as generic error" as a follow-up. A kinder dedicated line for it would be nicer UX.
4. `Unhandled error in /alexa`: a relay bug. Save the traceback and stop.

### 4.6 Rate-limit line ("getting a lot of questions")

Both the primary and `openrouter/free` returned 429. This is provider capacity
(napkin guardrail 9), not our pacing and not the 1,000/day account cap. Wait
about 1 minute and retry **once**. Don't hammer it, because failed calls still
count against the relay's cap. If it persists across several minutes, record it
as evidence to revisit ADR 0011's model choice.

### 4.7 Timeout line, or Alexa's generic error with a 200 that took more than 8 s

- The relay's own ceiling is 2 s connect + 5 s read **per LLM call**. A primary
  429 that comes back slowly, followed by the fallback call, can in the worst
  case add up to more than 8 s. Amazon then gives up even though the relay
  eventually returns 200. The inspector duration shows it.
- The first signed request after a restart also pays for the S3 cert-chain
  download (same 2 s/5 s timeouts). This is why T1 (Launch) comes first.
- CPU contention: `$NB 'uptime; top -bn1 | head -15'`. Check whether a
  `digital_frame.sh` scaling burst is competing (architecture.md §7).
- One slow outlier is acceptable (free tier). **Repeated** timeouts are a
  follow-up: consider `Nice=` on the unit, progressive responses (Phase 4), or
  a model re-test.

### 4.8 Quota line earlier than expected

`$NB 'cat ~/talkpal-relay/daily_counter.json'`. If `count` ≥ `DAILY_CAP`, it's
working as designed. Earlier smoke tests and every AskAnything attempt,
**including failed ones**, count. Options: stop for today (it resets at local
midnight), or **[HUMAN ONLY]** temporarily raise `DAILY_CAP` in `.env` and
restart. Headroom on the real OpenRouter limit (1,000/day) is plentiful.

---

## 5. Safety and rollback

Pick the least disruptive switch that fixes the problem:

| Goal | Action | Undo |
|---|---|---|
| **Take the skill offline for the Echo, netbook untouched** (fastest, no SSH) | Developer Console → Test tab → set skill testing to **Off**. You can also disable the skill in the Alexa app (Your Skills → Dev). | Set it back to Development. |
| Cut public access, keep the relay running for local curl debugging | `$NB 'sudo systemctl stop talkpal-tunnel'` | `sudo systemctl start talkpal-tunnel` |
| Stop everything now | `$NB 'sudo systemctl stop talkpal-relay'`. Because of `Requires=`, this also stops `talkpal-tunnel`. | `sudo systemctl start talkpal-relay talkpal-tunnel`, then public `/health` |
| Keep it down across reboots | `$NB 'sudo systemctl disable --now talkpal-tunnel talkpal-relay'` | `sudo systemctl enable --now talkpal-relay talkpal-tunnel` |
| Suspected key abuse or unexpected billing | Revoke or rotate the key in the OpenRouter dashboard. The $0 spend limit already blocks paid usage. | New key into `.env` **[HUMAN ONLY]** + restart |

**When to pull the plug mid-test:**
- Any sign `DEBUG_SKIP_SIGNATURE` is on during the live run: the journal line
  `DEBUG_SKIP_SIGNATURE is set -- signature verification bypassed` appears.
- Requests in the inspector that did **not** come from your test. Check the
  timing, and whether the User-Agent is Amazon's `Apache-HttpClient`. The
  signature gate should 400 them, but stop and look anyway.
- `daily_counter.json` climbing without you speaking, which would mean a loop.

**Which side is at fault**, summarized:
- **Relay-side**: the inspector shows the request, and the relay answered
  400/500 or spoke a relay fallback line. The journal has the reason.
- **Tunnel/network-side**: local `/health` works but public `/health` fails,
  502/ngrok error pages appear, or `talkpal-tunnel` isn't active or keeps
  reconnecting in its journal.
- **Alexa console/account-side**: public `/health` and the §1.3 check both pass,
  but the Echo's request **never shows up** in the inspector, or ASR/Voice
  History shows the invocation wasn't recognized.

---

## 6. "MVP done" criteria

**Declare the MVP working when all of the following are observed in one
sitting, with the signature gate confirmed ON (§1.3b):**

1. **T1**: voice invocation opens the skill and the greeting is heard, **3 out
   of 3 attempts** (invocation reliability).
2. **T2 + T3**: at least **5 real questions** answered with relevant spoken
   answers across two or more sessions, including two in a row in one session.
   No Alexa generic errors. Every inspector duration is **under about 5 s**.
3. **T4, T5, T6**: Help, Stop, and Cancel each behave as specified.
4. **The journal shows zero** `Signature verification failed`,
   `applicationId mismatch`, `Exception on /alexa`, or `DEBUG_SKIP_SIGNATURE`
   lines for the real Echo traffic. The only signature rejection is the
   deliberate §1.3b probe.
5. `daily_counter.json` went up by exactly the number of AskAnything questions asked.
6. **Second-voice check**: one other household member successfully opens the
   skill and asks a question in their own voice and accent.

That is enough to hand it to the household. At that point, record the outcome
in `CHANGELOG.md` (a separate step, not part of this plan) with the measured
durations.

**Acceptable at MVP, tracked as follow-up work (not blockers):**
- T7 one-shot ("ask english talk pal …") not routing reliably. The two-turn flow is the supported path (ADR 0005).
- T8 bare questions landing on FallbackIntent and producing the misleading
  "couldn't reach my brain" line. Follow-up: a dedicated Fallback line and/or
  more sample utterances.
- An occasional single rate-limit or timeout line (free-tier variance).
- Answer quality or length tuning of the system prompt and `max_tokens`
  (architecture.md §11 Phase 3, remaining bullets).
- The week-long household usage review: real queries per day against
  `DAILY_CAP=45`, and whether the dev-mode skill needs periodic re-enabling
  (ADR 0006 follow-up).
- Observability gap: successful requests leave no trace in the journal. Consider
  a one-line INFO/WARNING access log with duration (no utterance text, per
  architecture.md §9.6).
- Root-CA path building (owned by the parallel plan).

**Not MVP. Stop and fix before household use:**
- Any real Echo request failing signature verification, however intermittently.
  This includes the timestamp-format, `Signature-256`, and EC-cert 500 cases
  in §4.2.
- Repeated (≥2 of 5) Alexa generic errors, or timeouts on ordinary short questions.
- The live test only passed with `DEBUG_SKIP_SIGNATURE` on.
