"""
Hermes — Pierre's WhatsApp Business communication agent
=======================================================
Production endpoint. Authorized sources (Zapier, and now Xavier via his own
webhook key) POST an incoming message; Hermes asks Claude to classify + draft,
validates the JSON, enforces the safety rules server-side, and returns clean
JSON the caller can branch on.

Deployed on Render as a Web Service.
  Build command:  pip install -r requirements.txt
  Start command:  gunicorn hermes_agent:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60

Environment variables:
  ANTHROPIC_API_KEY      (required) your Claude API key
  WEBHOOK_KEYS           (recommended) JSON of source->secret, e.g.
                         {"zapier":"long-random-1","xavier":"long-random-2"}
  WEBHOOK_SECRET         (fallback) single shared secret, labeled "default",
                         used only if WEBHOOK_KEYS is not set
  TRUSTED_SOURCES        (optional) comma-list of sources allowed to assert
                         contact.trusted == "Yes"; default "zapier,default".
                         Any source NOT in this list is forced to manual-approval.
  ALLOW_UNAUTHENTICATED  (optional) "true" to allow unauthenticated POSTs
                         (LOCAL DEV ONLY — never set this in production)
  HERMES_MODEL           (optional) Claude model id; default below

Callers authenticate with the header:  X-Webhook-Secret: <their key>
"""

import os
import json
import time
import hmac
import logging

from flask import Flask, request, jsonify
import anthropic

# --------------------------------------------------------------------------- #
# Config & setup
# --------------------------------------------------------------------------- #
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hermes")

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY is not set")

# Update to the current Claude model you have access to (or set HERMES_MODEL).
MODEL = os.environ.get("HERMES_MODEL", "claude-sonnet-4-5")

ALLOW_UNAUTHENTICATED = os.environ.get("ALLOW_UNAUTHENTICATED", "").lower() == "true"

# Sources permitted to assert a contact is trusted (i.e. eligible for auto-send).
# Every other source is downgraded to manual-approval so it can never auto-send.
TRUSTED_SOURCES = {
    s.strip() for s in os.environ.get("TRUSTED_SOURCES", "zapier,default").split(",") if s.strip()
}

client = anthropic.Anthropic(api_key=API_KEY)
app = Flask(__name__)

VALID_DECISIONS = {"auto-send", "manual-approval", "escalate", "quarantine"}


def _load_webhook_keys() -> dict:
    """Build a {source_name: secret} map from env."""
    raw = os.environ.get("WEBHOOK_KEYS")
    if raw:
        try:
            keys = json.loads(raw)
            if isinstance(keys, dict) and keys:
                return {str(k): str(v) for k, v in keys.items()}
            log.error("WEBHOOK_KEYS must be a non-empty JSON object; ignoring.")
        except json.JSONDecodeError:
            log.error("WEBHOOK_KEYS is not valid JSON; ignoring.")
    single = os.environ.get("WEBHOOK_SECRET")
    if single:
        return {"default": single}
    return {}


WEBHOOK_KEYS = _load_webhook_keys()

SYSTEM_PROMPT = """You are Hermes, Pierre's WhatsApp Business communication assistant.
Pierre is a warm, bubbly, kind French public figure (singer/actor/creator).
You analyze ONE incoming message and decide how to handle it.

INPUT: a JSON object with
  contact.name, contact.phone, contact.trusted ("Yes"/"No"),
  message.text, and optional history[] (prior turns) and calendar_context.

DECISION RULES (priority order):
1. SAFETY / LEGAL / EMERGENCY / AGE OVERRIDE — If the message involves any
   safeguarding or child-safety concern, anything age-inappropriate, a legal
   notice/threat/compliance matter, a credible emergency, or a serious threat
   to Pierre's health, safety, or reputation -> Decision = "escalate".
   Response MUST be empty. Put an urgent, specific note in Alert.
   This OVERRIDES trusted status — trusted contacts are NOT exempt.
2. SCAM / SPAM / BAD-FAITH — likely fraud, phishing, leverage-seeking, or
   spam -> Decision = "quarantine". Response empty. Explain in Reasoning.
3. TRUSTED AUTO-SEND — if contact.trusted == "Yes" and none of the above ->
   Decision = "auto-send". Write a helpful reply in Pierre's warm voice.
4. OTHERWISE -> Decision = "manual-approval". Draft a reply in Response for
   Pierre to review; he will approve it or ask for a revision.

If input contains revision_note, treat it as Pierre's feedback on the previous
draft (also supplied) and produce an improved manual-approval draft.

VOICE: warm, genuine, kind, a little playful — never robotic. Match the
relationship (family/friend/colleague). Never write anything that could be
misquoted or damage Pierre's reputation. Use calendar_context to stay
time-aware when relevant.

OUTPUT: respond with ONLY valid JSON, no prose, EXACTLY this schema:
{"Decision":"auto-send|manual-approval|escalate|quarantine",
 "Response":"reply text, or empty string if escalate/quarantine",
 "Reasoning":"brief explanation",
 "Alert":"null, or an urgent note to Pierre if a safety/legal/emergency flag fired"}

Auto-send Response text must be under 160 characters."""


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def authenticate(req) -> "str | None":
    """Return the source name for a valid key, else None."""
    presented = req.headers.get("X-Webhook-Secret", "")
    if not WEBHOOK_KEYS:
        if ALLOW_UNAUTHENTICATED:
            log.warning("No webhook keys configured; allowing request (DEV MODE).")
            return "dev"
        log.error("No webhook keys configured; rejecting request.")
        return None
    for name, secret in WEBHOOK_KEYS.items():
        if secret and hmac.compare_digest(presented, secret):
            return name
    return None


# --------------------------------------------------------------------------- #
# Core helpers
# --------------------------------------------------------------------------- #
def call_claude(payload: dict, max_retries: int = 2) -> dict:
    """Call Claude and parse strict JSON. Retries transient API errors."""
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(payload)}],
            )
            raw = msg.content[0].text.strip()
            if raw.startswith("```"):  # strip accidental code fences
                raw = raw.strip("`")
                raw = raw[raw.find("{"):]
            return json.loads(raw)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            last_err = e
            log.warning("Claude API error (attempt %s): %s", attempt + 1, e)
            time.sleep(1.5 * (attempt + 1))
    raise last_err if last_err else RuntimeError("Claude call failed")


def apply_source_policy(payload: dict, source: str) -> dict:
    """Only trusted sources may assert contact.trusted == 'Yes'.
    Any other source is forced to trusted='No' so it can never auto-send."""
    if source not in TRUSTED_SOURCES:
        contact = payload.setdefault("contact", {})
        if str(contact.get("trusted", "")).strip().lower() == "yes":
            log.warning("Source '%s' tried to assert trusted=Yes; downgraded.", source)
        contact["trusted"] = "No"
    return payload


def enforce_safety(result: dict) -> dict:
    """Server-side guardrails so no bug can leak an unsafe send."""
    decision = result.get("Decision")

    if decision not in VALID_DECISIONS:
        return {
            "Decision": "manual-approval",
            "Response": "",
            "Reasoning": "Unrecognized decision from model; defaulting to manual review.",
            "Alert": "Classifier returned an unexpected Decision — please review manually.",
        }

    # escalate / quarantine can never carry an outgoing message.
    if decision in ("escalate", "quarantine"):
        result["Response"] = ""

    # auto-send / manual-approval must never ship an empty message.
    if decision in ("auto-send", "manual-approval") and not (result.get("Response") or "").strip():
        result["Decision"] = "manual-approval"
        result["Alert"] = result.get("Alert") or "Empty draft produced — routed to manual approval."

    result.setdefault("Reasoning", "")
    result.setdefault("Alert", None)
    return result


def fail_safe(reason: str, alert: str) -> dict:
    """Any failure defaults to manual review — never a silent auto-send."""
    return {
        "Decision": "manual-approval",
        "Response": "",
        "Reasoning": reason,
        "Alert": alert,
    }


def process_inbound(source: str):
    """Shared pipeline for every inbound webhook, whatever the source."""
    payload = request.get_json(force=True, silent=True) or {}
    payload = apply_source_policy(payload, source)

    contact = payload.get("contact", {})
    log.info(
        "Inbound source=%s from=%s trusted=%s",
        source, contact.get("phone"), contact.get("trusted"),
    )

    try:
        result = call_claude(payload)
    except json.JSONDecodeError:
        result = fail_safe(
            "Could not parse model output as JSON.",
            "Classifier output was not valid JSON — review manually.",
        )
    except Exception as e:  # noqa: BLE001 — fail safe on anything unexpected
        log.exception("Hermes error")
        result = fail_safe(
            f"Assistant error: {type(e).__name__}",
            "Assistant failed to run — message needs manual handling.",
        )

    result = enforce_safety(result)
    result["_source"] = source  # echo back which channel was used (audit)
    log.info("source=%s Decision=%s", source, result["Decision"])
    return jsonify(result), 200


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/webhook", methods=["POST"])
def webhook():
    """General-purpose inbound webhook (e.g. Xavier's channel)."""
    source = authenticate(request)
    if source is None:
        return jsonify({"error": "unauthorized"}), 401
    return process_inbound(source)


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    """Dedicated route for the Zapier WhatsApp pipeline."""
    source = authenticate(request)
    if source is None:
        return jsonify({"error": "unauthorized"}), 401
    return process_inbound(source)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "model": MODEL,
        "sources": sorted(WEBHOOK_KEYS.keys()),
        "trusted_sources": sorted(TRUSTED_SOURCES),
    }), 200


@app.route("/", methods=["GET"])
def root():
    return jsonify({"service": "hermes", "status": "running"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
