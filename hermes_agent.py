"""
Hermes — Pierre's WhatsApp Business communication agent
=======================================================
Production endpoint. Zapier POSTs an incoming WhatsApp message; Hermes asks
Claude to classify + draft, validates the JSON, enforces the safety rules
server-side, and returns clean JSON Zapier can branch on.

Deployed on Render as a Web Service.
  Build command:  pip install -r requirements.txt
  Start command:  gunicorn hermes_agent:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60

Required environment variables:
  ANTHROPIC_API_KEY   your Claude API key
  WEBHOOK_SECRET      random string; Zapier sends it as header X-Webhook-Secret
  HERMES_MODEL        (optional) Claude model id; defaults below
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

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
# Update to the current Claude model you have access to (or set HERMES_MODEL).
MODEL = os.environ.get("HERMES_MODEL", "claude-sonnet-4-5")

client = anthropic.Anthropic(api_key=API_KEY)
app = Flask(__name__)

VALID_DECISIONS = {"auto-send", "manual-approval", "escalate", "quarantine"}

SYSTEM_PROMPT = """You are Hermes, Pierre's WhatsApp Business communication assistant.
Pierre is a warm, bubbly, kind French public figure (singer/actor/creator).
You analyze ONE incoming WhatsApp message and decide how to handle it.

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
        # JSONDecodeError intentionally NOT retried here; handled by caller.
    raise last_err if last_err else RuntimeError("Claude call failed")


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


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    if WEBHOOK_SECRET and not hmac.compare_digest(
        request.headers.get("X-Webhook-Secret", ""), WEBHOOK_SECRET
    ):
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(force=True, silent=True) or {}
    contact = payload.get("contact", {})
    log.info("Incoming from %s (trusted=%s)", contact.get("phone"), contact.get("trusted"))

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
    log.info("Decision=%s", result["Decision"])
    return jsonify(result), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "model": MODEL}), 200


@app.route("/", methods=["GET"])
def root():
    return jsonify({"service": "hermes", "status": "running"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
