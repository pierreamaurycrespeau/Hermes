# WhatsApp → AI → Auto-send / Draft-approval workflow

This guide wires up the flow you described:
**WhatsApp message → check trusted-numbers sheet → ask the assistant → auto-send OR send you a draft to approve/reject.**

---

## 0. What each piece does

| Piece | Role |
|---|---|
| **WhatsApp Business Cloud** (Meta) or **Twilio WhatsApp** | Receives/sends messages. Zapier has native apps for both. |
| **Google Sheet: `Trusted Numbers`** | The allow-list for full automation. One row per phone number. |
| **`whatsapp_assistant.py`** (this repo) | Your AI endpoint. Zapier calls it; it returns the Decision JSON. |
| **Zapier** | The glue: trigger, sheet lookup, webhook call, branching, sending. |
| **Slack or Telegram** | Where drafts land with Approve / Reject buttons. |
| **Airtable (recommended)** | Stores pending drafts so "Reject → regenerate" has state to work with. |

> Key principle: the sheet only sets `Trusted = Yes/No`. **The assistant returns the Decision, and Zapier branches on the Decision — not on the sheet.** This keeps the safety escalation path impossible to bypass.

---

## 1. Deploy the AI endpoint

1. Deploy `whatsapp_assistant.py` to any host (Render / Railway / Fly.io / Cloud Run / Lambda).
2. Set env vars: `ANTHROPIC_API_KEY` and `WEBHOOK_SECRET` (a random string).
3. Note the public URL, e.g. `https://your-app.onrender.com/whatsapp`.
4. Test: `GET /health` should return `{"ok": true}`.

---

## 2. Google Sheet: `Trusted Numbers`

| phone | name | trusted |
|---|---|---|
| +33612345678 | Maman | Yes |
| +33698765432 | Manager Léa | Yes |

Store numbers in the same format WhatsApp gives you (E.164, e.g. `+33...`).

---

## 3. Build the Zap

**Step 1 — Trigger:** `WhatsApp Business Cloud` → *New Message*.
(Or Twilio → *New Inbound WhatsApp Message*.)

**Step 2 — Lookup:** `Google Sheets` → *Lookup Spreadsheet Row*.
- Worksheet: `Trusted Numbers`
- Lookup column: `phone`  → value: the sender's number from Step 1.
- Turn ON "create row if it doesn't exist" = **No**.
- If no row is found, the `trusted` field comes back empty → treat as `No`.

**Step 3 — Formatter (optional):** map the lookup result to `Yes`/`No`
so an empty result becomes `No`.

**Step 4 — Webhooks by Zapier:** *POST*
- URL: your endpoint `https://.../whatsapp`
- Header: `X-Webhook-Secret: <your WEBHOOK_SECRET>`
- Payload type: JSON
- Data:
```json
{
  "contact": {
    "name": "{{Step2.name}}",
    "phone": "{{Step1.from}}",
    "trusted": "{{Step3.trusted}}"
  },
  "message": { "text": "{{Step1.text}}", "message_id": "{{Step1.id}}" }
}
```
The response gives you `Decision`, `Response`, `Reasoning`, `Alert`.

**Step 5 — Paths (branch on `Decision`):**

- **Path A — `auto-send`:**
  `WhatsApp` → *Send Message* to `{{Step1.from}}` with text `{{Step4.Response}}`.

- **Path B — `manual-approval`:**
  Send the draft to yourself for approval (see Section 4).

- **Path C — `escalate`:**
  URGENT notify you — e.g. `SMS by Zapier` / push / a call trigger — with
  `{{Step4.Alert}}`. **Never** auto-send on this path. This fires even when
  `trusted = Yes`.

- **Path D — `quarantine`:**
  `Google Sheets` → *Create Row* in a `Quarantine` tab with the message +
  `{{Step4.Reasoning}}`. Send no reply.

---

## 4. The Approve / Reject / Regenerate loop

Zaps are linear, so the cleanest approve→send / reject→regenerate loop uses
an interactive chat tool plus a tiny bit of stored state.

**Recommended: Slack (or Telegram) buttons + Airtable for state.**

1. On `manual-approval`, write a row to Airtable `Pending Drafts`:
   `message_id, from, incoming_text, draft, status=pending`.
2. Post the draft to Slack with two buttons: **Approve** / **Reject**.
   (Use Slack's *interactive messages*, or Zapier's Slack "Send Channel
   Message" with buttons linking back to a second Zap.)
3. **Approve** → second Zap: send `draft` via WhatsApp, set `status=sent`.
4. **Reject** → second Zap: call the AI endpoint again, this time adding a
   `revision_note` (why you rejected) + the previous draft in `history`, so
   the assistant produces a fresh version. Update the Airtable row and
   re-post the new draft.

> Simpler no-Airtable version: reply to the Slack/Telegram message with
> `ok` to send, or with new instructions to regenerate. A Zap watches for
> your reply and acts on it. Airtable just makes multi-round revisions and
> auditing much cleaner.

---

## 5. Prompt changes needed (already reflected in the code)

For the workflow to run, the assistant needs a defined **input contract**.
The system prompt in `whatsapp_assistant.py` now expects this JSON in:

```json
{
  "contact": { "name": "...", "phone": "+33...", "trusted": "Yes|No" },
  "message": { "text": "...", "message_id": "..." },
  "history": [ { "from": "contact|pierre", "text": "...", "ts": "..." } ],
  "calendar_context": "optional free text, e.g. 'On tour in Lyon until Fri'"
}
```

and returns exactly:

```json
{ "Decision": "...", "Response": "...", "Reasoning": "...", "Alert": null }
```

Two enhancements worth adding as you grow:
- **history**: pass the last few WhatsApp turns so replies have context.
- **calendar_context**: pass a one-line summary of Pierre's schedule so
  drafts are time-aware ("I'm on tour till Friday — can we do next week?").

---

## 6. Safety guarantees baked in

- `escalate` and `quarantine` **always** return an empty `Response` — nothing
  can be sent on those paths.
- A trusted number can never force a send if the message trips a
  safety/legal/emergency/age flag; the assistant returns `escalate`.
- If the AI call fails or returns non-JSON, the endpoint fails **safe** →
  `manual-approval`, never a silent auto-send.
- Quarantine writes to a sheet you can always review; nothing is deleted.
