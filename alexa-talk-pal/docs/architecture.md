# alexa-talk-pal — Architecture & Build Plan

**Status:** Planning (nothing built yet)
**Component:** Fourth app in the netbook ecosystem (alongside `digital_frame.sh`, Syncthing, dupe-sweep)
**Running cost:** **$0/month** — every external service is on a free tier (see §1.1)
**Date:** 2026-09-20

---

## 1. Intro & Why This Shape

The original idea was "run a local LLM on the netbook so Alexa can talk to it." That is dead on arrival on this hardware, and it's worth writing down exactly why so it never gets re-litigated:

| Hardware fact (verified via SSH) | Consequence |
|---|---|
| Intel Atom N270 @ 1.6 GHz, 1 physical core (2 logical via SMT), circa 2008 | Roughly 1–2 GFLOPS of usable throughput. A 1B-param model at Q4 needs far more just to hit conversational token rates. |
| 32-bit i686 userspace | `llama.cpp`, `ollama`, `llamafile`, ONNX Runtime, PyTorch — none ship or meaningfully support 32-bit x86 builds. A 32-bit process also can't mmap more than ~3 GB of address space. |
| SSE/SSE2/SSSE3 only — **no AVX, no AVX2, no FMA** | Every modern GGUF/quantized kernel path assumes AVX2 at minimum. Scalar fallback (where it even exists) is 10–30× slower. |
| 2 GB RAM, ~1.5 GB actually available | Already shared with the Syncthing daemon and the `feh`-based slideshow. Even a 1B Q4 model (~700 MB weights + KV cache) would evict everything else and thrash. |

So: **the netbook does not run a model. It runs a ~30 MB HTTP/JSON relay.** All inference happens at OpenRouter. The Atom's job is to accept a JSON POST, verify a signature, make one outbound HTTPS call, and reshape the answer. That's a workload this machine handled fine in 2008.

The second hardware-driven decision: Alexa custom skills require a **publicly reachable HTTPS endpoint with a modern, valid certificate**. Xubuntu 18.04's system TLS stack is old, the netbook sits behind a home router, and port-forwarding a decade-old box to the open internet is not something we're doing. A **tunnel service** solves both: it terminates TLS at the provider's edge with a modern cert, and the netbook only makes an *outbound* connection — no inbound ports, no exposed home IP.

### 1.1 Cost posture: everything on a free tier

**Design constraint: this component runs at $0/month.** Every piece below has a free tier that covers a household's usage. Where a free tier imposes a real limit, it's stated rather than glossed over — the limits shape the design (especially §8).

| Piece | Free? | The catch |
|---|---|---|
| Amazon Developer account | **Free** | None. Registration is free. |
| Custom skill kept in **development mode** | **Free** | Never published → no certification, no fees. Works on Echo devices signed into the same Amazon account. |
| Alexa Voice Service (ASR + TTS) | **Free** | None — it's part of owning the Echo. |
| Echo device, netbook, electricity | **Sunk** | Already owned and already running 24/7 for Syncthing. |
| Public HTTPS endpoint — **ngrok free plan** | **Free** | 1 auto-assigned dev domain (`<name>.ngrok-free.app`), persists across restarts, **no session timeout**; 20,000 HTTP requests/month; 1 GB/month; 3 online endpoints. Hostname is auto-assigned and ugly — irrelevant, it's typed into the dev console once. |
| *(alternative)* Cloudflare Tunnel | Service free, **but** | Named tunnels require a domain whose nameservers are on Cloudflare ⇒ **~$10/yr domain registration**. The free `trycloudflare.com` quick tunnel gives a **random hostname that changes on every restart**, which is incompatible with Alexa's static endpoint setting. So Cloudflare = not $0 unless a domain is already owned. |
| LLM inference — **OpenRouter `:free` models** | **Free** | **20 requests/minute and 50 requests/day** while lifetime credit purchases are under $10. (A one-time $10 purchase raises it to 1,000/day, permanently — but that's $10, so it's not the default.) Free endpoints are also slower and may log/train on prompts. See §8 and §9.6. |
| Relay code, systemd, Python | **Free** | Open source; already on the box. |

**Net:** $0/month, with the binding constraint being **50 questions per day** from OpenRouter's free tier. ngrok's 20k requests/month (~660/day) is far looser, so it never binds first. If 50/day proves too tight in practice, §10 lists the escape hatches — the cheapest being a **one-time** $10 OpenRouter credit purchase that permanently unlocks 1,000/day.

### 1.2 Future option: local LLM inference backend

**Phase 4 option** (post-v1): The relay's inference backend can be made pluggable via configuration. The default stays OpenRouter, but an alternative backend — `llama.cpp` running on the Windows PC host (`192.168.4.55:11434`) — could be wired in via an env var or CLI flag (e.g. `INFERENCE_BACKEND=openrouter` vs. `INFERENCE_BACKEND=local_llama:http://192.168.4.55:11434`). Benefit: eliminates the 50/day free-tier cap and the free-endpoint training opt-in, at the cost of requiring the always-on PC to also run an inference service. Latency over LAN must be measured to confirm it fits the 8s Alexa deadline. **Not the default plan** — v1 launches with OpenRouter to keep complexity minimal and leverage existing fast-model tuning. If real usage shows 50/day is the blocker, this becomes the escape hatch that doesn't require any paid tier.

---

## 2. End-to-End Request Flow

```
  ┌──────────────┐
  │   Person     │  "Alexa, ask Talk Pal why the sky is blue"
  └──────┬───────┘
         │ voice
         ▼
  ┌──────────────┐
  │  Echo device │  wake word detection only; streams audio up
  └──────┬───────┘
         │ audio over WiFi/internet
         ▼
  ╔═══════════════════════════════════════════════════╗
  ║  AMAZON CLOUD                                     ║
  ║  ┌─────────────────────┐                          ║
  ║  │ Alexa Voice Service │  ASR: audio → text       ║
  ║  └──────────┬──────────┘                          ║
  ║             ▼                                     ║
  ║  ┌─────────────────────┐                          ║
  ║  │ Alexa Skills Kit    │  matches invocation name ║
  ║  │ (interaction model) │  "talk pal", fills the   ║
  ║  │                     │  {query} slot, builds    ║
  ║  └──────────┬──────────┘  IntentRequest JSON      ║
  ╚═════════════╪═════════════════════════════════════╝
                │ HTTPS POST (signed) — must answer in ~8s
                ▼
  ╔═══════════════════════════════════════════════════╗
  ║  TUNNEL PROVIDER EDGE  (ngrok free tier)          ║
  ║  https://<assigned>.ngrok-free.app/alexa          ║
  ║  - modern TLS termination (provider's cert)       ║
  ║  - routes into the pre-established tunnel         ║
  ║  [alt: Cloudflare edge, if a domain is owned]     ║
  ╚═════════════╪═════════════════════════════════════╝
                │ tunnel (outbound-initiated from netbook)
                ▼
  ╔═══════════════════════════════════════════════════╗
  ║  NETBOOK (Atom N270, 2GB, i686)                   ║
  ║  ┌───────────────┐                                ║
  ║  │ ngrok agent   │  systemd service, ~25MB RSS    ║
  ║  │ (or cloudflared)                               ║
  ║  └───────┬───────┘                                ║
  ║          │ plain HTTP → 127.0.0.1:5040            ║
  ║          ▼                                        ║
  ║  ┌──────────────────────────────────────┐         ║
  ║  │  talkpal-relay (Flask, systemd)      │         ║
  ║  │  1. verify Alexa signature + cert    │         ║
  ║  │  2. check applicationId              │         ║
  ║  │  3. extract {query} slot text        │         ║
  ║  │  4. POST to OpenRouter               │         ║
  ║  │  5. wrap reply as outputSpeech JSON  │         ║
  ║  └───────┬──────────────────────────────┘         ║
  ╚══════════╪════════════════════════════════════════╝
             │ outbound HTTPS (Python requests + certifi)
             ▼
  ┌────────────────────────────────────────┐
  │  OpenRouter  /api/v1/chat/completions  │
  │  → routes to chosen small/fast model   │
  └────────────────┬───────────────────────┘
                   │ JSON completion
                   ▼
        (relay reshapes → Alexa response JSON)
                   │
                   ▼  back up the same chain
        ASK → Alexa TTS → Echo speaker → person hears the answer
```

**Narrated version of the hops:**

1. **Person → Echo.** The Echo does wake-word detection locally, then opens a stream to Amazon. Nothing about our system is involved yet.
2. **Echo → Alexa Voice Service.** AVS does speech recognition, producing text like *"ask talk pal why the sky is blue."*
3. **AVS → Alexa Skills Kit.** ASK matches the **invocation name** ("talk pal") to our skill, runs the utterance against our **interaction model**, matches `AskAnythingIntent`, and captures the rest of the sentence into a free-form slot. ASK then builds a JSON `IntentRequest` envelope and signs the HTTP request.
4. **ASK → tunnel provider edge.** Amazon POSTs to `https://<assigned>.ngrok-free.app/alexa`. The provider presents a valid modern cert (Amazon requires this and will refuse a self-signed or weak-TLS endpoint). **This is the hop that the netbook's ancient OpenSSL could not have served itself.**
5. **Edge → netbook.** Traffic goes down the persistent tunnel the agent opened *outbound* from the netbook at boot. No inbound firewall rule, no port forward, home IP never disclosed.
6. **Tunnel agent → relay.** Delivered as plain HTTP to `127.0.0.1:5040`. Plaintext is fine here — it never leaves the loopback interface.
7. **Relay work.** Verify Amazon's signature (see §4), confirm the `applicationId` is ours, pull the slot text, build a chat-completions payload, POST it to OpenRouter with the API key from the environment.
8. **OpenRouter → model → back.** OpenRouter routes to whichever provider is serving the chosen model and returns a standard OpenAI-shaped completion.
9. **Relay → ASK.** The relay maps `choices[0].message.content` into `response.outputSpeech.text` and returns `200 OK`. **The entire round trip from step 4 to here must complete in under ~8 seconds** or Amazon gives up and Alexa says something unhelpful.
10. **ASK → TTS → Echo.** Amazon synthesizes the text and the Echo speaks it. If `shouldEndSession` is `false`, the mic reopens for a follow-up.

---

## 3. Components: What Runs Where

| Component | Location | Why it's needed |
|---|---|---|
| Echo device | Living room | Mic + speaker + wake word. Nothing custom here. |
| Alexa Voice Service | Amazon cloud | Speech-to-text and text-to-speech. We get world-class ASR/TTS for free; there is no way to run this locally on an Atom either. |
| Alexa Skills Kit + interaction model | Amazon Developer Console | Maps the spoken invocation to our skill and to `AskAnythingIntent`; defines the JSON contract and the ~8s deadline. |
| Tunnel provider edge (ngrok free) | ngrok cloud | Public hostname and **modern TLS termination**. Required because Amazon mandates valid HTTPS and 18.04's stack is too old to trust for serving it. Free plan gives one persistent dev domain. |
| ngrok agent (or `cloudflared`) | **Netbook**, systemd service | Outbound-only tunnel. Avoids port-forwarding, avoids exposing the home IP, and (being a Go binary with its own embedded TLS + CA pool) sidesteps the OS's aging OpenSSL entirely. |
| `talkpal-relay` (Flask) | **Netbook**, systemd service | The only custom code we write. Signature verification, slot extraction, OpenRouter call, response shaping. Holds the API key. |
| OpenRouter API | Cloud | Actual LLM inference on `:free` models, plus one account/one key across many models — so swapping models is a config change, not a rewrite. |
| Model weights | **Nowhere on the netbook** | Deliberately. See §1. |

Deliberately **not** used: AWS Lambda / Alexa-Hosted skills (genuinely free and would remove the tunnel entirely — but the whole point of this project is that the *netbook* is the home server, and the OpenRouter key would end up in AWS instead), Cloudflare Tunnel as the default (free service, but the required domain isn't — see §6), port-forwarding (rejected), and any paid plan of anything.

---

## 4. Alexa Skill Setup

### 4.1 In the Amazon Developer Console

Create a **Custom skill** with **"Provision your own"** hosting (not Alexa-Hosted — that forces Lambda).

- **Invocation name:** two lowercase words, e.g. `talk pal`. Must be distinct enough that ASR doesn't collide with built-ins. (See open questions — this needs real-world testing; names like "pal" alone tend to mis-trigger.)
- **Interaction model** — one catch-all intent plus the required built-ins:

```
AskAnythingIntent
  slot: query   type: AMAZON.SearchQuery
  sample utterances:
    "{query}"
    "ask {query}"
    "tell me {query}"
    "about {query}"
AMAZON.HelpIntent
AMAZON.StopIntent
AMAZON.CancelIntent
AMAZON.FallbackIntent
```

**Known gotcha:** `AMAZON.SearchQuery` is the only practical free-form slot type, and Amazon **will not accept a sample utterance that is nothing but the slot** — it needs carrier words around it (`"ask {query}"` works, bare `"{query}"` is rejected in the model validator). Plan on a few iterations here; this is the single most fiddly part of the console setup. Two-turn flow ("Alexa, open Talk Pal" → "What would you like to ask?" → user speaks) sidesteps it entirely and is the safer default for Phase 2.

- **Endpoint:** type **HTTPS**, URL `https://<assigned>.ngrok-free.app/alexa`, and select the cert option **"My development endpoint has a certificate from a trusted certificate authority."** (True — it's the tunnel provider's wildcard cert.)
- **Distribution:** leave the skill **in development / unpublished**. See §9.

### 4.2 Request signature verification — this is real work

Amazon requires every self-hosted skill endpoint to validate incoming requests. This is **not** optional boilerplate; certification and, more importantly, basic safety depend on it. An unvalidated endpoint is an open LLM proxy that anyone who discovers the hostname can bill to your OpenRouter account.

Full manual validation means all of:

1. Read `SignatureCertChainUrl` header; verify the URL is `https`, host is `s3.amazonaws.com`, port 443, path starts with `/echo.api/`, and normalize it (protect against `../` traversal).
2. Download and **cache** that PEM chain (don't refetch per request — that alone could blow the latency budget on a slow Atom).
3. Verify the cert chain validates to a trusted root, is currently within its validity window, and has `echo-api.amazon.com` in its SAN list.
4. Base64-decode the `Signature` header and verify it (SHA1withRSA) against the **raw request body bytes** using the cert's public key. *Raw bytes* — re-serializing the parsed JSON will fail.
5. Reject requests whose `request.timestamp` is more than 150 seconds old (replay protection).
6. Check `session.application.applicationId` matches our skill ID.

**Strong recommendation: don't hand-roll this.** Use Amazon's `ask-sdk-webservice-support` / `flask-ask-sdk`, which implements the whole verifier set. Caveat to budget for: those packages pull in `cryptography` / `pyOpenSSL`, and on 32-bit i686 + Python 3.6 there may be **no prebuilt wheel**, meaning a source build on an Atom N270 (slow — think tens of minutes, and needs `build-essential`, `libssl-dev`, `libffi-dev`, `python3-dev`). Mitigation if that build fights back: install `python3-cryptography` from the Ubuntu archive (`apt`) rather than pip, and let pip see it as already satisfied. **Verify this install path in Phase 1 before building anything else** — it's the highest-risk unknown in the plan.

---

## 5. Relay Service Design

### 5.1 Language & framework

**Python 3 + Flask**, running under a small WSGI server.

- Xubuntu 18.04 ships **Python 3.6** as the system `python3`. This matters:
  - **FastAPI is out.** Current FastAPI/Starlette/Pydantic v2 require Python 3.8+. Pydantic v2 also ships compiled Rust wheels with no i686 build. Don't fight this.
  - Flask 1.x/2.0.x works fine on 3.6. Pin versions explicitly in `requirements.txt` rather than taking latest.
  - No `async`/`await` ergonomics needed anyway — this is one request at a time on one core.
- Alternative if Python packaging on i686 turns painful: a single static **Go** binary (Go cross-compiles to `GOOS=linux GOARCH=386` trivially and has its own TLS stack, dodging both the old OpenSSL and the wheel problem). Cost: the Alexa signature verification would have to be hand-written (or use a third-party Go ASK library). Keep this as the fallback plan, not the default.
- Serve with **`waitress`** (pure Python, no compilation, works on 3.6) rather than gunicorn+gevent. One or two worker threads is plenty; concurrency here is "one person talking to one Echo."

### 5.2 Per-request logic

```
POST /alexa
  ├─ capture raw body bytes BEFORE parsing
  ├─ verify_signature(raw_body, headers)        → 400 on failure, log it
  ├─ verify applicationId matches ours          → 400 on failure
  ├─ dispatch on request.type:
  │    LaunchRequest        → "Hi, what would you like to ask?" (keep session open)
  │    SessionEndedRequest  → 200, empty body
  │    IntentRequest:
  │       AMAZON.Stop/Cancel → "Goodbye." (end session)
  │       AMAZON.Help        → static help text
  │       AskAnythingIntent  → main path below
  └─ main path:
       query = slots.query.value          (may be absent → reprompt)
       if daily_counter >= DAILY_CAP:     ← free-tier guard, see §8.1
            return alexa_response("I've used up my questions for today.")
       payload = {
         model: OPENROUTER_MODEL,
         max_tokens: ~150,                 ← latency AND TTS-length control
         messages: [
           {role: system, content: VOICE_SYSTEM_PROMPT},
           {role: user,   content: query}
         ]
       }
       resp = POST https://openrouter.ai/api/v1/chat/completions
              headers: Authorization: Bearer $OPENROUTER_API_KEY
              timeout=(connect 2s, read 5s)   ← hard ceiling, see 5.3
       speech = resp.choices[0].message.content
       return alexa_response(speech, end_session=False)
```

The **system prompt matters more than usual** because the output is spoken, not read: instruct the model to answer in 2–4 short sentences of plain spoken prose, no markdown, no bullet lists, no code, no emoji, no URLs. A model that emits a markdown table will have Alexa read punctuation aloud. Also cap `max_tokens` (~150) — it bounds both the latency and how long Alexa drones on.

### 5.3 The ~8 second deadline

Amazon gives a skill endpoint roughly **8 seconds** to return the initial response. Blowing it produces "there was a problem with the requested skill's response." This is the hardest constraint in the whole design and it dictates model choice.

Budget:

| Segment | Estimate |
|---|---|
| Amazon → tunnel provider edge → tunnel → netbook | 100–300 ms |
| Signature verification (cached cert chain, RSA verify on an Atom) | 50–200 ms — **measure this**; uncached cert-chain fetch would add 500ms+, hence the cache |
| Netbook → OpenRouter TCP + TLS handshake | 100–400 ms (keep a `requests.Session` alive to amortize) |
| OpenRouter routing + model time-to-first-token + full generation of ~150 tokens | **1.5–4 s** for a small fast model; 6–15 s for a large reasoning model. **Add variance on `:free` endpoints** — they're best-effort capacity and can queue (§8.1) |
| Response back through the tunnel to Amazon | 100–300 ms |
| **Total** | **~2–5 s typical, with real tail risk — and the free tier widens the tail** |

Defensive measures:

- **Hard client-side timeout below Amazon's** — 5s read timeout, so we always control the failure instead of timing out silently.
- **On timeout or any exception, return a valid Alexa response anyway** — something like *"Sorry, I couldn't reach my brain just now. Try again?"* Never return a 500; a graceful spoken failure is far better UX than Alexa's generic error.
- **Never enable reasoning/thinking modes** on the chosen model — they trade seconds for quality we don't need in a voice reply.
- **Pre-warm at boot**: on service start, make one throwaway OpenRouter call so DNS, TLS session, and the cert cache are hot before the first real query. (Budget it: on the free tier that call costs one of the day's 50.)
- **429 handling**: on a rate-limit response, fall back **once** to the secondary model and otherwise speak a friendly "too many questions right now" line. Never retry-loop — failed requests still consume the daily free quota (§8.1).
- Optional later refinement: a *progressive response* (ASK lets you send an interstitial "let me think about that" directive) to buy headroom. Not needed for v1.

### 5.4 systemd service

Consistent with how Syncthing is already run on this box (see repo README):

- Unit `talkpal-relay.service`, `After=network-online.target`, `Restart=always`, `RestartSec=5`, running as an unprivileged user.
- `EnvironmentFile=/etc/talkpal/talkpal.env` holding `OPENROUTER_API_KEY=...`, `OPENROUTER_MODEL=...` (a `:free` ID), `OPENROUTER_FALLBACK_MODEL=openrouter/free`, `DAILY_CAP=45`, `ALEXA_SKILL_ID=...`.
- That env file is `chmod 600`, owned by the service user, **lives outside the git repo**, and is covered by a `.gitignore` entry in this component's directory. The repo gets a `talkpal.env.example` with placeholder values only.
- Worth checking whether Syncthing's ignore list (per `syncthing/README.md`) excludes it too — `/home` is being synced to the Windows PC, so an API key in a home directory would be replicated. **Prefer `/etc/talkpal/` over `~/` precisely for this reason.**
- Logs to journald; add a light rate/usage counter so a runaway loop shows up before the OpenRouter bill does.

---

## 6. Public Ingress (Tunnel) Setup

The requirement is narrow: **one stable public HTTPS hostname, with a valid CA-issued cert, that forwards to `127.0.0.1:5040`, and that survives a reboot without changing.** "Stable" is non-negotiable because the endpoint URL is configured once in the Alexa console; a rotating hostname breaks the skill every restart.

### 6.1 Option A — ngrok free plan (**recommended default: $0**)

The free plan gives one **auto-assigned dev domain** (`<something>.ngrok-free.app`) that persists across restarts, with **no session timeout** — endpoints can stay online indefinitely. Quotas: 20,000 HTTP requests/month, 1 GB/month transfer, 3 online endpoints, 1 concurrent user. Far more than a household will use (and looser than OpenRouter's 50/day, which binds first).

1. Create a free ngrok account; copy the authtoken.
2. **Install the agent on the netbook.** ngrok publishes `linux/386` builds. **Verify a current 386 build exists in Phase 0** — this is a real risk on a 32-bit box and is the single thing that could force Option B or C.
3. `ngrok config add-authtoken <token>` → writes `~/.config/ngrok/ngrok.yml` (mode `600`, not in git).
4. Claim the free dev domain in the ngrok dashboard, then run the agent against it:

```yaml
# ~/.config/ngrok/ngrok.yml
version: 3
agent:
  authtoken: <token>
endpoints:
  - name: talkpal
    url: https://<assigned>.ngrok-free.app
    upstream:
      url: 5040
```

5. systemd unit `ngrok.service`, `Restart=always`, `After=network-online.target`, started at boot — same posture as the relay and as Syncthing.

**Two caveats to verify in Phase 2, not assume:**
- **The interstitial warning page.** ngrok's free tier injects a browser warning page for *HTML browser traffic*. Alexa's calls are `POST` with a JSON content type and a non-browser user agent, so they should pass straight through — but confirm it, because a silently-injected HTML page would break the skill in a confusing way. If it does trigger, the documented bypass is the `ngrok-skip-browser-warning` request header, which Amazon won't send — in that case fall back to Option B or C.
- **Quota exhaustion behavior** if something loops: 20k/month is the ceiling.

### 6.2 Option B — Cloudflare Tunnel (better, but ~$10/yr)

Strictly nicer (own hostname, better edge performance, WAF rules available, no interstitial), and the tunnel *service* is free — but named tunnels require a domain whose nameservers are on Cloudflare, so **a domain registration is the real cost**. The free `trycloudflare.com` quick tunnel is not usable here: hostnames are random and change on every restart.

Choose this if a domain is already owned, or if ngrok's 386 build or interstitial turns out to be a blocker.

1. Install `cloudflared` (Cloudflare does publish `linux-386` builds; static Go binary, so glibc age is irrelevant — still verify the current release).
2. `cloudflared tunnel login` → browser auth → cert into `~/.cloudflared/`.
3. `cloudflared tunnel create talkpal` → tunnel UUID + credentials JSON.
4. Config `/etc/cloudflared/config.yml`:

```yaml
tunnel: <tunnel-uuid>
credentials-file: /etc/cloudflared/<uuid>.json
ingress:
  - hostname: talkpal.<yourdomain>.com
    service: http://127.0.0.1:5040
  - service: http_status:404
```

5. `cloudflared tunnel route dns talkpal talkpal.<yourdomain>.com` → creates the CNAME automatically.
6. `cloudflared service install` → systemd unit, enabled at boot.
7. Optional free extras: a Cloudflare WAF / rate-limiting rule on that hostname. (Cloudflare Access is *not* usable in front of Amazon's POSTs without a service token — rely on §4.2 signature verification as the real gate.)

### 6.3 Option C — run the tunnel agent on the Windows PC

If no 32-bit tunnel agent can be made to work, run the agent on the always-on Windows PC and point its upstream at `http://<netbook-lan-ip>:5040` instead of localhost. Still $0, still no port-forwarding to the internet. Cost: the relay must then bind the LAN interface rather than loopback, so add a host firewall rule restricting `:5040` to the PC's IP. Keep this as the fallback, not the plan.

**In all options the relay binds `127.0.0.1` only** (except C), never `0.0.0.0`. The tunnel is the only way in, by construction.

---

## 7. Resource Footprint on This Specific Machine

Starting point: 2 GB total, ~1.5 GB available with Syncthing + `digital_frame.sh` already running.

| Process | RSS estimate | Notes |
|---|---|---|
| ngrok agent (or `cloudflared`) | 20–35 MB | Go runtime; idles near-zero CPU with a persistent tunnel. |
| `talkpal-relay` (Flask + waitress, 2 threads) | 35–60 MB | Higher end if `cryptography`/`pyOpenSSL` are loaded for signature verification. |
| Cached Amazon cert chain | negligible | A few KB in memory. |
| **New total** | **~55–95 MB** | ~4–6% of available RAM. |

CPU: idle between queries. During a request, the only non-trivial local work is one RSA signature verification and JSON handling — milliseconds-to-low-hundreds-of-ms on an N270. The dominant latency is network + remote inference, which the Atom simply waits on.

**Coexistence:** this is comfortably within budget and won't disturb the existing apps. Two things to watch:

- `digital_frame.sh` pre-scales images in a background queue; if a slideshow scaling burst coincides with an Alexa query, the single core is contended and signature verification could stretch. Consider `Nice=-5` (or a modest `CPUWeight`) on `talkpal-relay.service` so voice requests win — a delayed slideshow frame is invisible, a blown 8s deadline is audible.
- The relay must not log request/response bodies to a path inside a Syncthing-synced folder, both for volume and privacy.

Disk: trivial (~50 MB for the tunnel agent + Python deps) against 128 GB free.

---

## 8. Model Choice on OpenRouter (free tier)

### 8.1 The free tier and what it costs in constraints

OpenRouter exposes zero-cost variants of many models with a **`:free` suffix** in the model ID. Using only those, inference is genuinely $0. The limits, per OpenRouter's own docs:

| Lifetime credits purchased | Requests/minute | Requests/day (across all free models) |
|---|---|---|
| **Under $10 (i.e. never paid anything)** | 20 | **50** |
| $10 or more (one-time, permanent unlock) | 20 | 1,000 |

Three things follow directly:

- **50 questions/day is the real ceiling on this design.** For one household asking an Echo occasional questions, that's probably fine. It is *not* fine if someone discovers the skill is fun and machine-guns it, or if an Alexa routine loops.
- **Failed requests still count.** A 429 consumes quota, so a naive retry loop can burn a whole day's allowance in seconds. **The relay must not auto-retry more than once, if at all.**
- The relay should read `GET /api/v1/key` (field `free_model_daily_requests`) at startup and occasionally after, so remaining quota is visible in the logs rather than discovered by a confused Alexa.

Free endpoints are also **latency-variable** — they're best-effort capacity, subject to queuing and provider throttling. Against an 8-second deadline (§5.3) this is the main technical downside of going free, and it's why the graceful-fallback path matters more here than it would on a paid model.

### 8.2 Candidate free models

Selection criteria, in order: **(1)** round trip comfortably under ~5s for ~150 output tokens, **(2)** `:free`, **(3)** decent conversational quality, **(4)** **no reasoning tokens**.

The free catalog churns constantly — model IDs appear and vanish month to month. **Check the live list at <https://openrouter.ai/models?max_price=0> before wiring anything in**, and treat the names below as shapes, not gospel. As of Sept 2026 the free tier holds roughly 20+ models, including Google Gemma-class, NVIDIA Nemotron-class (including an explicitly fast "lightning" variant), Qwen-class, Z.ai GLM-class and Cohere-class entries.

| Pick | Why | Watch out for |
|---|---|---|
| A **small/fast free model** — e.g. an NVIDIA Nemotron "lightning"-class or Gemma-class `:free` entry | Explicitly optimized for speed/low latency; best odds of fitting the 8s budget. Strong default. | Quality dips on nuanced questions. Verify the exact ID is still live. |
| A **mid-size free general model** — e.g. a Qwen- or GLM-class `:free` entry | Noticeably better answers; fine if measured latency leaves headroom. | Bigger = slower; measure before committing. |
| **`openrouter/free`** (the Free Models Router) | Routes to whichever free provider currently has capacity → best resilience against a single free endpoint being saturated. Good *fallback* value. | Latency is unpredictable by design, and you don't control which model answers (so tone/quality vary). Don't make it the primary. |

**Explicitly avoid**, even though free: anything with "thinking"/"reasoning"/"inkling" in the name or a reasoning-capable flag (reasoning tokens will blow past 8s), anything huge (550B-class free entries exist — they're slow), and anything with `:online` (adds a web-search round trip).

Useful settings:
- `max_tokens: 150` — the primary latency governor.
- `provider: { sort: "throughput" }` to bias toward fast providers.
- A configured **fallback chain**: primary `:free` model, then `openrouter/free` if the first 429s or times out. **At most one fallback attempt** — see the quota-burn warning above.

### 8.3 Cost reality check

This is the honest trade-off versus the (impossible) local-inference dream. The good news: **on `:free` models it is actually $0**, which was the original appeal of local inference in the first place. What you pay instead is in constraints, not dollars:

- **50 queries/day** hard cap;
- **variable latency** against a fixed 8s deadline;
- **your prompts may be logged and trained on** by the free providers (§9.6).

If any of those bites, the escape hatches in ascending cost are: (a) tune `max_tokens` and the system prompt to reduce failures, (b) a **one-time $10** OpenRouter credit purchase — permanently raises free-model usage to 1,000/day *and* leaves $10 of credit that also buys paid small models at fractions of a cent per query, (c) check whether a provider's own direct free API tier (e.g. Google AI Studio) offers a higher daily allowance than OpenRouter's free routing — worth a look if 50/day is the only blocker, at the cost of losing OpenRouter's one-key-many-models convenience.

Regardless of tier: set a **spend limit of $0** on the OpenRouter key while running free-only, so an accidental switch to a paid model ID can't quietly start billing. And add a **relay-side daily counter** that refuses politely past ~45 requests — protection against a stuck Echo routine, and it keeps the failure *spoken and friendly* instead of a 429 surfacing as Alexa's generic error.

---

## 9. Security Considerations

1. **The OpenRouter API key never leaves the netbook.** It is not in the skill definition, not in the interaction model, not in Cloudflare config, not in this repo. It lives in `/etc/talkpal/talkpal.env`, mode `600`. Deliberately placed outside `/home` so the existing `/home` → Windows PC Syncthing replication doesn't copy it to another machine. Add `*.env` to this component's `.gitignore` and commit only `talkpal.env.example`.
2. **Alexa signature verification is the front door lock.** Without it, anyone who learns the hostname has a free, unauthenticated LLM proxy billed to the user. Combined with the `applicationId` check, only requests genuinely originating from our skill get through. This is the single most security-relevant piece of code in the component — treat a validation failure as a hard reject, and log it.
3. **The tunnel hides the home network.** The public hostname resolves to the tunnel provider, not to the home IP. No router ports are opened, and the aging 18.04 network stack is never directly exposed to internet scan traffic. The tunnel is outbound-initiated and authenticated by its authtoken/credentials file (also `600`, also not in git — note the ngrok authtoken is itself a secret that grants use of the account's endpoints).
4. **Relay binds loopback only.** Even on the LAN, nothing can reach `:5040` except via the tunnel.
5. **Old OS caveat, handled where it matters.** The netbook's *inbound* TLS problem is eliminated by design (Cloudflare terminates it). For *outbound* calls to OpenRouter, 18.04's OpenSSL 1.1.1 does support TLS 1.2/1.3, but the system CA bundle is stale — use `requests` with an up-to-date `certifi` from pip and pin it, so cert validation against OpenRouter doesn't quietly rot. Never disable verification to "make it work."
6. **Privacy — and the specific price of the free tier.** Everything asked through this skill is sent to Amazon *and* to OpenRouter and its downstream provider. On top of that: **many OpenRouter free endpoints are free precisely because the provider may log and train on the prompts.** OpenRouter gates this behind account settings ("enable free endpoints that may train on inputs" / "that may publish prompts") — turning them **off** shrinks the usable free model list, turning them **on** is the implicit cost of $0 inference. Decide this consciously, tell the household, and keep genuinely private questions off the Echo. Separately: don't log full utterances to disk by default; if logging is needed for debugging, keep it to a short-lived local file **outside any Syncthing-synced folder**, and turn it off afterwards.
7. **Free tiers fail differently.** With no paid plan there is no support channel and no SLA — if ngrok changes its free terms or a free model is withdrawn, the skill simply stops working one day. Not a security issue, but plan for it: keep the model ID and endpoint URL as one-line config, so recovery is an edit and a restart.
8. **Keep the skill unpublished.** A development-mode skill is only reachable from Echo devices on the developer's own Amazon account — that's an access control, not just a convenience.

---

## 10. Open Questions / Decisions Still Needed

1. **Invocation name.** "talk pal"? Something more distinctive? Needs ASR testing against household accents — two-syllable pairs with common words tend to mis-trigger or get swallowed. Also decide the persona/name Alexa uses when answering.
2. **Default free model.** Pick one from §8.2 and put it in the env file. Make it a one-line config change, and measure real end-to-end latency for two or three `:free` candidates in Phase 1 before deciding. Also decide the fallback (probably `openrouter/free`).
3. **Conversation memory.** Start **stateless** — each query is independent. It's simplest, cheapest, and fastest. If follow-ups ("and why is that?") turn out to matter, the natural next step is keeping the last N turns in the Alexa `session.attributes` (which ASK round-trips for you, so the relay stays stateless and no storage is needed on the netbook). Decide only after using v1 for a week.
4. **Publish or not.** **Recommendation: stay in development mode.** It works indefinitely on Echo devices signed into the same Amazon account as the developer, requires no certification, no privacy policy URL, no icons/store listing, and no review cycle. Publishing would mean Amazon's full certification gauntlet for zero benefit on a personal assistant. (Caveat to verify: development-mode skills can require periodic re-enabling — confirm this doesn't require attention every few months.)
5. **Ingress option.** ngrok free (§6.1, $0, auto-assigned ugly hostname) vs. Cloudflare Tunnel (§6.2, nicer but needs a ~$10/yr domain). Default is ngrok, purely on cost — revisit if a domain is already owned or if Phase 0 finds no 32-bit ngrok build.
6. **Is 50 questions/day enough?** This is the sharpest free-tier limit (§8.1). Live with it, or spend the one-time $10 for 1,000/day? Recommendation: start free, measure a week of real usage, decide with data.
7. **Free-endpoint training opt-in.** Free models generally require allowing providers to log/train on prompts (§9.6). Accept it (and keep private questions off the Echo), or restrict to free endpoints that don't require it — at the cost of a much shorter model list. Needs an explicit yes/no.
8. **System prompt / personality.** How chatty, how formal, what language? **Portuguese vs. English matters a lot** — it affects the Alexa skill's configured locale (`pt-BR` vs `en-US`), the invocation name, and the system prompt. Decide before creating the interaction model, since locale isn't trivially changed later.
9. **Fallback lines.** What exactly does Alexa say when the model times out, when the daily free quota is exhausted, and when the tunnel is down? Three distinct canned lines — write them up front.
10. **Does the tunnel belong on the netbook at all?** (§6.3) The Windows PC is also always-on. Running the agent there removes the 32-bit binary risk entirely, but splits the component across two machines. Default: netbook; revisit only if Phase 0 fails.

---

## 11. Suggested Build Order

### Phase 0 — De-risk the environment (do this first, it's where surprises live)
- [ ] SSH in; confirm `python3 --version` (expect 3.6) and whether `python3-venv` / `python3-pip` are installed.
- [ ] **Confirm a current `linux/386` build of the ngrok agent exists**; download and run `ngrok version` on the netbook. If not → try `cloudflared` 386 (needs a domain, §6.2) → else Option C on the Windows PC (§6.3).
- [ ] Try installing `flask`, `requests`, `waitress`, and the ASK verification packages in a venv. **If `cryptography` needs a source build, resolve that now** (apt-installed `python3-cryptography`, or fall back to the Go plan in §5.1).
- [ ] Create the free ngrok account, claim the dev domain, note the hostname.
- [ ] Create/confirm the OpenRouter account; decide the free-endpoint training toggle (§9.6); set the key's spend limit to **$0**; note the live `:free` model IDs from <https://openrouter.ai/models?max_price=0>.

### Phase 1 — Relay + tunnel, no Alexa yet
- [ ] Write the minimal Flask relay: a `/health` endpoint and a `/alexa` endpoint that accepts a *fake* Alexa-shaped JSON body (signature check bypassable via a debug env flag).
- [ ] Wire in the OpenRouter call; verify from the netbook with `curl` directly against `127.0.0.1:5040`.
- [ ] **Time it.** Run ~10 queries against each of 2–3 `:free` candidates and record total latency, including the slowest run — free endpoints are variable, so the *tail* matters more than the median against an 8s deadline. Pick the default from real numbers, not from the table in §8.2. (Mind the 50/day budget while testing: ~30 test queries is most of a day's quota.)
- [ ] Implement the quota guard: read `free_model_daily_requests`, cap at ~45/day locally, **no aggressive retries**.
- [ ] Install both systemd units; reboot the netbook and confirm the relay and tunnel come back automatically **with the same hostname**.
- [ ] `curl https://<assigned>.ngrok-free.app/health` from the Windows PC (i.e. from outside the tunnel) — proves the whole public path works before Amazon is involved. **Check the response is your JSON and not an interstitial HTML page** (§6.1).
- [ ] Check RAM impact with `free -m` and confirm the slideshow is undisturbed.

### Phase 2 — Alexa skill in the simulator
- [ ] Create the custom skill; set invocation name and locale.
- [ ] Build the interaction model (expect a few rounds with the `AMAZON.SearchQuery` sample-utterance validator).
- [ ] Point the HTTPS endpoint at the tunnel hostname; select the trusted-CA option.
- [ ] **Turn signature verification ON and remove the debug bypass.** Confirm a hand-crafted `curl` POST is now rejected.
- [ ] Test in the developer console's Alexa Simulator; watch journald on the netbook side to see requests arrive.
- [ ] Deliberately break things: kill the relay mid-test, set an absurdly low timeout, force the daily-quota guard to trip — confirm the user hears each graceful fallback line, not Alexa's generic error.

### Phase 3 — Real Echo device
- [ ] Confirm the Echo is signed into the same Amazon account as the developer account. The dev-mode skill appears automatically in Alexa app → Skills → Your Skills → Dev.
- [ ] Live test: invocation reliability, latency as actually experienced, whether answers *sound* right (this is where the voice-oriented system prompt gets tuned).
- [ ] Tune `max_tokens` and the system prompt based on how long the spoken answers feel.
- [ ] Let the household use it for a week; check the OpenRouter dashboard afterwards for **actual queries/day vs. the 50/day free cap** (spend should read $0.00). That number decides open question §10.6.

### Phase 4 — Polish (optional, only if v1 earns it)
- [ ] Session-attribute conversation memory (§10.3).
- [ ] Progressive response directive for latency headroom (helps most on slow free endpoints).
- [ ] Update `/README.md` to describe a **four**-app ecosystem, and add ADRs for the three decisive choices: *no local inference*, *tunnel over port-forwarding*, and *free-tier-only operation*.

---

## Appendix: Files This Component Will Eventually Own

```
alexa-talk-pal/
├── docs/
│   └── architecture.md          ← this document
├── relay/
│   ├── app.py                   (Flask relay — not yet written)
│   ├── requirements.txt         (pinned, Python 3.6-compatible)
│   └── talkpal.env.example      (placeholders only — real key never committed)
├── alexa/
│   └── interaction-model.json   (exported from the dev console, for reproducibility)
├── deploy/
│   ├── talkpal-relay.service
│   ├── ngrok.service
│   └── ngrok-config.example.yml (authtoken redacted)
├── .gitignore                   (*.env, *.json credentials, ngrok.yml)
└── README.md                    (usage + restore procedure, matching the other components)
```
