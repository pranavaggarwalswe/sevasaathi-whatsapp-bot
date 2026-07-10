# SevaSaathi WhatsApp Booking Bot 🏠

A WhatsApp chatbot that takes home-cleaning service bookings end-to-end — service
selection, address, scheduling, and UPI payment — built on the **Meta WhatsApp
Cloud API** for [SevaSaathi](https://sevasaathi.co.in), a home services platform
for tier 2 & 3 cities in India.

## How it works

```
Customer on WhatsApp ──► Meta Cloud API ──► webhook ──► Flask bot ──► reply
                                                          │
                                                     SQLite (orders)
                                                          │
                                              owner gets order alert on WhatsApp
```

- **Interactive flow**: button + list messages guide the customer:
  welcome → category → service → address → date → time slot → UPI payment link → confirmation
- **Session state machine** per phone number, persisted in SQLite
- **Order dispatch**: every confirmed booking is instantly forwarded to the
  owner's WhatsApp with full details
- **UPI payments**: sends a hosted payment page link that deep-links into the
  customer's UPI app with amount + order note pre-filled
- **Bilingual UX**: Hinglish prompts for tier 2/3 India

## Stack

- Python / Flask webhook server
- Meta WhatsApp Cloud API (free tier — replies to customers cost ₹0)
- SQLite for sessions + orders
- Deployed on Render (gunicorn)

## Setup

See [SETUP.md](SETUP.md) for the full Meta + deployment walkthrough.

```
pip install -r requirements.txt
# set env vars (see .env.example)
python bot.py          # local
gunicorn bot:app       # production
```

Configuration is entirely via environment variables (`.env.example`) — no
secrets in code.
