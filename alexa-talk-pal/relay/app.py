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
"""
import base64
import json
import os
from datetime import datetime
from urllib.parse import urlparse

import requests
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv
from flask import Flask, jsonify, request

from fallback_messages import (
    GENERIC_ERROR_MESSAGE as GENERIC_ERROR_FALLBACK,
    QUOTA_EXHAUSTED_MESSAGE as QUOTA_EXHAUSTED_FALLBACK,
    TIMEOUT_MESSAGE as TIMEOUT_FALLBACK,
)

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
# two. Local needs a bigger max_tokens than OpenRouter -- its hybrid
# reasoning shares the same token budget as the spoken answer, and 150 was
# too tight (ADR 0012's follow-up testing).
INFERENCE_BACKEND = os.environ.get("INFERENCE_BACKEND", "openrouter").strip().lower()
LOCAL_LLM_BASE_URL = os.environ.get(
    "LOCAL_LLM_BASE_URL", "http://192.168.4.55:11434/v1/chat/completions"
)
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "LFM2.5-2.6B-Q4_K_M.gguf")
LOCAL_LLM_MAX_TOKENS = int(os.environ.get("LOCAL_LLM_MAX_TOKENS", "500") or "500")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_CONNECT_TIMEOUT = 2
OPENROUTER_READ_TIMEOUT = 5
OPENROUTER_MAX_TOKENS = 150

VOICE_SYSTEM_PROMPT = (
    "You are a helpful voice assistant answering a spoken question. "
    "Reply in 2 to 4 short sentences of plain spoken prose. Do not use "
    "markdown, bullet lists, code, emoji, or URLs -- your reply will be "
    "read aloud exactly as written."
)

# Timeout / quota-exhausted / generic-error lines come from
# fallback_messages.py (imported above). Rate limit isn't one of that
# module's three cases, so it stays local.
RATE_LIMIT_FALLBACK = "I'm getting a lot of questions right now. Try again in a minute?"
HELP_TEXT = "You can ask me pretty much anything -- just ask a question and I'll do my best to answer."
GOODBYE_TEXT = "Goodbye."
LAUNCH_GREETING = "Hi, what would you like to ask?"
NO_QUERY_TEXT = "Sorry, I didn't catch a question. What would you like to ask?"


# ---------------------------------------------------------------------------
# Alexa response shaping
# ---------------------------------------------------------------------------
def alexa_response(speech_text, end_session=False):
    """Shape a minimal valid Alexa Skills Kit response body."""
    return jsonify(
        {
            "version": "1.0",
            "response": {
                "outputSpeech": {"type": "PlainText", "text": speech_text},
                "shouldEndSession": end_session,
            },
        }
    )


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
    # Normalize to guard against a ../ traversal path, then check prefix.
    normalized_path = os.path.normpath(parsed.path)
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


def _get_cert_chain(url):
    if url in _cert_chain_cache:
        return _cert_chain_cache[url]
    resp = requests.get(url, timeout=(OPENROUTER_CONNECT_TIMEOUT, OPENROUTER_READ_TIMEOUT))
    resp.raise_for_status()
    certs = _load_pem_certificates(resp.content)
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
    # (leaf -> intermediate -> ...). Note: this checks internal chain
    # consistency but does not build/validate a path to a locally trusted
    # Amazon root store -- the `cryptography` version available via apt
    # (2.1.4, ADR 0007) predates its path-building APIs. Combined with the
    # SignatureCertChainUrl host/port/path pinning to Amazon's S3 bucket,
    # per-cert validity-date checks, and the SAN check, this covers the
    # practical intent of §4.2 within this library's constraints.
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


def _call_chat_completions(base_url, model, query, api_key=None, max_tokens=OPENROUTER_MAX_TOKENS):
    """POSTs an OpenAI-compatible /chat/completions request. Generic over
    the target -- OpenRouter needs a bearer token, a local llama.cpp
    server (measure_latency.py --base-url) doesn't. max_tokens is
    overridable per-backend: OpenRouter's default (150) is too tight for
    the local server's hybrid reasoning, which shares the same budget as
    the spoken answer and was getting truncated (ADR 0012)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer {}".format(api_key)
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": VOICE_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
    }
    return requests.post(
        base_url,
        headers=headers,
        json=payload,
        timeout=(OPENROUTER_CONNECT_TIMEOUT, OPENROUTER_READ_TIMEOUT),
    )


def _call_openrouter(model, query):
    return _call_chat_completions(OPENROUTER_URL, model, query, api_key=OPENROUTER_API_KEY)


def _call_local_llm(query):
    return _call_chat_completions(
        LOCAL_LLM_BASE_URL, LOCAL_LLM_MODEL, query, max_tokens=LOCAL_LLM_MAX_TOKENS
    )


def ask_openrouter(query):
    """Returns spoken text. Raises OpenRouterRateLimited if both the
    primary and (if configured) fallback model return 429; raises other
    exceptions (timeouts, HTTP errors, bad JSON shape) for the caller to
    turn into a graceful fallback -- never lets them escape as a 500
    (architecture.md §5.3)."""
    resp = _call_openrouter(OPENROUTER_MODEL, query)
    if resp.status_code == 429 and OPENROUTER_FALLBACK_MODEL:
        resp = _call_openrouter(OPENROUTER_FALLBACK_MODEL, query)
    if resp.status_code == 429:
        raise OpenRouterRateLimited()
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def ask_local_llm(query):
    """Returns spoken text from the local llama.cpp server. No fallback
    model, no 429 handling (single private server, no rate limit) --
    just a hard timeout/error like any other backend failure, caught by
    the same generic exception handling as ask_openrouter (§5.3)."""
    resp = _call_local_llm(query)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def ask_llm(query):
    """Dispatches to the configured backend. Default is OpenRouter;
    INFERENCE_BACKEND=local hard-switches to the Windows PC llama.cpp
    server -- no automatic fallback between the two backends themselves
    (ADR 0013)."""
    if INFERENCE_BACKEND == "local":
        return ask_local_llm(query)
    return ask_openrouter(query)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


def _handle_ask_anything(intent):
    slots = (intent or {}).get("slots") or {}
    query = (slots.get("query") or {}).get("value")

    if not query:
        return alexa_response(NO_QUERY_TEXT, end_session=False)

    if not check_and_increment_quota():
        return alexa_response(QUOTA_EXHAUSTED_FALLBACK, end_session=True)

    try:
        speech = ask_llm(query)
    except OpenRouterRateLimited:
        return alexa_response(RATE_LIMIT_FALLBACK, end_session=False)
    except requests.exceptions.Timeout:
        return alexa_response(TIMEOUT_FALLBACK, end_session=False)
    except Exception as exc:  # noqa: broad -- never let anything escape as a 500
        app.logger.exception("LLM call failed (backend=%s): %s", INFERENCE_BACKEND, exc)
        return alexa_response(GENERIC_ERROR_FALLBACK, end_session=False)

    if not speech:
        return alexa_response(GENERIC_ERROR_FALLBACK, end_session=False)

    return alexa_response(speech, end_session=False)


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

    # Everything past this point is a trusted, well-formed Alexa request --
    # but we still never let an internal bug surface as a 500 (§5.3).
    try:
        req = parsed_body.get("request") or {}
        req_type = req.get("type")

        if req_type == "LaunchRequest":
            return alexa_response(LAUNCH_GREETING, end_session=False)

        if req_type == "SessionEndedRequest":
            return "", 200

        if req_type == "IntentRequest":
            intent = req.get("intent") or {}
            intent_name = intent.get("name")
            if intent_name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
                return alexa_response(GOODBYE_TEXT, end_session=True)
            if intent_name == "AMAZON.HelpIntent":
                return alexa_response(HELP_TEXT, end_session=False)
            if intent_name == "AskAnythingIntent":
                return _handle_ask_anything(intent)
            return alexa_response(GENERIC_ERROR_FALLBACK, end_session=False)

        return alexa_response(GENERIC_ERROR_FALLBACK, end_session=False)
    except Exception as exc:  # noqa: broad -- last-resort safety net
        app.logger.exception("Unhandled error in /alexa: %s", exc)
        return alexa_response(GENERIC_ERROR_FALLBACK, end_session=False)


if __name__ == "__main__":
    from waitress import serve

    port = int(os.environ.get("PORT", "5040"))
    app.logger.info(
        "Starting talkpal-relay on 127.0.0.1:%d (DEBUG_SKIP_SIGNATURE=%s)",
        port,
        DEBUG_SKIP_SIGNATURE,
    )
    serve(app, host="127.0.0.1", port=port)
