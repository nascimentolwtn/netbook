# Plan 0002: Phase 2, Alexa Developer Console reconfiguration

- Status: proposed (planning only, nothing executed yet)
- Date: 2026-09-22
- Scope: napkin backlog item 2 / `architecture.md` §11 Phase 2, **console side only**
- Out of scope (covered by parallel plans): the live Echo end-to-end test, and the
  signature-verification root-CA decision (napkin backlog item 1). This plan assumes the
  current verification in `relay/app.py` is enabled and good enough for this phase.

## 0. Goal and definition of done

The reused skill (ADR 0009: same Skill ID, invocation name `english talk pal`, en-US) has to
end up like this:

1. Its endpoint is **HTTPS** `https://viscous-landlady-reappoint.ngrok-free.dev/alexa`, not
   the old AWS Lambda ARN (ADR 0003).
2. Its interaction model is the one `relay/app.py` routes on. That means an intent named exactly
   `AskAnythingIntent` with a slot named exactly `query` of type `AMAZON.SearchQuery`, plus the
   built-in Stop, Cancel and Help intents (ADR 0005).
3. **Build Model** succeeds.
4. The console's **Test tab simulator** gets a real, relay-generated reply to a launch, a
   question, help, and stop. The requests should show up in `journalctl -u talkpal-relay` on
   the netbook.

Once all four hold, the parallel plan can run the real Echo test.

## 1. What the relay actually expects

This comes from `relay/app.py` (`alexa()` and `_handle_ask_anything`). The interaction model
has to match these names exactly, character for character.

| Incoming request | Relay behaviour | Model requirement |
|---|---|---|
| `LaunchRequest` | says `"Hi, what would you like to ask?"`, session stays open | none (always sent on "open english talk pal") |
| `IntentRequest` `AskAnythingIntent` | reads `intent.slots.query.value` and sends it to the LLM | intent name `AskAnythingIntent`, slot name `query` |
| same, slot empty or missing | `"Sorry, I didn't catch a question. What would you like to ask?"` | (this is also what a misnamed slot looks like) |
| `AMAZON.StopIntent` / `AMAZON.CancelIntent` | `"Goodbye."`, ends session | include both built-ins |
| `AMAZON.HelpIntent` | help text, session stays open | include built-in |
| `SessionEndedRequest` | empty 200 | none |
| **any other intent name** | `GENERIC_ERROR_FALLBACK` (`"Sorry, I couldn't reach my brain just now."`) | this is the silent-misroute trap |
| `applicationId` != `ALEXA_SKILL_ID` env | **HTTP 400** before any routing | the real Skill ID must be in the netbook `.env` (§5) |

Consequences for the model:

- **Delete `TalkIntent`** and its slot `utterance` from the prototype. If either one remains, the
  relay answers with the "couldn't reach my brain" line, which looks like an LLM outage even
  though the real problem is a naming mismatch.
- **Do not add `AMAZON.FallbackIntent` yet.** `architecture.md` §4.1 lists it, but the relay has
  no handler for it. Any out-of-domain utterance would then get the misleading "couldn't reach
  my brain" line. Without FallbackIntent, Alexa pushes stray utterances into the closest
  intent. That is usually `AskAnythingIntent`, and when its slot is empty the relay gives the
  accurate "I didn't catch a question" reply. Adding a FallbackIntent handler to the relay is a
  possible later follow-up, not part of this phase.
- **Keep `AMAZON.NavigateHomeIntent`.** The console adds it automatically and treats it as a
  required built-in, and the build can fail if it is removed. The prototype JSON leaves it out.
  The relay does not handle it, but on a voice-only Echo it is essentially never sent to the
  skill, so the gap is acceptable.

## 2. Discrepancies found while planning (for the human to note or fix in docs)

These are recorded here only. ADRs and `architecture.md` were intentionally left unchanged.

1. **SSL certificate option.** `architecture.md` §4.1 says to choose "trusted certificate
   authority". I checked the live endpoint on 2026-09-22 with `openssl s_client`. The
   certificate is `CN = *.ngrok-free.dev`, SAN `*.ngrok-free.dev, ngrok-free.dev`, issued by
   Let's Encrypt `YE2`. That is a **wildcard** certificate from a publicly trusted CA, so the
   matching console option is the **wildcard** one (§3.2). `architecture.md` also says
   `.ngrok-free.app`, but the real domain is `.ngrok-free.dev`.
2. **ADR 0005 says the prototype had no bare-slot sample.** That is wrong: the prototype's
   `/mnt/e/dev/alexa-talk-pal/alexa-skill/interaction-model.json` does contain `"{utterance}"`
   as a `TalkIntent` sample. `architecture.md` §4.1 also lists bare `"{query}"`. A bare
   `AMAZON.SearchQuery` sample is exactly what the validator rejects, so it is a strong candidate
   for the prior "difficult to configure" experience. Neither plan below includes it.
3. **The prototype JSON has no `AMAZON.NavigateHomeIntent`.** It may not match what is really
   saved in the console, because the console could have added it back. That is one more reason
   to replace the whole model in the JSON Editor rather than edit the old one piece by piece.

## 3. Step-by-step console procedure (human executes)

Log in at <https://developer.amazon.com/alexa/console/ask> with the Amazon account that owns
the prototype skill. It must be the **same account the Echo is signed into** (ADR 0006).

### 3.1 Locate the skill and record its Skill ID

1. Find the skill in the **Alexa Skills** list. Its name is probably "English Talk Pal".
2. Click **Copy Skill ID** under the skill name (or open the skill and go to **Build → Endpoint**,
   where "Your Skill ID" is shown). The value looks like
   `amzn1.ask.skill.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`. Keep it for §5.
3. Open the skill. At the top left, make sure the language dropdown shows **English (US)**.
4. (Optional backup) Go to **Build → Interaction Model → JSON Editor** and copy the current JSON
   somewhere safe before replacing it. It is the only record of what is really live today.

### 3.2 Switch the endpoint from Lambda ARN to HTTPS

1. **Build** tab → left sidebar → **Endpoint**.
2. Change the service endpoint type from **AWS Lambda ARN** to **HTTPS**.
3. **Default Region** → URL:
   `https://viscous-landlady-reappoint.ngrok-free.dev/alexa`
   The URL must include the `/alexa` path. The bare hostname returns a 404 from Flask.
4. SSL certificate type for Default Region: select
   **"My development endpoint is a sub-domain of a domain that has a wildcard certificate from
   a certificate authority."**
   - Why: the tunnel serves `*.ngrok-free.dev`, a wildcard certificate issued by Let's Encrypt,
     a publicly trusted CA. Our host is a sub-domain covered by that wildcard. The console
     wording is about how the certificate is issued, not about whether it is trusted.
   - Not "self-signed": nothing needs uploading, and ngrok renews the Let's Encrypt certificate
     every ~90 days without any console change.
   - Fallback: if the simulator shows an SSL/handshake error with **no request logged by the
     relay**, change this to "trusted certificate authority", save, and retest. Both options
     tell Amazon to validate against public CAs, so either one is low-risk.
5. Leave the North America / Europe / Far East region-specific endpoints **empty**.
6. Click **Save Endpoints** at the top.
7. Optional cleanup, not required: the old Lambda function in AWS is now unused and can be
   deleted or left alone. It no longer receives traffic from this skill.

### 3.3 Replace the interaction model (Plan A)

1. **Build** tab → **Interaction Model** → **JSON Editor**.
2. Select all existing JSON and **replace it entirely** with the Plan A JSON in §4.2. Don't merge
   it with the old model: leftover `TalkIntent`/`{utterance}` references are the most likely
   cause of save errors.
3. Click **Save Model**. Section §6 lists the errors to expect and how to fix each one.
4. Click **Build Model**. Building takes about 30 s to a few minutes. Success looks like:
   - a "Build Successful" / "Full build successful" notification (bell icon, top right);
   - the right-hand **Skill builder checklist** showing green ticks for Invocation Name,
     Intents/Samples/Slots, Build Model and Endpoint;
   - `AskAnythingIntent (1 slot)` in the left sidebar, and no `TalkIntent`.
5. Check the invocation name under **Invocations → Skill Invocation Name**. It should be
   `english talk pal`. The JSON sets it, but confirm it wasn't overridden.

If **Save Model** or **Build Model** still fails after the fixes in §6, go to **Plan B** (§7).
Paste that JSON instead. It is a superset of Plan A.

## 4. Plan A: single-shot catch-all interaction model (ADR 0005)

### 4.1 Sample utterance design

Validator rules this list is built around:

- Every sample that contains `{query}` also has at least one carrier word. There is no bare
  `{query}` (ADR 0005).
- `{query}` is the only slot in each sample (`AMAZON.SearchQuery` cannot share a sample with
  other slots). Samples are lowercase, contain no punctuation except apostrophes, and have no
  numerals.
- No sample overlaps the built-ins. There is nothing like "help me …", "stop …" or "cancel …",
  which would compete with HelpIntent, StopIntent or CancelIntent.
- The carriers are chosen to be low in meaning. **Alexa strips carrier words from the slot
  value**, and the relay only ever receives `query` (Alexa never sends the raw utterance).
  With `"tell me {query}"`, the utterance "tell me why the sky is blue" becomes
  `query = "why the sky is blue"`, and nothing meaningful is lost.

Final list (23 samples):

```
ask {query}
ask you {query}
i want to ask {query}
i have a question {query}
my question is {query}
question {query}
tell me {query}
please tell me {query}
can you tell me {query}
could you tell me {query}
tell me about {query}
about {query}
i want to know {query}
i would like to know {query}
i'd like to know {query}
do you know {query}
explain {query}
please explain {query}
can you explain {query}
i wonder {query}
i was wondering {query}
what do you think about {query}
give me {query}
```

How these get spoken:

- One-shot: "Alexa, ask english talk pal **to tell me** why the sky is blue". Alexa treats "to"
  as a connector, and the remaining "tell me why…" matches `tell me {query}`. Another example:
  "Alexa, ask english talk pal **to explain** black holes".
- In session, after "Alexa, open english talk pal" → "Hi, what would you like to ask?", the user
  speaks a phrase with a carrier, for example "tell me why the sky is blue" or "i want to know
  who won the world cup". The relay leaves the session open after every answer. Follow-up
  questions **also** need a carrier phrase, and that is the main UX limitation of Plan A.
  Plan B removes it for the first question.

**Optional "A+" experiment, not in the JSON by default.** You could add question-word carriers
such as `what is {query}`, `why {query}`, `how do {query}` or `who is {query}` so that bare
natural questions match. The cost is that the question word is stripped: "why is the sky blue"
becomes `query = "is the sky blue"`, and the LLM may answer "yes". Try this only if Plan A's
carrier requirement feels too awkward. Check the resulting slot values in the Utterance Profiler
(§8.1) before keeping it.

### 4.2 Plan A JSON (paste into Build → Interaction Model → JSON Editor)

```json
{
  "interactionModel": {
    "languageModel": {
      "invocationName": "english talk pal",
      "intents": [
        { "name": "AMAZON.CancelIntent", "samples": [] },
        { "name": "AMAZON.HelpIntent", "samples": [] },
        { "name": "AMAZON.StopIntent", "samples": [] },
        { "name": "AMAZON.NavigateHomeIntent", "samples": [] },
        {
          "name": "AskAnythingIntent",
          "slots": [
            { "name": "query", "type": "AMAZON.SearchQuery" }
          ],
          "samples": [
            "ask {query}",
            "ask you {query}",
            "i want to ask {query}",
            "i have a question {query}",
            "my question is {query}",
            "question {query}",
            "tell me {query}",
            "please tell me {query}",
            "can you tell me {query}",
            "could you tell me {query}",
            "tell me about {query}",
            "about {query}",
            "i want to know {query}",
            "i would like to know {query}",
            "i'd like to know {query}",
            "do you know {query}",
            "explain {query}",
            "please explain {query}",
            "can you explain {query}",
            "i wonder {query}",
            "i was wondering {query}",
            "what do you think about {query}",
            "give me {query}"
          ]
        }
      ],
      "types": []
    }
  }
}
```

The built-in intents have empty `samples` on purpose. Amazon already trains "stop", "cancel",
"help" and so on. The prototype also listed `"exit"`, but Alexa reserves that word and handles
it itself, and extra built-in samples only add more ways for validation to fail.

## 5. Manual step: put the real Skill ID into the netbook `.env`

The relay compares `session.application.applicationId` with `ALEXA_SKILL_ID` on every request
(`app.py` lines ~428-434). If the value is empty or wrong, **every** request returns HTTP 400,
and the simulator says *"There was a problem with the requested skill's response."*

Only the human can do this. The planner and agents must not read or print any `.env` file.

1. Take the Skill ID from §3.1 step 2.
2. On the netbook, set `ALEXA_SKILL_ID=amzn1.ask.skill.…` in the `.env` that
   `talkpal-relay.service` actually loads (ADR 0008; `systemctl cat talkpal-relay` shows the
   working directory if unsure).
3. In the same file, confirm `DEBUG_SKIP_SIGNATURE` is empty or false (architecture.md §11
   Phase 2: "turn signature verification ON").
4. `sudo systemctl restart talkpal-relay`.
5. Check the result without opening `.env`: `journalctl -u talkpal-relay -n 20` should show the
   startup line `... (DEBUG_SKIP_SIGNATURE=False)`.

Diagnostic shortcut: if you are unsure the ID is right, the first simulator request that fails
this check logs `applicationId mismatch: got 'amzn1.ask.skill.…'` in journald. The value it
prints is the ID Amazon is really sending. That log line reveals nothing secret, because the
Skill ID is not a secret.

## 6. Expected "Save Model" / "Build Model" friction and fixes

| Symptom (console error, paraphrased) | Cause | Fix |
|---|---|---|
| Sample utterance containing only an `AMAZON.SearchQuery` slot / "must contain a carrier phrase" | a bare `{query}` (or leftover `{utterance}`) sample | remove it; Plan A already has none |
| "Slot `utterance` referenced in sample is not defined" (or similar) | old `TalkIntent` samples survived a partial edit | replace the **whole** JSON; don't merge |
| "Required built-in intent `AMAZON.NavigateHomeIntent` is missing" | pasted the prototype-shaped model | Plan A includes it; re-add if deleted |
| "Utterance conflicts with …" / "duplicate sample" | the same carrier appears twice, or a sample overlaps a built-in | delete the duplicate; don't put "help"/"stop"/"cancel" words in carriers |
| "Invalid characters in sample" | punctuation (`?`, `,`, `.`) or numerals | lowercase words and apostrophes only; spell out numbers |
| "Slot type `AMAZON.SearchQuery` cannot be used with other slots in the same utterance" | a second slot was added to a sample | keep `{query}` as the only slot |
| Save succeeds, **Build** fails with a vague "internal error" | occasional console flakiness | wait a minute, click **Build Model** again; reload page if needed |
| "Invocation name is invalid" | the JSON's `invocationName` has capitals, digits, or a launch word | keep `english talk pal` exactly (ADR 0009) |

Two workarounds, in order, if the validator keeps rejecting Plan A:

1. **Trim to a core list and grow it back.** Save with just five samples (`ask {query}`,
   `tell me {query}`, `i want to know {query}`, `explain {query}`, `my question is {query}`).
   If that saves, paste the rest back in batches of about five. The error then points to a
   specific carrier.
2. **Switch to Plan B (§7).** It keeps every Plan A sample and adds a dialog-model path whose
   slot-filling samples accept a bare `{query}`. It also avoids needing a carrier phrase for the
   first question.

## 7. Plan B: two-turn fallback (ready to paste)

### 7.1 How it works

`AskAnythingIntent` gets a **dialog model**. The `query` slot becomes *required*, has an
elicitation prompt, and has **slot-filling samples**. **Auto-delegation** is turned on. When the
intent is triggered **without** a query, Alexa itself asks "What would you like to ask?" and
does not call the relay for that turn. The user's next reply is interpreted as filling `query`.
In that position a bare `{query}` sample is allowed, because the carrier-phrase rule applies to
intent samples, not slot-filling samples. The relay then gets an ordinary `AskAnythingIntent`
with `slots.query.value` filled in (plus a `dialogState: COMPLETED` field it ignores).

**No relay code changes are needed.** The relay already reads `slots.query.value` and ignores
dialog state.

Conversation shapes:

- Two-turn: "Alexa, ask english talk pal **a question**" → *(Alexa, auto-delegated)* "What would
  you like to ask?" → "why is the sky blue" → relay gets `query = "why is the sky blue"`, with
  nothing stripped.
- One-shot still works: "Alexa, ask english talk pal to tell me why the sky is blue" fills
  the slot right away, so there is no elicitation and the request goes straight to the relay.
- "Alexa, open english talk pal" → relay's launch greeting → the user must still use a carrier
  (Plan A behaviour). If the utterance maps to `AskAnythingIntent` with an empty slot,
  auto-delegation now asks "What would you like to ask?" instead of the relay's "didn't catch
  a question" line.

Known limitation: after the relay answers, the session is no longer in slot elicitation, so a
bare follow-up question again needs a carrier or the trigger "a question". To get fully bare
turns, the relay would have to return a `Dialog.ElicitSlot` directive from `LaunchRequest` and
after each answer, with `updatedIntent: {name: "AskAnythingIntent", confirmationStatus: "NONE",
slots: {query: {name: "query", confirmationStatus: "NONE"}}}`, `slotToElicit: "query"`. That is
a relay code change (**not part of this phase**) and is listed as a follow-up in §10.

### 7.2 Plan B JSON (paste into JSON Editor, replacing everything)

```json
{
  "interactionModel": {
    "languageModel": {
      "invocationName": "english talk pal",
      "intents": [
        { "name": "AMAZON.CancelIntent", "samples": [] },
        { "name": "AMAZON.HelpIntent", "samples": [] },
        { "name": "AMAZON.StopIntent", "samples": [] },
        { "name": "AMAZON.NavigateHomeIntent", "samples": [] },
        {
          "name": "AskAnythingIntent",
          "slots": [
            {
              "name": "query",
              "type": "AMAZON.SearchQuery",
              "samples": [
                "{query}",
                "my question is {query}",
                "i want to know {query}",
                "tell me {query}"
              ]
            }
          ],
          "samples": [
            "a question",
            "question",
            "i have a question",
            "i've got a question",
            "something",
            "ask something",
            "ask a question",
            "ask {query}",
            "ask you {query}",
            "i want to ask {query}",
            "i have a question {query}",
            "my question is {query}",
            "question {query}",
            "tell me {query}",
            "please tell me {query}",
            "can you tell me {query}",
            "could you tell me {query}",
            "tell me about {query}",
            "about {query}",
            "i want to know {query}",
            "i would like to know {query}",
            "i'd like to know {query}",
            "do you know {query}",
            "explain {query}",
            "please explain {query}",
            "can you explain {query}",
            "i wonder {query}",
            "i was wondering {query}",
            "what do you think about {query}",
            "give me {query}"
          ]
        }
      ],
      "types": []
    },
    "dialog": {
      "intents": [
        {
          "name": "AskAnythingIntent",
          "delegationStrategy": "ALWAYS",
          "confirmationRequired": false,
          "prompts": {},
          "slots": [
            {
              "name": "query",
              "type": "AMAZON.SearchQuery",
              "confirmationRequired": false,
              "elicitationRequired": true,
              "prompts": {
                "elicitation": "Elicit.Slot.AskAnythingIntent.query"
              }
            }
          ]
        }
      ],
      "delegationStrategy": "ALWAYS"
    },
    "prompts": [
      {
        "id": "Elicit.Slot.AskAnythingIntent.query",
        "variations": [
          { "type": "PlainText", "value": "What would you like to ask?" }
        ]
      }
    ]
  }
}
```

Plan B friction notes:

- If Save rejects bare `"{query}"` in the **slot** samples, which is not expected, remove that
  line. The remaining three slot samples still let most answers fill the slot.
- "Required slot must have an elicitation prompt": the `prompts` block and the
  `prompts.elicitation` id have to match exactly (`Elicit.Slot.AskAnythingIntent.query`). Pasting
  the whole JSON keeps them in sync.
- After saving, check in the UI (**AskAnythingIntent → Dialog Delegation Strategy**) that it
  reads **enable auto delegation**. If it says "fallback to skill setting", the skill-level
  setting has to be enabled instead (**Build → Interfaces**/**Skill-level auto delegation**
  toggle, depending on console version).

## 8. Validation before declaring Phase 2 (console side) done

### 8.1 Model-only check, no endpoint traffic: Utterance Profiler

**Build** tab → **Evaluate Model** (top right) → **Utterance Profiler**. Type each line below
and confirm the resolved intent and slot. This does not call the relay and does not count
against the LLM quota.

| Utterance typed | Expected intent | Expected `query` |
|---|---|---|
| `tell me why the sky is blue` | AskAnythingIntent | `why the sky is blue` |
| `i want to know who invented the telephone` | AskAnythingIntent | `who invented the telephone` |
| `explain black holes` | AskAnythingIntent | `black holes` |
| `help` | AMAZON.HelpIntent | none |
| `stop` | AMAZON.StopIntent | none |
| `a question` (Plan B only) | AskAnythingIntent | empty (dialog will elicit) |

If a question resolves to a built-in, or the slot comes back as `utterance` rather than
`query`, fix the model before touching the simulator.

### 8.2 End-to-end in the console simulator (text, no Echo)

1. On the netbook, in a separate terminal: `ssh netbook@192.168.4.36`, then
   `journalctl -u talkpal-relay -f`.
2. **Test** tab → the "Skill testing is enabled in:" dropdown → **Development**. It is **Off** by
   default and has to be switched on once.
3. Type (or hold the mic and speak) in order:

| Simulator input | Expected Alexa output | Expected journald |
|---|---|---|
| `open english talk pal` | "Hi, what would you like to ask?" | request logged, no warnings |
| `tell me why the sky is blue` | a 2–4 sentence LLM answer | request logged |
| `help` | "You can ask me pretty much anything…" | request logged |
| `stop` | "Goodbye." | request logged (+ SessionEnded) |
| `ask english talk pal to explain black holes` | LLM answer (one-shot path) | request logged |
| Plan B only: `ask english talk pal a question`, then `why is the sky blue` | "What would you like to ask?", then an LLM answer | **one** relay request (the elicitation turn stays inside Alexa) |

4. For each turn, open the **JSON Input** pane and confirm `request.intent.name` is
   `AskAnythingIntent` and `request.intent.slots.query.value` contains the question.

Reading failures:

| What you see | Most likely cause | Where to look |
|---|---|---|
| "There was a problem with the requested skill's response", **nothing** in journald | endpoint URL wrong or missing `/alexa`, SSL option, tunnel down | §3.2; `systemctl status talkpal-tunnel`; `curl https://viscous-landlady-reappoint.ngrok-free.dev/health` from the PC (expect JSON, not ngrok HTML) |
| Same message, journald `applicationId mismatch: got '…'` | `ALEXA_SKILL_ID` missing or wrong | §5 |
| Same message, journald `Signature verification failed: …` | signature-verification domain | **stop and hand to the parallel root-CA plan**. Do **not** set `DEBUG_SKIP_SIGNATURE` to get past it |
| "Sorry, I couldn't reach my brain just now" on a question | intent name not `AskAnythingIntent` (check JSON Input), or an LLM backend error (journald traceback) | §1, §4.2 |
| "Sorry, I didn't catch a question" on a clear question | slot not named `query`, or the utterance didn't hit a carrier | Utterance Profiler §8.1 |
| "Sorry, that took me too long…" | LLM latency over the relay's 5 s read timeout | not a console problem; note for Phase 3 |

Phase 2 (console side) is **done** when every row of the §8.2 happy-path table passes and
journald shows no signature or applicationId warnings. At that point the skill is ready for the
parallel live-Echo test plan.

Each simulator question uses one LLM call against the daily cap (`DAILY_CAP`, default 45). The
six rows above cost about three calls.

## 9. Human-only steps vs. ready-to-paste handoffs

**Human-only (requires Amazon login, the netbook `.env`, or judgment):**

1. Log in to the Alexa Developer Console with the account that owns the prototype skill (§3).
2. Copy the real Skill ID (§3.1).
3. Optionally back up the current console JSON (§3.1 step 4).
4. Change the endpoint type, enter the URL, choose the SSL option, and save (§3.2).
5. Paste the model JSON, then Save Model and Build Model, and iterate on errors (§3.3, §6).
6. If Plan A won't validate, decide to switch to Plan B (§7).
7. Set `ALEXA_SKILL_ID` (and confirm `DEBUG_SKIP_SIGNATURE` is off) in the netbook `.env`, then
   restart `talkpal-relay` (§5).
8. Enable Development testing, and run the Utterance Profiler and simulator checks (§8).
9. Optionally delete the orphaned AWS Lambda function (§3.2 step 7).

**Ready-to-paste, no judgment needed:**

- Endpoint URL: `https://viscous-landlady-reappoint.ngrok-free.dev/alexa`
- SSL option: "…sub-domain of a domain that has a wildcard certificate from a certificate
  authority"
- Plan A interaction model JSON (§4.2)
- Plan B interaction model JSON (§7.2)
- Utterance Profiler and simulator test scripts (§8.1, §8.2)
- The `.env` line shape: `ALEXA_SKILL_ID=<value copied from console>`

## 10. Follow-ups this plan surfaces (not done here)

- Update `architecture.md` §4.1 to match §2 above: wildcard SSL option, `.ngrok-free.dev`, no
  bare `{query}`, no `AMAZON.FallbackIntent` until the relay handles it.
- ADR 0005's statement that the prototype had no bare-slot sample needs a correction note, since
  the prototype JSON contains `"{utterance}"`. That supports the ADR's own suspicion about where
  the prior difficulty came from.
- Optional relay changes, for a later phase only:
  (a) an `AMAZON.FallbackIntent` handler with an honest "I didn't understand" line;
  (b) a `Dialog.ElicitSlot` on `LaunchRequest` and after each answer, so every turn can be a
  bare question (§7.1);
  (c) a `reprompt` on open-session responses, so Alexa re-asks instead of closing silently
  after about 8 s.
- When one of the plans validates, record which one (A or B) and any validator surprises in
  `CHANGELOG.md` and the napkin. If Plan B becomes the default, add an ADR amending 0005.
