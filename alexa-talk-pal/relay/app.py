"""talkpal-relay — Flask relay between the Alexa Skills Kit and OpenRouter.

See docs/architecture.md sections 4.2 (signature verification), 5.2
(per-request logic), 5.3 (the ~8s Alexa deadline and its defensive
measures). Secrets load from `.env` via python-dotenv per ADR 0008 (NOT
the /etc/talkpal/talkpal.env path architecture.md's earlier draft
describes). `cryptography` comes from the apt-installed system package,
not pip, per ADR 0007 — the venv must be created with
`--system-site-packages --without-pip`.

DEBUG_SKIP_SIGNATURE (env var, default OFF) bypasses Alexa signature
verification for local/dev testing only. It must stay unset/false in any
real deployment — signature verification is the front door lock
(architecture.md §9.2).

Locale (docs/plans/0004) and multi-turn conversation (docs/plans/0005)
were implemented in parallel on separate branches, each against the
unmodified single-turn baseline, then reconciled here into the combined
target signature both plans call for: `ask_llm(query, locale,
conversation_history=None)` (plan 0005 §14 / plan 0004 §11). See
docs/adr/0016 and docs/adr/0017 for each plan's individual decisions;
this file is the merge of both.
"""
import base64
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from urllib.parse import urlparse

import requests
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv
from flask import Flask, jsonify, request

from conversation import (
    append_to_history,
    build_messages_with_history,
    extract_conversation_history,
    session_attributes_from_history,
    trim_conversation_history,
)
from fallback_messages import get_messages

load_dotenv()

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Config (env vars — see .env.example)
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "")
OPENROUTER_FALLBACK_MODEL = os.environ.get("OPENROUTER_FALLBACK_MODEL", "")
ALEXA_SKILL_ID = os.environ.get("ALEXA_SKILL_ID", "")
DAILY_CAP = int(os.environ.get("DAILY_CAP", "45") or "45")
DEBUG_SKIP_SIGNATURE = os.environ.get("DEBUG_SKIP_SIGNATURE", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Backend switch (napkin backlog item 3 / ADR 0013). "openrouter" (default)
# is the only backend with a fallback model and 429 handling; "local" is a
# hard switch to the Windows PC llama.cpp server, no fallback between the
# two. Both backends need a bigger max_tokens than a plain chat model --
# their hybrid reasoning shares the same token budget as the spoken answer.
# Confirmed live for OPENROUTER_MODEL (liquid/lfm-2.5-2.6b:free): reasoning
# is mandatory for this model on OpenRouter's free endpoint (its API
# rejects `reasoning: {enabled: false}` with "Reasoning is mandatory for
# this endpoint") and consistently burns ~148-150 of a 150-token budget,
# leaving `content` empty/None (`finish_reason: "length"`). 500 resolved it
# cleanly (`finish_reason: "stop"`) across several live test queries, same
# value chosen for the local backend in ADR 0012/0013.
INFERENCE_BACKEND = os.environ.get("INFERENCE_BACKEND", "openrouter").strip().lower()
LOCAL_LLM_BASE_URL = os.environ.get(
    "LOCAL_LLM_BASE_URL", "http://192.168.4.55:11434/v1/chat/completions"
)
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "LFM2.5-2.6B-Q4_K_M.gguf")
LOCAL_LLM_MAX_TOKENS = int(os.environ.get("LOCAL_LLM_MAX_TOKENS", "500") or "500")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_CONNECT_TIMEOUT = 2
OPENROUTER_READ_TIMEOUT = 5
OPENROUTER_MAX_TOKENS = int(os.environ.get("OPENROUTER_MAX_TOKENS", "500") or "500")

# ---------------------------------------------------------------------------
# Locale support (docs/plans/0004). Alexa's wire format is hyphenated
# BCP-47 ("pt-BR", "en-US") -- see _normalize_locale.
# ---------------------------------------------------------------------------
DEFAULT_LOCALE = "en_US"

# Internal locale keys the relay actually has content for.
SUPPORTED_LOCALE_KEYS = ("en_US", "pt_BR")


def _normalize_locale(raw_locale):
    """Normalizes a locale string to one of SUPPORTED_LOCALE_KEYS.

    Alexa sends `request.locale` hyphenated per BCP-47 (e.g. "pt-BR",
    "en-US") -- that's the real wire format this normalizes from, not an
    underscore form. Accepts underscore input too (e.g. "pt_BR") since
    it's a trivial superset to support. Case-insensitive. Falls back to
    DEFAULT_LOCALE for anything missing, malformed, or not one of the
    locales this relay has content for.
    """
    if not raw_locale or not isinstance(raw_locale, str):
        return DEFAULT_LOCALE
    normalized = raw_locale.strip().replace("-", "_")
    for key in SUPPORTED_LOCALE_KEYS:
        if normalized.lower() == key.lower():
            return key
    return DEFAULT_LOCALE


def _extract_locale(parsed_body):
    """Reads `request.locale` from an Alexa request payload and normalizes
    it to an internal locale key. Defaults to DEFAULT_LOCALE if the field
    is missing, the payload is malformed, or the locale isn't one this
    relay has content for."""
    try:
        raw_locale = (parsed_body.get("request") or {}).get("locale")
    except AttributeError:
        raw_locale = None
    return _normalize_locale(raw_locale)


# Single-turn system prompts (docs/plans/0004) -- kept as a reference point
# (ADR 0015 discusses the en_US text) but no longer read by any live code
# path: every turn now goes through the multi-turn message-building
# pipeline below, even a first turn with no history yet (plan 0005 §2.2).
SYSTEM_PROMPTS = {
    "en_US": (
        "You are a helpful voice assistant answering a spoken question. "
        "Reply in 2 to 4 short sentences of plain spoken prose. Do not use "
        "markdown, bullet lists, code, emoji, or URLs -- your reply will be "
        "read aloud exactly as written. Only give your final answer -- never "
        "show your reasoning, thinking, or planning process."
    ),
    "pt_BR": (
        "Você é um assistente de voz útil respondendo a uma pergunta falada. "
        "Responda em português do Brasil, em 2 a 4 frases curtas de prosa "
        "falada simples. Não use markdown, listas, código, emojis ou URLs "
        "-- sua resposta será lida em voz alta exatamente como está escrita. "
        "Dê apenas a resposta final -- nunca mostre seu raciocínio, "
        "pensamento ou planejamento."
    ),
}
VOICE_SYSTEM_PROMPT = SYSTEM_PROMPTS[DEFAULT_LOCALE]

# Live, per-locale, multi-turn-aware system prompts (docs/plans/0005 §2.2 +
# docs/plans/0004 §11: "the combined result is a per-locale, per-mode
# matrix ... four prompts, not two"). Every request, single-turn or
# multi-turn, builds its message list with one of these.
CONVERSATIONAL_SYSTEM_PROMPTS = {
    "en_US": (
        "You are a helpful voice assistant in an ongoing conversation. "
        "You have access to previous turns in this conversation. "
        "Reply in 2 to 4 short sentences of plain spoken prose. "
        "Do not use markdown, bullet lists, code, emoji, or URLs -- "
        "your reply will be read aloud exactly as written. "
        "If the user asks a follow-up (e.g., 'tell me more', 'and why', 'how'), "
        "reference the prior context to give a natural continuation."
    ),
    "pt_BR": (
        "Você é um assistente de voz útil em uma conversa em andamento. "
        "Você tem acesso aos turnos anteriores desta conversa. "
        "Responda em português do Brasil, em 2 a 4 frases curtas de prosa "
        "falada simples. Não use markdown, listas, código, emojis ou URLs "
        "-- sua resposta será lida em voz alta exatamente como está escrita. "
        "Se o usuário fizer uma pergunta de continuação (por exemplo, "
        "'me conta mais', 'e por quê', 'como'), use o contexto anterior "
        "para dar uma continuação natural."
    ),
}
# Back-compat alias (en_US) -- measure_latency.py and pre-locale tests
# import this bare name.
CONVERSATIONAL_SYSTEM_PROMPT = CONVERSATIONAL_SYSTEM_PROMPTS[DEFAULT_LOCALE]


def _conversational_system_prompt_for_locale(locale):
    return CONVERSATIONAL_SYSTEM_PROMPTS.get(locale, CONVERSATIONAL_SYSTEM_PROMPTS[DEFAULT_LOCALE])


# Synthetic "user query" sent to the LLM when AMAZON.FallbackIntent fires
# with existing conversation history (plan 0005 §2.5, option 3).
# FallbackIntent requests never carry the raw utterance Alexa's NLU
# couldn't match, so there's no real query text to forward -- this stands
# in for a bare follow-up like "tell me more" or "and why" that didn't
# match AskAnythingIntent's carrier-phrase samples. It gets appended to
# history as the "user" turn, same as any other query. Locale-keyed so a
# Portuguese session doesn't get an English line injected into its history
# (plan 0004 §2.6's "no mixed-language history" principle, extended to
# this synthetic query -- neither plan anticipated this intersection on
# its own, so this is new work done as part of reconciling the two).
FALLBACK_CONTINUATION_QUERIES = {
    "en_US": "Please continue based on what we were just discussing.",
    "pt_BR": "Por favor, continue com base no que estávamos discutindo.",
}
FALLBACK_CONTINUATION_QUERY = FALLBACK_CONTINUATION_QUERIES[DEFAULT_LOCALE]


def _fallback_continuation_query(locale):
    return FALLBACK_CONTINUATION_QUERIES.get(locale, FALLBACK_CONTINUATION_QUERIES[DEFAULT_LOCALE])


_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>(.*?)</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)


def _strip_leaked_reasoning(text):
    """Defensive cleanup for a raw <think>...</think> block leaking into
    visible `content` -- observed once on the local backend under one
    llama.cpp config (2026-09-21 CHANGELOG entry), not reproduced against
    OpenRouter's cleanly-separated `reasoning`/`reasoning_details` fields
    (which this code never reads as spoken text). Cheap insurance against
    either backend doing it again. A closed block is removed and whatever
    follows is kept; an unclosed block (reasoning cut off mid-thought by
    the token budget, finish_reason="length") has nothing usable after it,
    so it's treated as empty rather than spoken as a fragment."""
    if not text:
        return text
    text = _THINK_BLOCK_RE.sub("", text).strip()
    if _THINK_OPEN_RE.search(text):
        return ""
    return text


# Locale-aware response strings for fixed (non-LLM) turns (docs/plans/0004
# §6 Phase 1.3). Timeout / quota-exhausted / generic-error text comes from
# fallback_messages.get_messages() instead, keyed the same way.
RESPONSE_TEXTS = {
    "en_US": {
        "HELP": "You can ask me pretty much anything -- just ask a question and I'll do my best to answer.",
        "GOODBYE": "Goodbye.",
        "LAUNCH": "Hi, what would you like to ask?",
        "NO_QUERY": "Sorry, I didn't catch a question. What would you like to ask?",
        "RATE_LIMIT": "I'm getting a lot of questions right now. Try again in a minute?",
    },
    "pt_BR": {
        "HELP": "Você pode me perguntar praticamente qualquer coisa -- é só fazer uma pergunta que eu farei o meu melhor para responder.",
        "GOODBYE": "Até logo.",
        "LAUNCH": "Oi, o que você gostaria de perguntar?",
        "NO_QUERY": "Desculpe, não entendi a pergunta. O que você gostaria de perguntar?",
        "RATE_LIMIT": "Estou recebendo muitas perguntas agora. Tente de novo daqui a um minuto?",
    },
}


def _get_response_text(key, locale):
    """Returns the locale-specific response string for `key`. Falls back
    to en_US if the locale (or, defensively, the key itself) isn't found
    -- _extract_locale already normalizes to a supported key, but this
    stays safe if called with something else."""
    texts = RESPONSE_TEXTS.get(locale) or RESPONSE_TEXTS[DEFAULT_LOCALE]
    return texts.get(key, RESPONSE_TEXTS[DEFAULT_LOCALE].get(key))


# Back-compat flat aliases (en_US) -- test_multiturn.py and any other
# pre-locale caller reference these bare names directly.
HELP_TEXT = RESPONSE_TEXTS[DEFAULT_LOCALE]["HELP"]
GOODBYE_TEXT = RESPONSE_TEXTS[DEFAULT_LOCALE]["GOODBYE"]
LAUNCH_GREETING = RESPONSE_TEXTS[DEFAULT_LOCALE]["LAUNCH"]
NO_QUERY_TEXT = RESPONSE_TEXTS[DEFAULT_LOCALE]["NO_QUERY"]
RATE_LIMIT_FALLBACK = RESPONSE_TEXTS[DEFAULT_LOCALE]["RATE_LIMIT"]
GENERIC_ERROR_FALLBACK = get_messages(DEFAULT_LOCALE)["GENERIC_ERROR"]
QUOTA_EXHAUSTED_FALLBACK = get_messages(DEFAULT_LOCALE)["QUOTA_EXHAUSTED"]
TIMEOUT_FALLBACK = get_messages(DEFAULT_LOCALE)["TIMEOUT"]


# ---------------------------------------------------------------------------
# Alexa response shaping
# ---------------------------------------------------------------------------
def alexa_response(speech_text, end_session=False, session_attributes=None):
    """Shape a minimal valid Alexa Skills Kit response body.

    `session_attributes` is only included in the response when not None
    (plan 0005 §3.3) -- every call site that doesn't end the session must
    pass the current conversation history through explicitly (plan §2.6);
    there's no implicit default that does the right thing by omission.
    """
    resp = {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": speech_text},
            "shouldEndSession": end_session,
        },
    }
    if session_attributes is not None:
        resp["sessionAttributes"] = session_attributes
    return jsonify(resp)


# ---------------------------------------------------------------------------
# Daily quota counter — in-memory, persisted to a local JSON file on each
# increment so a restart mid-day doesn't silently reset the cap. Fine as a
# single-process in-memory counter (waitress, low traffic -- one person
# talking to one Echo, per architecture.md §5.1).
# ---------------------------------------------------------------------------
COUNTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_counter.json")


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _load_counter_state():
    try:
        with open(COUNTER_PATH, "r") as f:
            data = json.load(f)
        return {"date": data.get("date", ""), "count": int(data.get("count", 0))}
    except (IOError, OSError, ValueError, KeyError, TypeError):
        return {"date": "", "count": 0}


_counter_state = _load_counter_state()


def _save_counter_state():
    try:
        with open(COUNTER_PATH, "w") as f:
            json.dump(_counter_state, f)
    except OSError:
        app.logger.warning("Could not persist daily counter to %s", COUNTER_PATH)


def check_and_increment_quota():
    """Returns True (and increments) if under DAILY_CAP, False if exhausted.

    Resets automatically when the stored date rolls over to a new day.
    """
    today = _today_str()
    if _counter_state.get("date") != today:
        _counter_state["date"] = today
        _counter_state["count"] = 0
    if _counter_state["count"] >= DAILY_CAP:
        return False
    _counter_state["count"] += 1
    _save_counter_state()
    return True


# ---------------------------------------------------------------------------
# Alexa request signature verification (architecture.md §4.2)
# ---------------------------------------------------------------------------
class SignatureVerificationError(Exception):
    pass


ECHO_API_CERT_HOST = "s3.amazonaws.com"
ECHO_API_SAN = "echo-api.amazon.com"
REQUEST_TIMESTAMP_TOLERANCE_SECONDS = 150

# Root-of-trust anchor (docs/plans/0001, option c2). `cryptography` 2.1.4
# (ADR 0007) predates the x509.verification path-building API, so the
# system `openssl` CLI does real RFC 5280 path validation against the
# system CA bundle instead -- no library/Python upgrade needed.
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
OPENSSL_BIN = "openssl"
OPENSSL_VERIFY_TIMEOUT_SECONDS = 3

# Cached cert chains, keyed by SignatureCertChainUrl. Deliberately not
# refetched per request (architecture.md §4.2 step 2 / §5.3 latency budget).
_cert_chain_cache = {}


def _validate_cert_chain_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SignatureVerificationError("SignatureCertChainUrl must be https")
    hostname = (parsed.hostname or "").lower()
    if hostname != ECHO_API_CERT_HOST:
        raise SignatureVerificationError("SignatureCertChainUrl host must be s3.amazonaws.com")
    port = parsed.port or 443
    if port != 443:
        raise SignatureVerificationError("SignatureCertChainUrl port must be 443")
    path = parsed.path
    # Real Amazon URLs never contain a percent-encoded character or a dot
    # path segment -- reject both outright rather than trying to decode
    # and re-normalize safely (docs/plans/0001: an encoded ".." here can
    # resolve differently depending on the installed urllib3 version).
    if "%" in path:
        raise SignatureVerificationError("SignatureCertChainUrl path must not be percent-encoded")
    segments = path.split("/")
    if "." in segments or ".." in segments:
        raise SignatureVerificationError("SignatureCertChainUrl path must not contain a dot segment")
    # Normalize as a second, redundant layer, then check prefix.
    normalized_path = os.path.normpath(path)
    if not normalized_path.startswith("/echo.api/"):
        raise SignatureVerificationError("SignatureCertChainUrl path must start with /echo.api/")


def _load_pem_certificates(pem_bytes):
    certs = []
    start_marker = b"-----BEGIN CERTIFICATE-----"
    end_marker = b"-----END CERTIFICATE-----"
    idx = 0
    while True:
        start = pem_bytes.find(start_marker, idx)
        if start == -1:
            break
        end = pem_bytes.find(end_marker, start)
        if end == -1:
            break
        end += len(end_marker)
        certs.append(x509.load_pem_x509_certificate(pem_bytes[start:end], default_backend()))
        idx = end
    if not certs:
        raise SignatureVerificationError("No certificates found in chain")
    return certs


def _verify_chain_anchor(certs, ca_file=SYSTEM_CA_BUNDLE, at_time=None):
    """Anchors the chain to a real trust store via the system `openssl`
    CLI (docs/plans/0001, option c2). ca_file/at_time exist only so tests
    can point at a throwaway CA and a frozen clock -- production always
    uses the system bundle and the real current time. Fails closed on any
    non-zero exit, timeout, or missing openssl binary."""
    leaf = certs[0]
    intermediates = certs[1:]
    with tempfile.TemporaryDirectory() as tmpdir:
        leaf_path = os.path.join(tmpdir, "leaf.pem")
        with open(leaf_path, "wb") as f:
            f.write(leaf.public_bytes(serialization.Encoding.PEM))

        argv = [OPENSSL_BIN, "verify", "-no-CApath", "-CAfile", ca_file]
        if at_time is not None:
            argv += ["-attime", str(int(at_time))]
        if intermediates:
            untrusted_path = os.path.join(tmpdir, "untrusted.pem")
            with open(untrusted_path, "wb") as f:
                for cert in intermediates:
                    f.write(cert.public_bytes(serialization.Encoding.PEM))
            argv += ["-untrusted", untrusted_path]
        argv.append(leaf_path)

        try:
            result = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=OPENSSL_VERIFY_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            app.logger.warning("openssl verify could not run: %s", exc)
            raise SignatureVerificationError("Could not anchor certificate chain to a trusted root")

    stdout = result.stdout.decode("utf-8", errors="replace")
    if result.returncode != 0 or not stdout.rstrip().endswith(": OK"):
        app.logger.warning(
            "openssl verify rejected chain (exit=%s): stdout=%r stderr=%r",
            result.returncode,
            stdout,
            result.stderr.decode("utf-8", errors="replace"),
        )
        raise SignatureVerificationError("Certificate chain did not anchor to a trusted root")


def _get_cert_chain(url):
    if url in _cert_chain_cache:
        return _cert_chain_cache[url]
    resp = requests.get(
        url,
        timeout=(OPENROUTER_CONNECT_TIMEOUT, OPENROUTER_READ_TIMEOUT),
        allow_redirects=False,
    )
    if resp.status_code != 200:
        raise SignatureVerificationError(
            "SignatureCertChainUrl fetch returned status {} (redirects are not followed)".format(
                resp.status_code
            )
        )
    # Re-check the URL requests will actually put on the wire, not just
    # the one we parsed (docs/plans/0001: what's sent can differ from
    # what's parsed depending on the installed urllib3 version).
    prepared_url = requests.Request("GET", url).prepare().url
    _validate_cert_chain_url(prepared_url)

    certs = _load_pem_certificates(resp.content)

    # Public certs, never secrets -- fine to log in full on every cache miss.
    app.logger.info("Cert chain cache miss for %s", url)
    for cert in certs:
        app.logger.info(
            "  cert subject=%s issuer=%s not_after=%s sha256=%s",
            cert.subject,
            cert.issuer,
            cert.not_valid_after,
            cert.fingerprint(hashes.SHA256()).hex(),
        )

    _verify_chain_anchor(certs)
    _cert_chain_cache[url] = certs
    return certs


def _verify_chain_dates_and_san(certs):
    now = datetime.utcnow()
    for cert in certs:
        if not (cert.not_valid_before <= now <= cert.not_valid_after):
            raise SignatureVerificationError("Certificate in chain is expired or not yet valid")
    leaf = certs[0]
    try:
        san_ext = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        san_values = san_ext.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        san_values = []
    if ECHO_API_SAN not in san_values:
        raise SignatureVerificationError("Leaf certificate SAN missing echo-api.amazon.com")


def _verify_chain_signatures(certs):
    # Verifies each cert in the chain was signed by the next cert's key
    # (leaf -> intermediate -> ...), proving the file is internally
    # consistent. Root-of-trust path validation against the system CA
    # bundle happens separately, in _verify_chain_anchor (docs/plans/0001,
    # option c2), via the system `openssl` CLI -- `cryptography` 2.1.4
    # (ADR 0007) predates its own path-building API. This function stays
    # as defense in depth alongside the URL pinning, validity-date checks
    # and SAN check.
    for child, issuer in zip(certs, certs[1:]):
        try:
            issuer.public_key().verify(
                child.signature,
                child.tbs_certificate_bytes,
                padding.PKCS1v15(),
                child.signature_hash_algorithm,
            )
        except InvalidSignature:
            raise SignatureVerificationError("Certificate chain signature verification failed")


def _verify_body_signature(leaf_cert, signature_b64, raw_body):
    try:
        signature = base64.b64decode(signature_b64)
    except Exception:
        raise SignatureVerificationError("Signature header is not valid base64")
    try:
        leaf_cert.public_key().verify(
            signature,
            raw_body,
            padding.PKCS1v15(),
            hashes.SHA1(),
        )
    except InvalidSignature:
        raise SignatureVerificationError("Request body signature verification failed")


def _verify_request_timestamp(parsed_body):
    try:
        timestamp_str = parsed_body["request"]["timestamp"]
        request_time = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ")
    except (KeyError, ValueError, TypeError):
        raise SignatureVerificationError("Missing or invalid request timestamp")
    now = datetime.utcnow()
    if abs((now - request_time).total_seconds()) > REQUEST_TIMESTAMP_TOLERANCE_SECONDS:
        raise SignatureVerificationError("Request timestamp outside allowed window (possible replay)")


def verify_alexa_signature(raw_body, headers, parsed_body):
    """Raises SignatureVerificationError on any failure. Caller treats that
    as a hard reject (architecture.md §9.2)."""
    cert_chain_url = headers.get("SignatureCertChainUrl")
    signature_b64 = headers.get("Signature")
    if not cert_chain_url or not signature_b64:
        raise SignatureVerificationError("Missing SignatureCertChainUrl or Signature header")
    _validate_cert_chain_url(cert_chain_url)
    certs = _get_cert_chain(cert_chain_url)
    _verify_chain_dates_and_san(certs)
    _verify_chain_signatures(certs)
    _verify_body_signature(certs[0], signature_b64, raw_body)
    _verify_request_timestamp(parsed_body)


# ---------------------------------------------------------------------------
# LLM backends -- OpenRouter (default) or local llama.cpp (opt-in)
# ---------------------------------------------------------------------------
class OpenRouterRateLimited(Exception):
    pass


def _call_chat_completions(base_url, model, messages, api_key=None, max_tokens=OPENROUTER_MAX_TOKENS):
    """POSTs an OpenAI-compatible /chat/completions request. Generic over
    the target -- OpenRouter needs a bearer token, a local llama.cpp
    server (measure_latency.py --base-url) doesn't. max_tokens is
    overridable per-backend: OpenRouter's default (150) is too tight for
    the local server's hybrid reasoning, which shares the same budget as
    the spoken answer and was getting truncated (ADR 0012).

    `messages` is the full message list (locale-selected system prompt +
    any conversation history + current query) built by
    `conversation.build_messages_with_history` -- this function no longer
    builds messages itself (plan 0005 §3.2/§6a); callers own message
    construction so multi-turn history and locale selection don't need a
    second signature change here."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer {}".format(api_key)
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    return requests.post(
        base_url,
        headers=headers,
        json=payload,
        timeout=(OPENROUTER_CONNECT_TIMEOUT, OPENROUTER_READ_TIMEOUT),
    )


def _call_openrouter(model, messages):
    return _call_chat_completions(OPENROUTER_URL, model, messages, api_key=OPENROUTER_API_KEY)


def _call_local_llm(messages):
    return _call_chat_completions(
        LOCAL_LLM_BASE_URL, LOCAL_LLM_MODEL, messages, max_tokens=LOCAL_LLM_MAX_TOKENS
    )


def _extract_spoken_text(data):
    """Pulls the visible answer out of an OpenAI-compatible chat-completions
    response, strips any leaked reasoning block, and raises ValueError if
    nothing speakable is left -- e.g. `content` is None/empty because the
    model spent its whole token budget on mandatory reasoning
    (`finish_reason: "length"`, see the OPENROUTER_MAX_TOKENS comment
    above). The caller's generic except turns that into the graceful
    "couldn't reach my brain" fallback rather than crashing on
    `None.strip()` or speaking a bare reasoning fragment."""
    content = data["choices"][0]["message"].get("content")
    text = _strip_leaked_reasoning(content.strip() if content else content)
    if not text:
        raise ValueError(
            "LLM returned no usable content (finish_reason={!r})".format(
                data["choices"][0].get("finish_reason")
            )
        )
    return text


def ask_openrouter(messages):
    """Returns spoken text. Raises OpenRouterRateLimited if both the
    primary and (if configured) fallback model return 429; raises other
    exceptions (timeouts, HTTP errors, bad JSON shape, empty content) for
    the caller to turn into a graceful fallback -- never lets them escape
    as a 500 (architecture.md §5.3).

    `messages` is the full message list built by `ask_llm` -- this
    function is backend-agnostic and doesn't know or care whether it
    carries conversation history or which locale's system prompt it
    contains (plan 0005 §5.5)."""
    resp = _call_openrouter(OPENROUTER_MODEL, messages)
    if resp.status_code == 429 and OPENROUTER_FALLBACK_MODEL:
        resp = _call_openrouter(OPENROUTER_FALLBACK_MODEL, messages)
    if resp.status_code == 429:
        raise OpenRouterRateLimited()
    resp.raise_for_status()
    return _extract_spoken_text(resp.json())


def ask_local_llm(messages):
    """Returns spoken text from the local llama.cpp server. No fallback
    model, no 429 handling (single private server, no rate limit) --
    just a hard timeout/error like any other backend failure, caught by
    the same generic exception handling as ask_openrouter (§5.3)."""
    resp = _call_local_llm(messages)
    resp.raise_for_status()
    return _extract_spoken_text(resp.json())


def ask_llm(query, locale=DEFAULT_LOCALE, conversation_history=None):
    """Dispatches to the configured backend. Default is OpenRouter;
    INFERENCE_BACKEND=local hard-switches to the Windows PC llama.cpp
    server -- no automatic fallback between the two backends themselves
    (ADR 0013).

    This is the combined signature both plans targeted (plan 0005 §14 /
    plan 0004 §11): `locale` (docs/plans/0004) selects the system prompt
    language; `conversation_history` (docs/plans/0005) carries prior
    {role, content} turns, already trimmed by the caller
    (conversation.trim_conversation_history) -- this function does not
    re-trim.

    query: current user question (or, for AMAZON.FallbackIntent,
        the locale-appropriate FALLBACK_CONTINUATION_QUERIES entry).

    Returns: (answer_text, updated_history) -- `answer_text` is the
    cleaned, spoken text (post `_strip_leaked_reasoning`);
    `updated_history` is `conversation_history` with this turn's real
    user query and real LLM answer appended. Never call this and then
    store a canned fallback string as if it were `updated_history`'s new
    assistant turn -- if this raises, there is no `updated_history` to
    use; callers must echo the *input* history unchanged instead (plan
    0005 §2.6, §3.2)."""
    system_prompt = _conversational_system_prompt_for_locale(locale)
    messages = build_messages_with_history(
        query, conversation_history=conversation_history, system_prompt=system_prompt
    )
    if INFERENCE_BACKEND == "local":
        answer = ask_local_llm(messages)
    else:
        answer = ask_openrouter(messages)
    updated_history = append_to_history(conversation_history, query, answer)
    return answer, updated_history


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


def _respond_with_llm(query, locale, current_history):
    """Shared quota-check / ask_llm / exception-handling / response-shaping
    flow for both AskAnythingIntent (real query text) and
    AMAZON.FallbackIntent (locale-appropriate continuation query, plan
    0005 §2.5 option 3). Single implementation so the plan §2.6
    history-echo discipline (every non-ending fallback path echoes
    `current_history` back unchanged; only the success path appends and
    echoes the new turn) can't drift between the two call sites, and so
    locale-aware fallback text (plan 0004) and history echoing (plan 0005)
    can't drift from each other either."""
    echo = session_attributes_from_history(current_history)
    messages = get_messages(locale)

    if not check_and_increment_quota():
        return alexa_response(messages["QUOTA_EXHAUSTED"], end_session=True)

    try:
        speech, updated_history = ask_llm(query, locale=locale, conversation_history=current_history)
    except OpenRouterRateLimited:
        return alexa_response(
            _get_response_text("RATE_LIMIT", locale), end_session=False, session_attributes=echo
        )
    except requests.exceptions.Timeout:
        return alexa_response(messages["TIMEOUT"], end_session=False, session_attributes=echo)
    except Exception as exc:  # noqa: broad -- never let anything escape as a 500
        app.logger.exception("LLM call failed (backend=%s): %s", INFERENCE_BACKEND, exc)
        return alexa_response(messages["GENERIC_ERROR"], end_session=False, session_attributes=echo)

    if not speech:
        return alexa_response(messages["GENERIC_ERROR"], end_session=False, session_attributes=echo)

    return alexa_response(
        speech, end_session=False, session_attributes=session_attributes_from_history(updated_history)
    )


def _handle_ask_anything(intent, locale, current_history):
    slots = (intent or {}).get("slots") or {}
    query = (slots.get("query") or {}).get("value")

    if not query:
        return alexa_response(
            _get_response_text("NO_QUERY", locale),
            end_session=False,
            session_attributes=session_attributes_from_history(current_history),
        )

    return _respond_with_llm(query, locale, current_history)


def _handle_fallback_intent(locale, current_history):
    """AMAZON.FallbackIntent -- Alexa's NLU routed an utterance it
    couldn't match to any configured intent. In practice this is most
    often a bare follow-up phrase ("tell me more", "and why") that
    AskAnythingIntent's carrier-phrase-plus-slot sample utterances don't
    cover (plan 0005 §2.5, "critical design gap #1", option 3). A
    FallbackIntent request never carries the raw utterance text, so there
    is no real query to extract here -- only whatever conversation history
    is already in session.attributes.

    With no history yet, there's nothing to continue -- this can't be a
    follow-up to anything, so it gets the same "didn't catch a question"
    treatment as an AskAnythingIntent with an empty slot. With history,
    treat it as an implicit continuation request and let the LLM use the
    stored context via the locale-appropriate continuation query."""
    if not current_history:
        return alexa_response(
            _get_response_text("NO_QUERY", locale),
            end_session=False,
            session_attributes=session_attributes_from_history(current_history),
        )

    return _respond_with_llm(_fallback_continuation_query(locale), locale, current_history)


@app.route("/alexa", methods=["POST"])
def alexa():
    raw_body = request.get_data()  # raw bytes, captured before any parsing

    try:
        parsed_body = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return jsonify({"error": "invalid JSON body"}), 400

    if DEBUG_SKIP_SIGNATURE:
        app.logger.warning("DEBUG_SKIP_SIGNATURE is set -- signature verification bypassed")
    else:
        try:
            verify_alexa_signature(raw_body, request.headers, parsed_body)
        except SignatureVerificationError as exc:
            app.logger.warning("Signature verification failed: %s", exc)
            return jsonify({"error": "signature verification failed"}), 400

    try:
        application_id = parsed_body["session"]["application"]["applicationId"]
    except (KeyError, TypeError):
        application_id = None
    if application_id != ALEXA_SKILL_ID:
        app.logger.warning("applicationId mismatch: got %r", application_id)
        return jsonify({"error": "applicationId mismatch"}), 400

    # Locale extraction is defensive (never raises) and locale-agnostic
    # w.r.t. trust, so it's fine to compute before the try below -- that
    # way the last-resort except also has a locale to respond in.
    locale = _extract_locale(parsed_body)

    # Everything past this point is a trusted, well-formed Alexa request --
    # but we still never let an internal bug surface as a 500 (§5.3).
    try:
        req = parsed_body.get("request") or {}
        req_type = req.get("type")

        # Extract + trim conversation history up front (plan 0005 §2.4
        # steps 1-2) so every branch below has it available. extract_ is
        # defensive (never raises, skips malformed entries); trim_ caps it
        # at 5 turns. LaunchRequest deliberately ignores this (below) --
        # a new launch always starts fresh, regardless of what's stored.
        session_attrs = (parsed_body.get("session") or {}).get("attributes") or {}
        current_history = trim_conversation_history(extract_conversation_history(session_attrs))

        if req_type == "LaunchRequest":
            # No session_attributes passed -- deliberate (plan 0005 §2.6
            # table): a new launch starts a fresh conversation, not a
            # continuation of whatever was stored from a prior session.
            return alexa_response(_get_response_text("LAUNCH", locale), end_session=False)

        if req_type == "SessionEndedRequest":
            return "", 200

        if req_type == "IntentRequest":
            intent = req.get("intent") or {}
            intent_name = intent.get("name")
            if intent_name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
                return alexa_response(_get_response_text("GOODBYE", locale), end_session=True)
            if intent_name == "AMAZON.HelpIntent":
                return alexa_response(
                    _get_response_text("HELP", locale),
                    end_session=False,
                    session_attributes=session_attributes_from_history(current_history),
                )
            if intent_name == "AskAnythingIntent":
                return _handle_ask_anything(intent, locale, current_history)
            if intent_name == "AMAZON.FallbackIntent":
                return _handle_fallback_intent(locale, current_history)
            return alexa_response(
                get_messages(locale)["GENERIC_ERROR"],
                end_session=False,
                session_attributes=session_attributes_from_history(current_history),
            )

        return alexa_response(
            get_messages(locale)["GENERIC_ERROR"],
            end_session=False,
            session_attributes=session_attributes_from_history(current_history),
        )
    except Exception as exc:  # noqa: broad -- last-resort safety net
        app.logger.exception("Unhandled error in /alexa: %s", exc)
        return alexa_response(get_messages(locale)["GENERIC_ERROR"], end_session=False)


if __name__ == "__main__":
    from waitress import serve

    port = int(os.environ.get("PORT", "5040"))
    app.logger.info(
        "Starting talkpal-relay on 127.0.0.1:%d (DEBUG_SKIP_SIGNATURE=%s)",
        port,
        DEBUG_SKIP_SIGNATURE,
    )
    serve(app, host="127.0.0.1", port=port)
