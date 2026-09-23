# Plan 0006: Phase 4a — Runtime Backend Switching (No Restart)

**Status:** Drafted (Opus-planned, awaiting implementation  
**Date:** 2026-09-23  
**Motivation:** Windows PC + local llama.cpp is not always on. Need to fall back to OpenRouter gracefully when PC is offline, and switch back when it comes online — all without restarting the relay.

---

## Overview

The relay currently reads backend config once at startup (module-level globals). To switch between local and OpenRouter on-the-fly, we introduce:

1. **Mode file** (`~/talkpal-relay/backend_mode`) — one-word config checked on every request
2. **CLI tool** (`talkpal-backend`) — switch with pre-flight health check
3. **Fast fallback** — if local fails mid-request, fall back to OpenRouter gracefully
4. **Status tracking** — `backend_status.json` shows today's counts and mode history

---

## How It Works

### Mode File

- **Path:** `~/talkpal-relay/backend_mode`
- **Content:** one word (`local` or `openrouter`)
- **Checked on every request** via modification-time cache (microseconds overhead)
- **Switch takes effect on next Alexa question**
- **Invalid values** are logged at WARNING and ignored
- **Deletion** reverts to `.env` default (`INFERENCE_BACKEND`)
- **Survives restarts and reboots**

### CLI Tool: `talkpal-backend`

Symlink to `~/bin/talkpal-backend` on the netbook.

```bash
talkpal-backend local              # Pre-flight check PC, then switch
talkpal-backend openrouter         # Switch immediately
talkpal-backend status             # Show mode, today's counts, PC reachability
talkpal-backend local --force      # Switch anyway (bypass PC check)
```

**`talkpal-backend local` flow:**
1. One-shot health check: `GET /v1/models` on PC (1s timeout)
2. Confirm `LOCAL_LLM_MODEL` is in the response
3. If OK, write mode file atomically and print `switched to local (PC answered in 84ms, model OK)`
4. If fails, refuse with reason and stay on OpenRouter (e.g., `PC not reachable: ConnectTimeout — staying on openrouter`)
5. `--force` overrides the refusal

**`talkpal-backend status` shows:**
- Current mode (file or `.env`?)
- Last change timestamp
- Today's count: questions answered on local vs. OpenRouter, fallbacks
- Live PC reachability check

### Safety Net: Fast Fallback

If you switch to `local` mode and the PC goes down, the relay doesn't break:

- **Fast failures** (ConnectionError, connect timeout <0.75s, HTTP 5xx, empty content):
  - Fall back to OpenRouter **once**, if 3s+ of request budget remain
  - Answer is still spoken, ~1s slower than usual
  - Logged: `LLM backend=openrouter fallback=yes(local ConnectionError) mode=local`
  
- **Slow answer** (read timeout >3s):
  - Don't fall back (protects 8s Alexa deadline)
  - Speak existing TIMEOUT line
  - Demote local for 5 minutes after 3 consecutive fallbacks (avoids repeated 0.75s timeouts)

- **Mode file unchanged** — your choice to use local still stands
- **Status file shows the problem:** `talkpal-backend status` prints "local mode, 5 fallbacks today" → signals "switch back"

### Remote Usage

From anywhere on the LAN:

```bash
ssh netbook@192.168.4.36 talkpal-backend local       # Switch to local
ssh netbook@192.168.4.36 talkpal-backend openrouter  # Switch to cloud
ssh netbook@192.168.4.36 talkpal-backend status      # Check state
```

### Optional: Windows Automation (Post-4a)

In Windows startup/shutdown tasks:

```powershell
# On startup
ssh netbook@192.168.4.36 talkpal-backend local

# On shutdown or sleep
ssh netbook@192.168.4.36 talkpal-backend openrouter
```

Gives close-to-automatic switching with zero background process on the netbook.

---

## Code Changes (Phase 4a)

### New Files

**`relay/backend_state.py`** (~150 lines):
- `read_mode_file(path, default_from_env)` — check modification time, re-read only when changed
- `BackendState` class:
  - `current_mode()` — return `local` or `openrouter`
  - `report_failure(backend, exc_kind)` — track consecutive failures, manage cooldown
  - `write_status_file()` — atomically write `backend_status.json`
  - No background threads, nothing runs on import

**`relay/talkpal_backend.py`** (~100 lines):
- CLI entry point with argparse
- `health_check_local(base_url, model, timeout)` — `GET /v1/models`, confirm model listed
- Subcommands: `local`, `openrouter`, `status`
- Exit code 0 on success, 1 on failure
- Uses stdlib + `requests` (already installed)

**`tests/test_backend_state.py`** (~200 lines):
- Missing file uses `.env` default
- Write `local` / `openrouter` switches without re-import
- Invalid values logged and ignored
- Deletion reverts to `.env`
- Status file format and atomicity

**`tests/test_backend_switching.py`** (~200 lines):
- Mode local: `ask_local_llm` called, `ask_openrouter` not
- Fast-fail fallback (ConnectionError → OpenRouter, history preserved)
- Slow timeout (ReadTimeout → TIMEOUT line, no fallback)
- Cooldown after 3 failures (local skipped for 5 minutes)
- Budget guard (nearly empty budget → no fallback)
- Forced `local` mode (no fallback, ADR 0013 behavior)

**`tests/fake_llama.py`** (optional, ~80 lines):
- Stdlib `http.server` for manual testing
- Serves `/v1/models` and `/v1/chat/completions`
- Switchable response: 200, 503, wrong model, configurable latency

### Changes to Existing Files

**`relay/app.py`:**
- Line 58–92: `INFERENCE_BACKEND` stays in `.env`, now just the startup default
- `ask_llm()` calls `backend_state.current_mode()` instead of reading globals
- Add deadline parameter (from request start time) passed to `ask_llm()`
- Add fallback logic (deadline-gated, fast-fail only)
- Add per-request log line: `LLM backend=local latency=1.12s fallback=no`
- `logging.basicConfig(level=logging.INFO)` in `__main__` (Flask 2.0 drops INFO by default)
- Mode changes logged at WARNING: `BACKEND_MODE from=openrouter to=local source=mode_file`

**`.env.example`:**
- Note `INFERENCE_BACKEND` is startup default only; mode file takes precedence at runtime
- Document `LOCAL_LLM_HEALTH_URL` (override default `/v1/models`)

**`.gitignore`:**
- Add `relay/backend_mode`
- Add `relay/backend_status.json`

**`docs/adr/0018-...md`** (new ADR):
- Document command-driven switching
- Fallback-on-fast-failure tweak to ADR 0013 (for local mode only)
- Why no HTTP admin route (all port 5040 traffic is public via ngrok)
- Privacy note: more queries stay on LAN with local backend

### Lines of Code

- New code: ~450 lines (backend_state + CLI + tests)
- Changes to app.py: ~50 lines
- Total: ~500 lines, fully testable without a Windows PC

---

## Testing Strategy

### Unit Tests (`test_backend_state.py`)
- Mode file missing → uses `.env` default
- Write `local` → next call sees `local`
- Write `openrouter` → next call sees `openrouter`
- Invalid value (typo, truncated file) → logged, previous mode kept
- Delete file → reverts to `.env`
- Status file atomicity (no half-written state)

### Flask Integration Tests (`test_backend_switching.py`)
- Mode `local`, `ask_local_llm` patched to return → goes through local only
- Mode `local`, `ask_local_llm` raises `requests.ConnectionError` → falls back to OpenRouter, history updated correctly
- Mode `local`, `ask_local_llm` raises `ReadTimeout` → speaks TIMEOUT line, OpenRouter not called
- Three consecutive fallbacks → local paused for 5 minutes
- Budget nearly spent → no fallback attempted
- Mode `openrouter` → exactly today's path (unaffected)

### CLI Tests (`test_talkpal_backend.py`)
- Fake llama server (stdlib `http.server`) on 127.0.0.1 ephemeral port
- `local` with 200 + model listed → switches, prints success
- `local` with 503 → refuses, prints `unavailable`
- `local` with model not listed → refuses, prints reason
- Closed port (refused) → refuses, prints `not reachable`
- `--force` → switches anyway
- `openrouter` → switches immediately
- `status` → prints current mode, counts, PC check result

### Manual Tests on Netbook (No Windows PC Needed)
- Point `LOCAL_LLM_BASE_URL` at a closed local port (connection refused)
- Run `talkpal-backend local --force` (bypass check)
- Ask Alexa a question → should fail fast, fall back to OpenRouter, and speak the answer
- Check journald: should see `fallback=yes(local ConnectionError)`
- Repeat with `LOCAL_LLM_BASE_URL` pointing to 192.168.4.254 (unused IP, simulates sleeping PC)
- Expect connect timeout, fallback, answer spoken

**Run all tests on the netbook itself** — it's Python 3.6 + Flask 2.0.3, not WSL's 3.12.

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Deadline blowout from fallback | Budget gating: no fallback if <3s remain; fast-fail only (no chains) |
| PC mode but goes down mid-request | Fast fallback catches it; fallback log visible in status; cooldown prevents hammering |
| Flapping during PC wake/sleep | 3-fallback cooldown; user sees problem and can explicitly switch back |
| Mode file corruption or race | Atomic write (temp + `os.replace`); modification-time cache, not parsing on every request |
| Thread safety under waitress | Mode file read is atomic under Python GIL; BackendState holds a lock for state updates |
| Slow answer vs. fast failure | Different timeouts: local read 3s (slow → TIMEOUT), connect 0.75s (fast → fallback) |
| Python 3.6 compat | No dataclasses, no f-strings in 3.7+ syntax; run tests on netbook before deploy |
| Import side effects break tests | Nothing runs on import; mode file read is lazy (first `/alexa` request) |
| Forgotten fallback exhaustion | Status file and `status` command show today's fallback count; operator can see the problem |

---

## Rollout

1. **Implement Phase 4a** (one PR, ~500 lines)
   - New files: `backend_state.py`, `talkpal_backend.py`, tests
   - Changes: `app.py`, `.env.example`, `.gitignore`
   - Tests pass on netbook (Python 3.6)

2. **Test on netbook before deploy**
   - Default mode stays `openrouter` in `.env`
   - No systemd or `.env` changes needed
   - `talkpal-backend local --force` followed by a test Alexa question

3. **Deploy**
   - Copy files to netbook
   - Symlink `relay/talkpal_backend.py` → `~/bin/talkpal-backend`
   - `.env` unchanged (stays `openrouter` by default)
   - No systemd restart needed

4. **Operate**
   - `ssh netbook talkpal-backend local` when PC is on
   - `ssh netbook talkpal-backend openrouter` when PC is off (or use fallback)
   - `ssh netbook talkpal-backend status` to see what's happening

---

## What's Deferred to Phase 4b or Later

- **Background health checker** (auto-switch on PC up/down, no command needed)
- **SIGHUP `.env` reload** (change `LOCAL_LLM_*` without restart)
- **Latency-based demotion** (if PC is busy, fall back on 3s slow answers)
- **HTTP admin route** (intentionally never — port 5040 is public)
- **Wake-on-LAN** (PC control from netbook)

---

## References

- ADR 0012: Local llama.cpp backend latency and truncation
- ADR 0013: Configurable local backend, OpenRouter stays default (replaced in part by ADR 0018)
- ADR 0018 (new): Command-driven runtime backend switching
- Architecture.md §1.2: Phase 4 option description
- Architecture.md §5.3: 8-second Alexa deadline

---

## Checklist (Ready to Implement)

- [ ] `relay/backend_state.py` — mode file reader, state holder
- [ ] `relay/talkpal_backend.py` — CLI tool
- [ ] `tests/test_backend_state.py` — unit tests
- [ ] `tests/test_backend_switching.py` — Flask integration tests
- [ ] `app.py` changes — deadline, fallback, logging
- [ ] `.env.example`, `.gitignore` updates
- [ ] ADR 0018 draft
- [ ] Run all tests on netbook (Python 3.6)
- [ ] Manual test on netbook (closed port fallback)
- [ ] Deploy and verify `talkpal-backend status`
