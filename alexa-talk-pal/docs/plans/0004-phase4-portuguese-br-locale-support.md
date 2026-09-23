# Plan 0004: Add Portuguese-BR (pt-BR) Language Support

## Executive Summary

This plan extends the alexa-talk-pal ecosystem to support Portuguese (Brazil) alongside the existing English (US) support. The work is scoped as a Phase 4 candidate (post-v1) and follows the existing architectural patterns. The key insight is that Alexa Skills support multiple locales under a single Skill ID (per ADR 0009), so we create a second locale configuration within the existing skill rather than building a separate skill.

---

## 1. Current State Analysis

**Locale Routing Currently:**
- Alexa console manages one interaction model per locale (both en-US and pt-BR models live in the same skill, independently configured)
- Amazon's Alexa Services route incoming requests to the appropriate model based on the Echo device's configured language
- The `/alexa` endpoint receives all requests (both locales) at the same URL
- Request payload includes `request.locale` field indicating which locale was matched (e.g., `"pt_BR"` or `"en_US"`)

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
  - **Invocation name:** Portuguese equivalent (e.g., `"assistente conversa"` or `"talk pal"` — invocation names are not typically translated)
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
- No new env vars strictly required for basic support
- Optional: add `LOCALE_PREFERENCE` to allow forcing a single locale for testing (default: auto-detect from request)
- Optional: add `SUPPORTED_LOCALES` list for validation

### 2.5 Tests (`relay/tests/`)
- Add test cases for locale extraction from requests
- Test fallback message selection for both locales
- Verify both en-US and pt-BR responses shape correctly

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
- **Impact:** Model latency may differ for Portuguese inputs. Recommended: re-run `relay/measure_latency.py` with Portuguese test queries to confirm the chosen model meets the 8s deadline with pt-BR requests.
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
| **Interaction model validation rejects Portuguese samples** | Low | Alexa's validator may have rules specific to English; blocks console configuration | Use existing en-US samples as template; test with 2–3 invocation + carrier combinations; fallback: use two-turn flow if samples fail |
| **System prompt quality degrades in Portuguese** | Medium | Model may respond less coherently in Portuguese, or violate "short sentences, no markdown" instruction | Test system prompt with a few live queries in the simulator; iterate on wording if responses are verbose/contain markdown |
| **Invocation name collision in Portuguese** | Low | Portuguese carrier words (e.g., `"assistente"`) might collide with built-in intents or be hard for Alexa ASR | Test with real Echo ASR recognition; common invocation name patterns exist (`"talk pal"` is language-neutral; sticking with it is safest) |
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
- [ ] Run `relay/measure_latency.py` (or equivalent) with 10–15 Portuguese test queries
- [ ] Example queries: `"Por que o céu é azul?"`, `"O que é a fotossíntese?"`, `"Quanto é 2 + 2?"`
- [ ] Record latency distribution, tail (p95/p99), any 429s or timeouts
- [ ] Confirm all queries complete under 5s (leaving 3s buffer for the 8s deadline)
- [ ] If latency is unacceptable, consider fallback model options

**0.3 Interaction model structure validation**
- [ ] Export the current en-US interaction model from the console (Build → Interaction Model → JSON Editor)
- [ ] Study the JSON structure, specifically:
  - `languageModel.invocationName` field
  - `languageModel.intents` array structure (identical across locales)
  - `languageModel.intents[n].samples` array (where translation happens)
- [ ] Draft Portuguese sample utterances based on the en-US model
- [ ] Create a shell pt-BR interaction model JSON for manual insertion in Phase 2

**0.4 Request locale field validation**
- [ ] In the console simulator, send a test request from both en-US and a simulated pt-BR locale (if supported)
- [ ] Confirm the request JSON includes `request.locale` with the expected value (`"en_US"` or `"pt_BR"` or `"pt-BR"`)
- [ ] If locale field is missing or named differently, document the actual field name

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
  - Add a `_extract_locale(parsed_body) -> str` function that reads `request.locale` from the payload
  - Normalize locale strings (handle both `"pt_BR"` and `"pt-BR"`)
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
- [ ] In `relay/tests/test_signature.py`, add tests for:
  - `_extract_locale()` with valid pt_BR, en_US, missing locale
  - Locale normalization (pt-BR → pt_BR)
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
- [ ] **Invocation name:** Keep it simple and language-neutral; `"talk pal"` works across both locales (or choose a Portuguese equivalent if preferred; test ASR first)
  - Safest option: stick with `"talk pal"` (English phrase, easy to recognize in Portuguese)
  - Alternative: translate to `"assistente conversa"` or similar (test ASR collision risk)
- [ ] **Intents:** Click **JSON Editor** and paste the pt-BR model structure (prepared in Phase 0)
  - **AskAnythingIntent** with `query` slot of type `AMAZON.SearchQuery` (identical structure to en-US)
  - **Sample utterances** in Portuguese:
    - `"fale sobre {query}"`
    - `"me pergunte {query}"`
    - `"diga {query}"`
    - `"o que é {query}"`
    - `"como {query}"`
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
  - "abra talk pal" → should trigger LaunchRequest → relay responds with Portuguese greeting
  - "por que o céu é azul" → should match AskAnythingIntent + query slot → relay sends to LLM in Portuguese, responds with Portuguese text
  - "ajuda" → should match AMAZON.HelpIntent → relay responds with Portuguese help text
  - "parar" → should match AMAZON.StopIntent → relay responds with Portuguese goodbye
- [ ] Verify relay logs (on the netbook, `journalctl -u talkpal-relay`) show successful requests with locale pt_BR
- [ ] Verify all responses are in Portuguese

### Phase 3: Live Echo Testing (Day 4)

**3.1 Echo device language configuration**
- [ ] On the Echo device or Alexa app, set the language to **Portuguese (Brazil)**
  - Or use a second Echo that's already in Portuguese
  - Or temporarily change a shared Echo's language for testing

**3.2 Live testing with real voice**
- [ ] "Alexa, talk pal, por que o céu é azul?"
- [ ] Wait for response; confirm:
  - Alexa recognizes the invocation and Portuguese query
  - Relay receives the request with `locale: "pt_BR"`
  - Response is in Portuguese
  - Voice output uses a Portuguese voice (Alexa automatically selects one)
- [ ] Test failure modes:
  - "Alexa, talk pal" with no query → should respond in Portuguese with "didn't catch a question"
  - Trigger quota exhaustion → should respond in Portuguese with quota message
  - Simulate LLM timeout → should respond in Portuguese with timeout fallback
- [ ] Test language-switching:
  - Set Echo to English; test en-US invocation and response
  - Set Echo to Portuguese; test pt-BR invocation and response
  - Confirm both locales work end-to-end

**3.3 Performance confirmation**
- [ ] Measure end-to-end latency for 5 English and 5 Portuguese queries
- [ ] Confirm all queries complete within 5–7s (comfortable margin under 8s deadline)
- [ ] Log any timeouts or 429s

### Phase 4: Documentation & Polish (Day 5)

**4.1 Update project documentation**
- [ ] Update `CHANGELOG.md` to record backlog item 3 completion
- [ ] Update `README.md` (if it exists) to mention multi-locale support
- [ ] Add a new ADR (e.g., `0015-multi-locale-pt-br-support.md`) documenting:
  - Decision to add pt-BR as a second locale on the same skill (not a separate skill)
  - Locale extraction from request payload
  - Language-aware system prompts and fallback messages
  - Testing strategy

**4.2 Move backlog item to napkin**
- [ ] In `.claude/napkin.md`, move the pt-BR item from Backlog to the relevant completed section (or a new "Phase 4 Completed" section if that section doesn't exist yet)

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

3. **`/home/lw_na/git/netbook/alexa-talk-pal/relay/tests/test_signature.py`**
   - New test cases for locale extraction and routing
   - Locale-aware message tests

### Configuration & Documentation
4. **`/home/lw_na/git/netbook/alexa-talk-pal/.env.example`**
   - Updated documentation of locale support (no new secrets)

5. **`/home/lw_na/git/netbook/alexa-talk-pal/docs/adr/0015-multi-locale-pt-br-support.md`** (new)
   - Architecture decision record for this work

### Alexa Console (Manual, Not in Repo)
- Alexa Developer Console skill configuration (pt-BR locale & interaction model)
- Interaction model JSON for pt-BR (can be tracked in repo as `docs/alexa-skill/interaction-model-pt-br.json` for reference)

---

## 9. Risk Mitigation & Fallback Plans

| Scenario | Fallback |
|---|---|
| **LLM latency unacceptable with Portuguese queries** | Revert to smaller/different free model; measure again. If no free model works, skip pt-BR in v1 and defer to Phase 5. |
| **Interaction model validation rejects Portuguese samples** | Switch to two-turn flow for pt-BR (launch → "O que você gostaria de perguntar?" → user responds). Keep en-US as single-shot. |
| **Echo ASR misses Portuguese invocation name** | Keep invocation name as `"talk pal"` (language-neutral). Test with real Echo before committing. |
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

## 11. Integration with Other Phase 4 Items

This pt-BR work is **orthogonal** to other Phase 4 candidates:

- **Conversational memory (napkin item 2):** Locale-agnostic. Session attributes can store multi-turn context for both English and Portuguese. No interaction.
- **Progressive response (architecture.md Phase 4):** Locale-agnostic. Interstitial "let me think about that" can be localized separately. No blocker.
- **Local LLM backend (ADR 0013 follow-up):** Locale-agnostic. LLM input language is independent of backend choice. No blocker.

All four can be developed in parallel; pt-BR work requires no dependencies on the others.
