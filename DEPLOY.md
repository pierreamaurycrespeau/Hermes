# Hermes agent — GitHub → Render deploy guide

Your live service: **https://hermes-6.onrender.com**

Files in this bundle:

| File | Purpose |
|---|---|
| `hermes_agent.py` | The agent (Flask web service). **This is the file to deploy.** |
| `requirements.txt` | Python dependencies. |
| `render.yaml` | One-click Render Blueprint config. |
| `Procfile` | Start command (fallback if you don't use the Blueprint). |
| `.env.example` | Env vars for local testing (placeholders only). |
| `ZAPIER_SETUP.md` | How Zapier calls this service and branches on the reply. |
| `README.md` | Index / overview of the whole bundle. |

---

## 1. Push to GitHub

```bash
mkdir hermes && cd hermes
# copy hermes_agent.py, requirements.txt, render.yaml, Procfile into here
git init
git add .
git commit -m "Hermes agent: initial deploy"
git branch -M main
git remote add origin https://github.com/<you>/hermes.git
git push -u origin main
```

> Do **not** commit real secrets. Only `.env.example` (placeholders) belongs in the repo.

## 2. Deploy on Render (Blueprint — easiest)

1. Render dashboard → **New +** → **Blueprint**.
2. Connect your GitHub and pick your repo.
3. Render reads `render.yaml` and creates the web service.
4. When prompted, paste your **`ANTHROPIC_API_KEY`** and your **`WEBHOOK_KEYS`**
   (both are marked `sync: false`, so Render asks you for them).
5. Deploy. When live you'll get a URL — yours is **`https://hermes-6.onrender.com`**.

**Manual alternative (no Blueprint):** New + → Web Service → connect repo →
Build: `pip install -r requirements.txt` →
Start: `gunicorn hermes_agent:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60` →
add the env vars by hand (see section 5).

## 3. Verify it's live

Open in your browser:

```
https://hermes-6.onrender.com/health
```

You should see all four fields:

```json
{"ok":true,"model":"claude-sonnet-4-5","sources":["zapier"],"trusted_sources":["zapier"]}
```

- If `sources` is **missing** → Render is running an old version; push the latest `hermes_agent.py` (see section 6).
- If `sources` is **empty `[]`** → `WEBHOOK_KEYS` isn't set correctly (see section 5).

Test the webhook route (use your real key):

```bash
curl -X POST https://hermes-6.onrender.com/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <your-zapier-key>" \
  -d '{"contact":{"name":"Maman","phone":"+33612345678","trusted":"Yes"},
       "message":{"text":"Coucou mon chéri, tu passes dimanche ?"}}'
```

You should get JSON back with `Decision` and a warm draft.

## 4. Point Zapier at it

In your Zap's **Webhooks by Zapier (POST)** step:
- URL: `https://hermes-6.onrender.com/webhook`  (or `/whatsapp` — same behavior)
- Header: `X-Webhook-Secret: <your zapier key>`
- Body: the JSON shown in `ZAPIER_SETUP.md`, Step 4.

Then branch your Zapier Paths on the returned `Decision`
(`auto-send` / `manual-approval` / `escalate` / `quarantine`).

### Webhook routes on the Hermes service

| Method & path | Purpose | Auth |
|---|---|---|
| `POST /webhook` | General inbound route for Zapier | `X-Webhook-Secret` header |
| `POST /whatsapp` | Same behavior; named alias for the WhatsApp Zap | `X-Webhook-Secret` header |
| `GET /health` | Liveness + shows configured sources | none |
| `GET /` | Simple status ping | none |

## 5. Environment variables (set these in Render → Environment)

| Key | Value | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | your Claude key (`sk-ant-...`) | required; app won't start without it |
| `WEBHOOK_KEYS` | `{"zapier":"<your-key>"}` | the whole JSON object, braces and quotes included |
| `TRUSTED_SOURCES` | `zapier` | only these sources may auto-send |
| `HERMES_MODEL` | `claude-sonnet-4-5` | change here if your account uses a different model id |

**Auth model:** callers authenticate with a key from `WEBHOOK_KEYS`
(a JSON map of `source -> secret`). The matched source name is logged for
audit. Only sources listed in `TRUSTED_SOURCES` may assert
`contact.trusted == "Yes"`; every other source is forced to `manual-approval`
and can never trigger an auto-send.

## 6. Updating the code / redeploying

Whenever `hermes_agent.py` changes, get the new version onto GitHub and Render
redeploys automatically:

```bash
git add hermes_agent.py
git commit -m "Update Hermes"
git push
```

Then watch Render → **Events** until it says **"Live."** If it doesn't
auto-deploy, use Render → top-right **"Manual Deploy" → "Deploy latest commit."**
Confirm success by reloading `/health` and checking the `sources` field.

---

## Notes

- **Model id:** `HERMES_MODEL` defaults to `claude-sonnet-4-5`. If your account
  uses a different model string, set it in Render env vars — no code change.
- **Free plan sleep:** Render's free web services sleep after ~15 min idle, so the
  first request after idle takes 30–60s and can time out Zapier. Load `/health`
  in a browser to wake it, then re-test. For always-on, use a paid instance or a
  keep-alive ping to `/health`.
- **Fail-safe:** if the AI call errors or returns bad JSON, Hermes returns
  `manual-approval` — it never auto-sends on failure.
