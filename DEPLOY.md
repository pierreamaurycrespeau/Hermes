# Hermes agent — GitHub → Render deploy

Files in this bundle:

| File | Purpose |
|---|---|
| `hermes_agent.py` | The agent (Flask web service). |
| `requirements.txt` | Python dependencies. |
| `render.yaml` | One-click Render Blueprint config. |
| `Procfile` | Start command (fallback if you don't use the Blueprint). |
| `.env.example` | Env vars for local testing. |
| `ZAPIER_SETUP.md` | How Zapier calls this service and branches on the reply. |

---

## 1. Push to GitHub

```bash
mkdir hermes-agent && cd hermes-agent
# copy hermes_agent.py, requirements.txt, render.yaml, Procfile into here
git init
git add .
git commit -m "Hermes agent: initial deploy"
git branch -M main
git remote add origin https://github.com/<you>/hermes-agent.git
git push -u origin main
```

> Do **not** commit real secrets. Only `.env.example` (placeholders) belongs in the repo.

## 2. Deploy on Render (Blueprint — easiest)

1. Render dashboard → **New +** → **Blueprint**.
2. Connect your GitHub and pick `hermes-agent`.
3. Render reads `render.yaml`, creates the web service, and generates `WEBHOOK_SECRET` for you.
4. When prompted, paste your **`ANTHROPIC_API_KEY`** (kept secret).
5. Deploy. When live you'll get a URL like `https://hermes-agent.onrender.com`.

**Manual alternative (no Blueprint):** New + → Web Service → connect repo →
Build: `pip install -r requirements.txt` →
Start: `gunicorn hermes_agent:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60` →
add the three env vars by hand.

## 3. Verify

```bash
curl https://hermes-agent.onrender.com/health
# {"ok": true, "model": "claude-sonnet-4-5"}
```

Test the main route (use your real WEBHOOK_SECRET):

```bash
curl -X POST https://hermes-agent.onrender.com/whatsapp \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <your-secret>" \
  -d '{"contact":{"name":"Maman","phone":"+33612345678","trusted":"Yes"},
       "message":{"text":"Coucou mon chéri, tu passes dimanche ?"}}'
```

You should get back JSON with `Decision: "auto-send"` and a warm draft.

## 4. Point Zapier at it

In your Zap's **Webhooks by Zapier (POST)** step:
- URL: `https://hermes-agent.onrender.com/whatsapp`
- Header: `X-Webhook-Secret: <your-secret>`
- Body: the JSON shown in `ZAPIER_SETUP.md`, Step 4.

Then branch your Zapier Paths on the returned `Decision`
(`auto-send` / `manual-approval` / `escalate` / `quarantine`).

---

## Notes

- **Model id:** `HERMES_MODEL` defaults to `claude-sonnet-4-5`. If your account
  uses a different model string, set it in Render env vars — no code change.
- **Free plan sleep:** Render's free web services sleep after inactivity, so the
  first request after idle is slow. For always-on, use a paid instance or a
  keep-alive ping to `/health`.
- **Fail-safe:** if the AI call errors or returns bad JSON, Hermes returns
  `manual-approval` — it never auto-sends on failure.
