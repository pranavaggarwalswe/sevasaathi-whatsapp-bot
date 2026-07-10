# SevaSaathi WhatsApp Bot — Setup Guide

This bot uses **Meta's official WhatsApp Cloud API**. It is NOT the normal WhatsApp
app or the WhatsApp Business app — it's a third thing (see the table below).

## Which WhatsApp do I need?

| | Personal WhatsApp | WhatsApp Business app | WhatsApp Cloud API (this bot) |
|---|---|---|---|
| What it is | Green app on your phone | Free "WA Business" app | Server-to-server API from Meta |
| Can run this bot? | ❌ Never (bots are banned) | ❌ No API access | ✅ Yes |
| Cost | Free | Free | Free for replies (see Pricing) |
| Number requirement | Your SIM | Any SIM | A number NOT already on WhatsApp |

**Bottom line: you don't need a "professional WhatsApp account" that exists today —
you create a Cloud API number through Meta's developer site. Best practice: buy a
new SIM for the business number. Your personal WhatsApp stays untouched (it's only
used as ADMIN_NUMBER to receive order alerts).**

## Step-by-step

### 1. Meta accounts (all free)
1. A normal Facebook account.
2. Go to https://business.facebook.com → create a **Business Portfolio** ("SevaSaathi").
3. Go to https://developers.facebook.com → **Get Started** → create a developer account.

### 2. Create the app
1. developers.facebook.com → **My Apps** → **Create App** → type **Business**.
2. Name it (e.g., "SevaSaathi Bot"), link it to your Business Portfolio.
3. On the app dashboard, find **WhatsApp** → **Set up**.

### 3. Free test number (start here — works in 5 minutes)
- Under **WhatsApp → API Setup**, Meta gives you a **free test phone number**.
- Copy the **Phone number ID** (a long number, NOT the phone number) → this is `PHONE_NUMBER_ID`.
- Copy the **temporary access token** (valid 24h) → this is `WHATSAPP_TOKEN` for testing.
- Add up to 5 recipient numbers (your own personal WhatsApp) in "To" → verify with OTP.
- You can now test the whole bot against your own phone before spending anything.

### 4. Run the bot locally
```
cd whatsapp-bot
pip install -r requirements.txt
set WHATSAPP_TOKEN=...        (PowerShell: $env:WHATSAPP_TOKEN="...")
set PHONE_NUMBER_ID=...
set VERIFY_TOKEN=my_secret_verify_token_123
set ADMIN_NUMBER=91XXXXXXXXXX
python bot.py
```
Then expose it to the internet for the webhook (Meta must reach it over HTTPS):
```
ngrok http 5000
```
(ngrok.com free account; it prints a URL like https://abc123.ngrok-free.app)

### 5. Connect the webhook
1. App dashboard → **WhatsApp → Configuration** → **Webhook** → Edit.
2. Callback URL: `https://YOUR-URL/webhook` (ngrok URL for testing, hosting URL in production).
3. Verify token: exactly what you set as `VERIFY_TOKEN`.
4. Click **Verify and save** (the bot's GET /webhook answers Meta's challenge).
5. Under **Webhook fields**, subscribe to **messages**.
6. Send "hi" from your verified personal WhatsApp to the test number → the bot should reply.

### 6. Go to production
1. **Permanent token** (the 24h token will expire):
   business.facebook.com → Business Settings → Users → **System Users** → Add →
   create an admin system user → **Generate Token** → select your app →
   enable `whatsapp_business_messaging` + `whatsapp_business_management` →
   copy the token → this is your permanent `WHATSAPP_TOKEN`.
2. **Real phone number**: WhatsApp → API Setup → **Add phone number**.
   - The number must NOT have an active WhatsApp account. If it does, open
     that WhatsApp app → Settings → Account → Delete account first.
   - Verify by SMS/call. Set the display name ("SevaSaathi") — Meta reviews it (~1-2 days).
3. **Host the bot** (free options): Render.com / Railway.app / PythonAnywhere.
   For Render: New Web Service → connect repo or upload → build `pip install -r requirements.txt`
   → start command `gunicorn bot:app` → add the env vars in the dashboard.
   Update the webhook URL to the hosting URL.
4. **Website**: put the number in `project pronoto/js/main.js` → `WHATSAPP_NUMBER = '91XXXXXXXXXX'`.

### Pricing (as of 2025)
- **Replying to customers is free**: "service conversations" (customer messages you,
  you reply within 24 hours) cost ₹0, unlimited. This bot only ever replies, so
  normal operation costs nothing.
- Business-initiated **template messages** (e.g., marketing blasts, reminders sent
  after the 24h window) are paid per message — the bot doesn't use any.
- Unverified businesses can message up to 250 *new* customers/day business-initiated;
  replies to incoming messages are not limited. Complete Business Verification
  (documents in Business Settings) later to lift limits and get a green checkmark
  eligibility.

### Notes
- Order data is stored in `bot.db` (SQLite) next to bot.py. On free hosts like Render
  the disk is wiped on redeploy — you still get every order as a WhatsApp alert on
  ADMIN_NUMBER, so nothing is lost operationally. Move to a hosted DB later if needed.
- The bot's service prices (CATALOG in bot.py) currently differ from the website's
  prices — edit one of them so they match.
