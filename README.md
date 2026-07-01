# Hermes — Pierre's WhatsApp communication agent

Hermes is a small web service that sits between Zapier and Claude. When a
WhatsApp message arrives, Zapier sends it to Hermes; Hermes decides whether to
**auto-send** a reply, request **manual approval**, **escalate** a safety/legal/
emergency concern, or **quarantine** likely spam/scams — and returns clean JSON
that Zapier branches on.

Live service: **https://hermes-6.onrender.com**

## The flow

```
WhatsApp message
      │
      ▼
Zapier trigger  →  Google Sheets lookup (Trusted? Yes/No)
      │
      ▼
POST to https://hermes-6.onrender.com/webhook   (X-Webhook-Secret header)
      │
      ▼
Hermes → Claude → JSON {Decision, Response, Reasoning, Alert}
      │
      ▼
Zapier Paths branch on Decision:
   ├─ auto-send       → send WhatsApp reply
   ├─ manual-approval → send YOU a draft to approve/reject
   ├─ escalate        → URGENT ping to you (never auto-sends)
   └─ quarantine      → log aside, no reply
```

## Files in this bundle

| File | What it is |
|---|---|
| `hermes_agent.py` | **The agent.** Deploy this file to Render. |
| `requirements.txt` | Python dependencies (Flask, Anthropic SDK, gunicorn). |
| `render.yaml` | Render Blueprint config for one-click deploy. |
| `Procfile` | Start command (fallback if not using the Blueprint). |
| `.env.example` | Environment-variable template for local testing. |
| `DEPLOY.md` | Step-by-step GitHub → Render deploy + troubleshooting. |
| `ZAPIER_SETUP.md` | How to wire up the full Zapier workflow. |
| `README.md` | This overview. |

## Start here

1. **Deploying?** → open `DEPLOY.md`.
2. **Wiring up Zapier?** → open `ZAPIER_SETUP.md`.

## Environment variables

| Key | Example | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Required. App won't start without it. |
| `WEBHOOK_KEYS` | `{"zapier":"<key>"}` | Source→secret map. Paste the whole JSON. |
| `TRUSTED_SOURCES` | `zapier` | Only these sources may auto-send. |
| `HERMES_MODEL` | `claude-sonnet-4-5` | Override if your account uses a different id. |

## Built-in protections

- **Escalate/quarantine never send** — those paths always return an empty reply.
- **Trusted ≠ bypass** — a safety/legal/age/emergency flag escalates even for a
  trusted contact.
- **Source-gated trust** — only `TRUSTED_SOURCES` can mark a contact trusted; any
  other caller is forced to manual-approval.
- **Fail-safe** — API errors or bad JSON fall back to `manual-approval`, never a
  silent auto-send.
- **Keyed, revocable auth** — each source has its own webhook key.
