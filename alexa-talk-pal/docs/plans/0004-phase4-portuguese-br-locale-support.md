# Plan 0004: Add Portuguese-BR (pt-BR) Language Support

## Executive Summary

This plan extends the alexa-talk-pal ecosystem to support Portuguese (Brazil) alongside the existing English (US) support. The work is scoped as a Phase 4 candidate (post-v1) and follows the existing architectural patterns. The key insight is that Alexa Skills support multiple locales under a single Skill ID (per ADR 0009), so we create a second locale configuration within the existing skill rather than building a separate skill.

---

## 0. Prerequisites

This plan assumes a working, tested v1 exists. Two things must be true
before starting, not just "unblocked":

1. **Backlog item 1 (OpenRouter reasoning-leak bug) is fixed.** Closed
   2026-09-23 — `OPENROUTER_MAX_TOKENS` raised to 500, shared
   `_strip_leaked_reasoning` helper added, see
   [ADR 0015](../adr/0015-openrouter-max-tokens-500-plus-reasoning-strip.md)
   and the CHANGELOG's 2026-09-23 entry. Confirmed done — but re-verify no
   regression before building on top of it, since this plan's own token
   budget and latency assumptions depend on that fix holding.
2. **Phase 3 live Echo testing (Plan 0003) has actually run.** As of this
   writing, `docs/plans/0003-phase3-live-mvp-test-with-real-echo.md` is
   still **Status: proposed** — the CHANGELOG confirms Phase 2 + the
   reasoning-leak fix together only "close out everything blocking" that
   test, not that it has happened. Locale support is meaningless to build
   on a v1 that hasn't been confirmed working end-to-end against a real
   Echo device yet. Run Plan 0003 to completion first.

---

## 1. Current State Analysis

**Locale Routing Currently:**
- Alexa console manages one interaction model per locale (both en-US and pt-BR models live in the same skill, independently configured)
- Amazon's Alexa Services route incoming requests to the appropriate model based on the Echo device's configured language
- The `/alexa` endpoint receives all requests (both locales) at the same URL
- Request payload includes `request.locale` field indicating which locale was matched — Alexa sends this **hyphenated**, per BCP-47 (e.g., `"pt-BR"` or `"en-US"`), not underscore-separated. The relay's normalization step (§2.2) needs to expect the hyphenated form as the actual wire format; converting to an underscore internal key (or not) is an implementation choice, but the input format assumption must be correct

**Relay Behavior (Currently English-only):**
- Hardcoded `VOICE_SYSTEM_PROMPT` (English)
- Hardcoded fallback messages in `fallback_messages.py` (English)
- Hardcoded intent response strings (HELP_TEXT, GOODBYE_TEXT, etc. — all English)
- No locale detection or language selection logic

**LLM Backend:**
- OpenRouter (primary) and local llama.cpp (fallback) both accept text in any language
- System prompt language affects model behavior, but not a hard constraint
- Free-tier model performance should be similar for Portuguese, but latency testing is needed

**TTS:**
- Currently handled entirely by Alexa's native Text-to-Speech (no custom TTS)
- Alexa automatically selects Portuguese voices when the skill's locale is pt-BR
- No relay-side changes needed for voice selection

---

## 2. Touch Points Requiring pt-BR Awareness

### 2.1 Alexa Developer Console (Manual)
- Add pt-BR as a second locale to the existing Skill ID (English Talk Pal)
- Create a Portuguese-language interaction model with:
  - **Invocation name:** The skill's invocation name is already fixed at `"english talk pal"` (ADR 0009) for en-US, not the placeholder `"talk pal"` used in an earlier draft of this plan. Decide whether pt-BR reuses that same invocation name (allowed — Alexa supports a different invocation name per locale on the same skill, and reuse is simplest and lowest-risk) or gets a distinct Portuguese phrase (e.g., `"assistente conversa"` — untested for ASR collisions)
  - **Sample utterances:** translated carrier phrases for the `query` slot (e.g., `"fale sobre {query}"`, `"pergunte {query}"`, `"diga {query}"`)
  - **AskAnythingIntent** structure: identical to en-US, only sample utterances differ
  - **Built-in intents:** AMAZON.StopIntent, AMAZON.CancelIntent, AMAZON.HelpIntent (same for all locales)

### 2.2 Relay Application (`relay/app.py`)
- **Locale extraction:** read `request.locale` from incoming Alexa payload
- **Language-aware system prompt:** select English or Portuguese based on locale
- **Fallback messages:** inject language-aware strings based on locale
- **Response text:** HELP_TEXT, GOODBYE_TEXT, etc. must be language-aware
- **No new route needed:** the same `/alexa` endpoint handles both locales

### 2.3 Fallback Messages Module (`relay/fallback_messages.py`)
- Restructure to hold both English and Portuguese variants
- Provide a function to get language-specific messages given a locale string

### 2.4 Configuration (`.env.example` and `.env`)
- No new env vars required. Locale is always auto-detected from
  `request.locale`, with a fallback to en_US for anything unrecognized —
  that already covers the only real failure mode, so there's nothing left
  for a config toggle to do. Dropped from an earlier draft of this plan:
  `LOCALE_PREFERENCE` (force a single locale for testing) and
  `SUPPORTED_LOCALES` (an allow-list) — both are YAGNI, nothing in this
  plan's scope needs to force or validate locale beyond the existing
  fallback behavior

### 2.5 Tests (`relay/tests/`)
- Add test cases for locale extraction from requests
- Test fallback message selection for both locales
- Verify both en-US and pt-BR responses shape correctly
- Language-compliance test: assert a pt-BR request actually gets a
  Portuguese-language *response*, not just a request routed to the
  Portuguese system prompt (see §7 for why these are different checks)

### 2.6 Bilingual Sessions / Mid-Session Locale Switching
- Alexa doesn't lock a session to one locale server-side. More relevantly,
  once Plan 0005's multi-turn history exists: if `conversation_history`
  accumulates turns in one language and the user's device locale differs
  on a later turn in the same session (device-language change takes effect
  immediately; a stale session could still be open, or testing could swap
  between an en-US and pt-BR Echo mid-session), the relay has to decide
  what happens to that history.
- **Decision needed:** either (a) clear `conversation_history` whenever the
  current request's `request.locale` differs from the locale the session
  started with, or (b) keep history and accept the model may see a prior
  turn in a different language than the active system prompt. Recommend
  (a) — simplest, avoids mixed-language context confusing a small free-tier
  model, and cheap to implement (store the session's starting locale
  alongside history, compare on each turn).
- This is only reachable once Plan 0005's history exists; it's documented
  here because both plans touch `session.attributes` and this is the seam
  between them (see §11, "Integration with Plan 0005").

---

## 3. Minimal Scope vs. Nice-to-Have

### Minimal Scope (Required for Basic pt-BR Support)
1. Add Portuguese interaction model to Alexa Developer Console (manual, one-time)
2. Modify `relay/app.py` to:
   - Extract `request.locale` 
   - Route to language-specific system prompt
   - Route to language-specific fallback messages
3. Translate fallback messages in `fallback_messages.py`
4. Translate all hardcoded response strings (HELP_TEXT, GOODBYE_TEXT, LAUNCH_GREETING, NO_QUERY_TEXT)
5. Update `.env.example` to document the locale support
6. Unit tests for locale routing

### Nice-to-Have (Phase 4+ Refinements)
- TTS voice selection logic (if custom TTS is added later)
- Conversation memory in Portuguese (session attributes) — not language-specific
- Multi-language response generation (if LLM could respond in a different language than the request)
- Dynamic locale list from Alexa (tell the relay which locales are configured)
- Locale-specific system prompts stored in a config file rather than code

---

## 4. Integration Points & ADR Considerations

### ADR 0009 (Skill ID & Locale)
- **Impact:** Confirms one Skill ID can have multiple locales. No changes needed to the decision; we're extending within its scope.
- **Follow-up:** A new ADR or update to 0009 should document the multi-locale strategy for future reference.

### ADR 0006 (Development Mode)
- **Impact:** Development-mode skills are per-locale. When adding pt-BR, both the en-US and pt-BR versions remain in development mode (same skill, same account).
- **No change:** The skill stays unpublished; no certification needed.

### ADR 0008 (Environment File)
- **Impact:** Secrets (OPENROUTER_API_KEY, ALEXA_SKILL_ID) are shared across all locales; no locale-specific keys needed.
- **No change:** One `.env` file per netbook.

### ADR 0013 (Configurable Backend)
- **Impact:** The INFERENCE_BACKEND switch (OpenRouter vs. local llama.cpp) is locale-agnostic. The LLM receives the prompt in the request's language and responds accordingly.
- **No change:** Inference backend selection is independent of locale.

### ADR 0011 (Model Choice)
- **Impact:** Model latency may differ for Portuguese inputs, on top of an
  already-tight baseline: ADR 0012 measured OpenRouter free-tier latency at
  p90 6.08s for English, single-turn, live from the netbook on 2026-09-21 —
  already close to the 8s deadline before any Portuguese-specific effect is
  measured. Recommended: re-run `relay/measure_latency.py` with Portuguese
  test queries (§Phase 0.2) to confirm the chosen model still meets the 8s
  deadline with pt-BR requests, rather than assuming the earlier English
  numbers leave comfortable headroom.
- **Action item:** latency testing step in Phase 0.

### ADR 0014 (Signature Verification)
- **Impact:** Signature verification is locale-agnostic; `SignatureCertChainUrl` and signature algorithm are the same for all locales.
- **No change:** Existing signature verification logic applies to both en-US and pt-BR requests.

---

## 5. Effort & Risk Estimate

### Effort Breakdown

| Task | Effort | Notes |
|---|---|---|
| **Phase 0: Planning & Risk Mitigation** | 2–4 hours | Review Alexa console locale support, measure LLM latency with pt-BR queries, confirm interaction model structure for another language |
| **Phase 1: Relay Code Changes** | 4–6 hours | Locale extraction, system prompt routing, fallback message refactoring, unit tests |
| **Phase 2: Alexa Console Configuration** | 2–3 hours | Create pt-BR interaction model, configure invocation name, validate with simulator |
| **Phase 3: Integration Testing** | 3–4 hours | Test both en-US and pt-BR on real Echo, ensure graceful fallback for each locale, confirm voice selection |
| **Phase 4: Documentation & Polish** | 1–2 hours | Update README, CHANGELOG, ADRs, .env.example |
| **Total** | **12–19 hours** | Roughly 2–3 working days for one person |

### Key Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **LLM latency with pt-BR inputs** | Medium | Could breach 8s deadline if model is slower with Portuguese; Alexa timeout fails open with "couldn't reach my brain" | Re-measure with `measure_latency.py` using Portuguese queries; confirm model latency headroom in Phase 0 |
| **Interaction model validation rejects Portuguese samples** | Low | Alexa's validator may have rules specific to English; blocks console configuration | Use existing en-US samples as template; test with 2–3 invocation + carrier combinations; fallback: use two-turn flow if samples fail (note: this requires `Dialog.ElicitSlot`, which the relay does **not** implement yet — treat it as new work, not a free fallback) |
| **System prompt quality degrades in Portuguese** | Medium | Model may respond less coherently in Portuguese, or violate "short sentences, no markdown" instruction | Test system prompt with a few live queries in the simulator; iterate on wording if responses are verbose/contain markdown |
| **Invocation name collision in Portuguese** | Low | Portuguese carrier words (e.g., `"assistente"`) might collide with built-in intents or be hard for Alexa ASR | Test with real Echo ASR recognition; the existing en-US invocation name `"english talk pal"` (ADR 0009) is a safe default to reuse for pt-BR too |
| **Locale not in request payload** | Very Low | Alexa may send requests without `request.locale` in some edge cases | Always default to en_US if locale is absent; log a warning |

---

## 6. Step-by-Step Implementation Strategy

### Phase 0: De-risk & Validate (Day 1)

**0.1 Confirm Alexa locale support structure**
- [ ] Log into Alexa Developer Console, open the existing en-US skill
- [ ] Verify the "Add new language" / "Add locale" option exists
- [ ] Note the console's expected JSON structure for pt-BR (will differ in `languageModel.invocationName` and `languageModel.intents[].samples`)
- [ ] Confirm both locales share the same Skill ID and endpoint URL

**0.2 LLM latency testing with Portuguese queries**
- [ ] `measure_latency.py` needs a `--questions-file` flag added first
      (shared work with Plan 0005's measure_latency.py updates — do it
      once, not twice) — put the Portuguese queries in a plain text file,
      one per line, instead of hand-editing the hardcoded `QUERIES` list
- [ ] Run `relay/measure_latency.py --questions-file <path>` with 10–15 Portuguese test queries
- [ ] Example queries: `"Por que o céu é azul?"`, `"O que é a fotossíntese?"`, `"Quanto é 2 + 2?"`
- [ ] Record latency distribution, tail (p95/p99), any 429s or timeouts
- [ ] Compare against ADR 0012's already-measured English baseline (p90
      6.08s on OpenRouter free tier, single-turn) instead of assuming a
      fresh 5s target — that baseline is already tight against the 8s
      deadline before Portuguese-specific effects are even measured
- [ ] If latency is unacceptable, consider fallback model options

**0.3 Interaction model structure validation**
- [ ] Export the current en-US interaction model from the console (Build → Interaction Model → JSON Editor)
- [ ] Study the JSON structure, specifically:
  - `languageModel.invocationName` field
  - `languageModel.intents` array structure (identical across locales)
  - `languageModel.intents[n].samples` array (where translation happens)
- [ ] Draft Portuguese sample utterances based on the en-US model
- [ ] Create a shell pt-BR interaction model JSON for manual insertion in Phase 2

(Request-locale field validation moved to Phase 2 §2.5 — it needs a real
pt-BR interaction model in the simulator to test against, which doesn't
exist until Phase 2 creates it. An earlier draft of this plan placed it
here in Phase 0, before any pt-BR model existed to test.)

### Phase 1: Relay Code Refactoring (Day 2–3)

**1.1 Refactor fallback messages to be locale-aware**
- [ ] Modify `relay/fallback_messages.py`:
  - Define constants for both languages
  - Add a `get_messages(locale: str) -> dict` function that returns a dict of locale-specific messages
  - Keys: `GENERIC_ERROR`, `QUOTA_EXHAUSTED`, `TIMEOUT`, plus any others
  - Example:
    ```python
    MESSAGES = {
        "en_US": { "GENERIC_ERROR": "Sorry, I couldn't reach my brain just now. Try again?" },
        "pt_BR": { "GENERIC_ERROR": "Desculpe, não consigo alcançar meu cérebro agora. Tente novamente?" }
    }
    def get_messages(locale: str) -> dict:
        return MESSAGES.get(_normalize_locale(locale), MESSAGES["en_US"])
    ```

**1.2 Add locale extraction and routing to relay**
- [ ] Modify `relay/app.py`:
  - Add a `_extract_locale(parsed_body) -> str` function that reads `request.locale` from the payload — Alexa sends this hyphenated (`"pt-BR"`, `"en-US"`), so the function must handle that as the primary real-world input, not as one of two equally-likely formats
  - Normalize locale strings (handle both `"pt_BR"` and `"pt-BR"` as input, converting to whichever internal key format is chosen — normalizing both works fine as long as the hyphenated form is the one actually expected from Alexa)
  - Default to `"en_US"` if missing
  - Store locale in a request context (Flask's `g` object or function parameter)
  
**1.3 Refactor hardcoded strings to use locale**
- [ ] Identify all hardcoded response strings in `relay/app.py`:
  - `VOICE_SYSTEM_PROMPT`
  - `HELP_TEXT`
  - `GOODBYE_TEXT`
  - `LAUNCH_GREETING`
  - `NO_QUERY_TEXT`
  - `RATE_LIMIT_FALLBACK`
  - Imported messages from `fallback_messages`
- [ ] Create a `_get_response_text(key: str, locale: str) -> str` function
- [ ] Move hardcoded strings to a module-level dict with locale keys:
  ```python
  RESPONSE_TEXTS = {
      "en_US": {
          "HELP": "You can ask me pretty much anything...",
          "GOODBYE": "Goodbye.",
          "LAUNCH": "Hi, what would you like to ask?"
      },
      "pt_BR": {
          "HELP": "Você pode me perguntar praticamente qualquer coisa...",
          "GOODBYE": "Adeus.",
          "LAUNCH": "Olá, o que você gostaria de perguntar?"
      }
  }
  ```
- [ ] Update all `alexa_response()` calls to use `_get_response_text()` with the extracted locale

**1.4 Create language-aware system prompts**
- [ ] Define two system prompts (one English, one Portuguese):
  ```python
  SYSTEM_PROMPTS = {
      "en_US": "You are a helpful voice assistant...",
      "pt_BR": "Você é um assistente de voz útil..."
  }
  ```
- [ ] Update `ask_llm(query, locale: str)` to pass the locale-appropriate prompt
- [ ] Modify both `ask_openrouter` and `ask_local_llm` to accept locale parameter

**1.5 Add test coverage**
- [ ] In a new `relay/tests/test_locale.py` (not `test_signature.py`, which
      stays scoped to signature verification), add tests for:
  - `_extract_locale()` with valid pt-BR, en-US, missing locale
  - Locale normalization (pt-BR is the real wire format Alexa sends — normalize from that, not the other way around)
  - Fallback to en_US on invalid locale
  - `_get_response_text()` returns correct locale-specific strings
  - Full `/alexa` POST with both locales yields locale-appropriate responses
- [ ] Run tests locally and on the netbook venv

**1.6 Update configuration example**
- [ ] In `.env.example`, add a comment documenting locale support:
  ```
  # Locale support: the relay automatically detects the locale from incoming
  # Alexa requests (request.locale field). If absent, defaults to en_US.
  # Both en-US and pt-BR are configured in the Alexa Developer Console
  # (same Skill ID, separate interaction models). No env var configuration needed.
  ```

### Phase 2: Alexa Console Configuration (Day 3)

**2.1 Create the Portuguese interaction model**
- [ ] Log into the Alexa Developer Console, open the existing English Talk Pal skill
- [ ] Click the **language dropdown** (currently "English (US)") and select **"Add new language"** or **"Portuguese (Brazil)"**
  - Console creates a new, empty interaction model for the pt-BR locale
- [ ] The Skill ID, endpoint, and development-mode status are shared across locales
- [ ] Endpoint URL remains `https://<same-ngrok-hostname>/alexa` (no per-locale URL)

**2.2 Configure the pt-BR interaction model**
- [ ] **Invocation name:** The real en-US invocation name is
      `"english talk pal"` (ADR 0009) — confirm it in the console before
      configuring pt-BR, don't assume a placeholder name
  - Safest option: reuse `"english talk pal"` for pt-BR too (English
    words, still plausible for Portuguese ASR, zero risk of a second name
    colliding with something else)
  - Alternative: translate to `"assistente conversa"` or similar (test ASR collision risk before committing)
- [ ] **Intents:** Click **JSON Editor** and paste the pt-BR model structure (prepared in Phase 0)
  - **AskAnythingIntent** with `query` slot of type `AMAZON.SearchQuery` (identical structure to en-US)
  - **Sample utterances** in Portuguese — avoid leading with question
    words like "o que é" / "como" as carrier prefixes. The `query` slot
    already captures the user's full spoken question, which itself often
    starts with "o que é" or "como" — a template like `"o que é {query}"`
    either doubles up awkwardly ("o que é o que é a fotossíntese") or
    trains the interaction model to strip the real question word, similar
    to the bare-`{query}` sample bug found during Phase 2 console testing
    on en-US. Prefer neutral leads that don't presuppose the query's
    grammatical shape:
    - `"fale sobre {query}"`
    - `"me pergunte {query}"`
    - `"diga {query}"`
    - `"quero saber {query}"`
    - `"pergunta rápida {query}"`
    - Ensure each sample has a carrier word (no bare `{query}`)
  - **Built-in intents:** AMAZON.StopIntent, AMAZON.CancelIntent, AMAZON.HelpIntent (same for all locales)

**2.3 Verify model builds successfully**
- [ ] Click **Save Model**, then **Build Model**
- [ ] Wait for build completion (30s–2 min)
- [ ] Confirm "Build Successful" notification and green ticks in the checklist

**2.4 Test with the console simulator**
- [ ] Switch to the **Test tab**
- [ ] At the top, switch language to **Portuguese (Brazil)** (simulator may have a language dropdown)
- [ ] Send test utterances in Portuguese:
  - "abra english talk pal" → should trigger LaunchRequest → relay responds with Portuguese greeting
  - "por que o céu é azul" → should match AskAnythingIntent + query slot → relay sends to LLM in Portuguese, responds with Portuguese text
  - "ajuda" → should match AMAZON.HelpIntent → relay responds with Portuguese help text
  - "parar" → should match AMAZON.StopIntent → relay responds with Portuguese goodbye
- [ ] Verify relay logs (on the netbook, `journalctl -u talkpal-relay`) show successful requests with locale `pt-BR`
- [ ] Verify all responses are in Portuguese

**2.5 Request locale field validation** (moved here from Phase 0 — needs
the pt-BR model just built above to exist)
- [ ] In the console simulator, send a test request from both en-US and the
      new pt-BR locale
- [ ] Confirm the request JSON includes `request.locale` with the expected
      value — Alexa sends this hyphenated (`"en-US"` / `"pt-BR"`), not
      underscore-separated; confirm the relay's normalization handles the
      actual wire format, not an assumed one
- [ ] If the locale field is missing or named differently than expected,
      document the actual field name

### Phase 3: Live Echo Testing (Day 4)

**3.1 Echo device language configuration**
- [ ] On the Echo device or Alexa app, set the language to **Portuguese (Brazil)**
  - Or use a second Echo that's already in Portuguese
  - Or temporarily change a shared Echo's language for testing

**3.2 Live testing with real voice**
- [ ] "Alexa, english talk pal, por que o céu é azul?"
- [ ] Wait for response; confirm:
  - Alexa recognizes the invocation and Portuguese query
  - Relay receives the request with `locale: "pt-BR"`
  - Response is in Portuguese
  - Voice output uses a Portuguese voice (Alexa automatically selects one)
- [ ] Test failure modes:
  - "Alexa, english talk pal" with no query → should respond in Portuguese with "didn't catch a question"
  - Trigger quota exhaustion → should respond in Portuguese with quota message
  - Simulate LLM timeout → should respond in Portuguese with timeout fallback
- [ ] Test language-switching:
  - Set Echo to English; test en-US invocation and response
  - Set Echo to Portuguese; test pt-BR invocation and response
  - Confirm both locales work end-to-end

**3.3 Performance confirmation**
- [ ] Measure end-to-end latency for 5 English and 5 Portuguese queries
- [ ] Compare against ADR 0012's p90 6.08s English baseline — that number
      already leaves little margin under the 8s deadline, so "5-7s" isn't a
      safe assumption going in; confirm the real numbers, don't assume them
- [ ] Log any timeouts or 429s

### Phase 4: Documentation & Polish (Day 5)

**4.1 Update project documentation**
- [ ] Update `CHANGELOG.md` to record backlog item 3 completion
- [ ] Update `README.md` (if it exists) to mention multi-locale support
- [ ] Add a new ADR, **`0017-multi-locale-pt-br-support.md`** (numbered
      0017, not 0015 — ADR 0015 already exists, for the reasoning-leak fix;
      Plan 0005's multi-turn ADR takes 0016, built first per §11's
      recommended build order, so this one is 0017) documenting:
  - Decision to add pt-BR as a second locale on the same skill (not a separate skill)
  - Locale extraction from request payload
  - Language-aware system prompts and fallback messages
  - Testing strategy

**4.2 Close out the backlog item**
- [ ] In `.claude/napkin.md`, remove the pt-BR line from the Backlog
      section entirely — the napkin's own Curation Rules say the Backlog
      category holds **open items only**; a completed item never gets a
      "done" marker left in place there
- [ ] Add the completed-work entry to `alexa-talk-pal/CHANGELOG.md`
      instead, dated the day this ships, following the existing entry
      style (see the 2026-09-22/2026-09-23 CHANGELOG entries for the
      format: what was found, what was decided, what was verified, and a
      link to the new ADR)

**4.3 Performance metrics documentation**
- [ ] In `.claude/napkin.md` Guardrails, add a note:
  - "Portuguese (pt-BR) is supported as a second locale on the same Skill ID. Latency with Portuguese queries is comparable to English (confirm with Phase 3 testing). No separate TTS configuration needed — Alexa automatically selects Portuguese voices when the device language is pt-BR."

---

## 7. Testing Strategy

### Unit Tests (added in Phase 1)
- Locale extraction from request payloads (valid, missing, malformed)
- Language-specific message selection
- Fallback to en_US on unknown locale
- System prompt routing for both languages

### Integration Tests (Phase 2–3)
- End-to-end POST `/alexa` with simulated en-US request → relay responds in English
- End-to-end POST `/alexa` with simulated pt-BR request → relay responds in Portuguese
- Both locales' fallback paths (quota, timeout, generic error) emit language-appropriate responses

### Language-Compliance Test (new — routing correctness isn't language correctness)
- Confirming the request was routed to the Portuguese system prompt is
  **not** the same as confirming the model actually replied in Portuguese.
  Nothing stops a small free-tier model (`liquid/lfm-2.5-2.6b:free`) from
  answering in English despite a Portuguese system prompt and a Portuguese
  query — small free-tier models are not reliably instruction-following on
  language choice.
- Add an explicit check in the integration/live tests — a heuristic is
  enough (e.g., presence of common Portuguese stopwords/diacritics,
  absence of common English stopwords) — that fails the test if a pt-BR
  request comes back in English, rather than only asserting the request
  reached the right code path.

### Live Echo Tests (Phase 3)
- Real voice commands in both languages
- Latency measurements (5 queries per locale)
- Graceful failure paths verified

---

## 8. Critical Files for Implementation

Based on the codebase architecture, these are the files most critical for implementing pt-BR support:

### Core Relay Files
1. **`/home/lw_na/git/netbook/alexa-talk-pal/relay/app.py`**
   - Locale extraction logic
   - Language-aware system prompt and fallback message routing
   - Request handling for both locales

2. **`/home/lw_na/git/netbook/alexa-talk-pal/relay/fallback_messages.py`**
   - Portuguese translations of all fallback messages
   - Locale routing function for message selection

3. **`/home/lw_na/git/netbook/alexa-talk-pal/relay/tests/test_locale.py`** (new file — `test_signature.py` stays scoped to signature verification only, per its existing content)
   - New test cases for locale extraction and routing
   - Locale-aware message tests
   - Language-compliance test (§7)

### Configuration & Documentation
4. **`/home/lw_na/git/netbook/alexa-talk-pal/.env.example`**
   - Updated documentation of locale support (no new secrets)

5. **`/home/lw_na/git/netbook/alexa-talk-pal/docs/adr/0017-multi-locale-pt-br-support.md`** (new — numbered 0017; see §4's ADR-numbering note and §11's build order)
   - Architecture decision record for this work

### Alexa Console (Manual, Tracked in Repo)
- Alexa Developer Console skill configuration (pt-BR locale & interaction model)
- Interaction model JSON for pt-BR, tracked at
  **`/home/lw_na/git/netbook/alexa-talk-pal/alexa/interaction-model.pt-BR.json`**
  — matching the existing `alexa/interaction-model.json` for en-US, not
  `docs/alexa-skill/` (that path doesn't match this repo's actual layout)

---

## 9. Risk Mitigation & Fallback Plans

| Scenario | Fallback |
|---|---|
| **LLM latency unacceptable with Portuguese queries** | Revert to smaller/different free model; measure again. If no free model works, skip pt-BR in v1 and defer to Phase 5. |
| **Interaction model validation rejects Portuguese samples** | Switch to two-turn flow for pt-BR (launch → "O que você gostaria de perguntar?" → user responds). Keep en-US as single-shot. **Requires implementing `Dialog.ElicitSlot` first — not yet in the relay** (see Plan 0005's follow-up-routing gap, which needs the same primitive); budget for that as new work, not a quick fallback. |
| **Echo ASR misses Portuguese invocation name** | Keep invocation name as `"english talk pal"` (ADR 0009's actual existing name). Test with real Echo before committing. |
| **Locale field missing from request in production** | Always default to en_US; log a warning. Non-critical, but report as potential Amazon API issue. |
| **Console refuses to add second locale** | Confirm account permissions and skill ownership. If truly blocked, create a separate skill for pt-BR (documented trade-off). |

---

## 10. Success Criteria

Phase 4 pt-BR support is complete when:

1. ✓ Alexa Developer Console has a pt-BR locale on the same Skill ID with working interaction model
2. ✓ Relay extracts locale from incoming requests and routes to language-specific system prompts and messages
3. ✓ All hardcoded response strings (HELP, GOODBYE, LAUNCH, NO_QUERY, TIMEOUT, QUOTA, ERROR) have Portuguese equivalents
4. ✓ End-to-end simulator test: sending Portuguese utterances returns Portuguese responses
5. ✓ End-to-end live Echo test: Echo in Portuguese mode responds in Portuguese
6. ✓ Latency testing confirms Portuguese queries meet the 8s deadline
7. ✓ Unit and integration tests cover locale routing and message selection
8. ✓ Documentation updated (ADR, CHANGELOG, README, napkin)

---

## 11. Integration with Plan 0005 (Multi-Turn) — Not Orthogonal

An earlier draft of this plan called pt-BR work "orthogonal" to Plan 0005's
multi-turn conversational work. That was wrong: both plans modify the same
`app.py` functions, the same interaction-model artifact family, and the
same `session.attributes` payload once Plan 0005 lands. Building them
independently risks two incompatible edits landing on the same call sites.

### Shared touch points
- **`ask_llm` / `_call_chat_completions`:** Plan 0005 changes these to
  accept a message list (history) instead of today's single `query`
  string. Plan 0004 needs the *system prompt* to vary by locale on every
  call. The combined signature has to carry both, e.g.
  `ask_llm(query, locale, conversation_history=None)` — whichever plan
  lands second must not silently drop the other plan's parameter.
- **Backends (`ask_openrouter`, `ask_local_llm`):** both need the
  locale-selected system prompt *and* the trimmed history in the same
  message list.
- **`alexa_response`:** Plan 0005 adds a `session_attributes` parameter.
  Plan 0004 doesn't change this function's shape directly, but every
  locale-aware response path still has to pass through whatever
  `alexa_response` becomes.
- **System prompts:** the combined result is a **per-locale, per-mode**
  matrix, not a single constant — `VOICE_SYSTEM_PROMPT` (en-US,
  single-turn) and `CONVERSATIONAL_SYSTEM_PROMPT` (en-US, multi-turn) from
  Plan 0005, plus pt-BR equivalents of both. Four prompts, not two.
- **Interaction model:** Plan 0005's follow-up-routing fix (new sample
  utterances, and/or `Dialog.ElicitSlot`/`AMAZON.FallbackIntent` handling)
  has to exist in *both* locale models, or pt-BR users get single-turn
  behavior while en-US users get multi-turn.
- **Locale-switching mid-session:** see §2.6 above — once history exists
  (Plan 0005), a locale change mid-session needs an explicit decision on
  whether it clears history.

### Recommended build order
1. **Plan 0005 first, English only.** Land the multi-turn plumbing
   (`conversation.py`, the `session.attributes` round-trip, follow-up
   routing, and the `ask_llm`/`_call_chat_completions`/`alexa_response`
   signature changes) against the existing en-US interaction model before
   touching locale at all — it's the harder, riskier change; isolate it.
2. **Plan 0004 second, on top of the new signatures.** Add the pt-BR
   locale, system prompts, fallback messages, and interaction model against
   the *already-changed* function signatures, instead of both plans racing
   to change the same lines independently.
3. Net result: a per-locale, per-mode system prompt matrix (4 prompts), one
   `ask_llm(query, locale, conversation_history=None)` call site, and a
   single interaction-model update pass per locale that includes the
   follow-up-routing fix from day one.

Doing pt-BR first would mean redoing its locale-routing changes once Plan
0005's signature changes land — net more work than sequencing them this
way. See Plan 0005 §"Shared Touch Points with Plan 0004" for the mirror of
this section.
