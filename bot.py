"""
SevaSaathi — WhatsApp Home Services Booking Bot
Built on Meta WhatsApp Cloud API (free tier)

Flow: Welcome -> Category menu -> Service menu -> Address -> Date -> Slot -> Confirm -> Dispatch
"""

import os
import json
import sqlite3
import requests
from datetime import datetime, timedelta
from urllib.parse import quote
from flask import Flask, request

app = Flask(__name__)

# ---------------- CONFIG (set these as environment variables) ----------------
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_secret_verify_token_123")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")          # Meta permanent access token
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")        # from Meta dashboard
ADMIN_NUMBER = os.environ.get("ADMIN_NUMBER", "")              # your personal WhatsApp e.g. 9198XXXXXXXX
BUSINESS_NAME = os.environ.get("BUSINESS_NAME", "SevaSaathi")
UPI_ID = os.environ.get("UPI_ID", "yourname@upi")

API_URL = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
HEADERS = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

# ---------------- SERVICE CATALOG (edit prices here) ----------------
# NOTE: the first 8 services mirror the website (project pronoto/index.html).
# If you change a price here, change it on the website too (service cards +
# the JSON-LD block in <head>), and vice versa.
CATALOG = {
    "cat_cleaning": {
        "title": "Cleaning services",
        "services": {
            "svc_mopping":   {"name": "Floor Sweeping & Mopping", "price": 99},
            "svc_bathroom":  {"name": "Bathroom Deep Clean",      "price": 99},
            "svc_kitchen":   {"name": "Kitchen Cleaning",         "price": 99},
            "svc_deepclean": {"name": "Full House Deep Clean",    "price": 499},
            "svc_sofa":      {"name": "Carpet & Sofa Cleaning",   "price": 99},
        },
    },
    "cat_laundry": {
        "title": "Laundry & wardrobe",
        "services": {
            "svc_laundry":   {"name": "Laundry & Ironing",      "price": 99},
            "svc_wardrobe":  {"name": "Wardrobe Cleaning",      "price": 99},
        },
    },
    "cat_kitchen": {
        "title": "Kitchen & utensils",
        "services": {
            "svc_utensils":  {"name": "Utensil Washing",        "price": 99},
            "svc_kprep":     {"name": "Kitchen Prep",           "price": 99},
            "svc_cabinet":   {"name": "Kitchen Cabinet Clean",  "price": 99},
        },
    },
    "cat_packing": {
        "title": "Packing & shifting",
        "services": {
            "svc_packing":   {"name": "Packing / Unpacking",    "price": 99},
        },
    },
    "cat_party": {
        "title": "Party ready",
        "services": {
            "svc_preparty":  {"name": "Pre-Party Express Clean",  "price": 99},
            "svc_afterparty":{"name": "After-Party Express Clean","price": 99},
        },
    },
    "cat_extras": {
        "title": "Extras",
        "services": {
            "svc_window":    {"name": "Window Cleaning",        "price": 99},
            "svc_fan":       {"name": "Fan Cleaning",           "price": 79},
            "svc_dusting":   {"name": "Dusting & Wiping",       "price": 99},
            "svc_balcony":   {"name": "Balcony Cleaning",       "price": 99},
            "svc_fridge":    {"name": "Fridge Cleaning",        "price": 99},
            "svc_plant":     {"name": "Plant Care",             "price": 99},
            "svc_car":       {"name": "Car Surface Cleaning",   "price": 99},
        },
    },
}

SLOTS = {
    "slot_morning":   "Subah (9 AM - 12 PM)",
    "slot_afternoon": "Dopeher (12 PM - 3 PM)",
    "slot_evening":   "Shaam (3 PM - 6 PM)",
}

# ---------------- DATABASE (session state + orders) ----------------
# Absolute path so the DB lands next to bot.py no matter where the bot
# is launched from (a relative path would scatter bot.db files around).
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS sessions (
            phone TEXT PRIMARY KEY,
            state TEXT DEFAULT 'start',
            category TEXT, service TEXT,
            address TEXT, date TEXT, slot TEXT,
            payment TEXT,
            worker_name TEXT, worker_area TEXT,
            updated_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT, service TEXT, price INTEGER,
            address TEXT, date TEXT, slot TEXT,
            payment TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT
        )""")
        # People applying to work with us — bot collects the lead, admin follows up.
        c.execute("""CREATE TABLE IF NOT EXISTS worker_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT, name TEXT, area TEXT, skills TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT
        )""")
        # WhatsApp message ids we've already handled, so a retried webhook
        # delivery doesn't trigger a second reply (see already_processed()).
        c.execute("""CREATE TABLE IF NOT EXISTS processed_messages (
            id TEXT PRIMARY KEY,
            created_at TEXT
        )""")
        # migrate older bot.db files that predate some columns
        for table in ("sessions", "orders"):
            cols = [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]
            if "payment" not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN payment TEXT")
        scols = [r["name"] for r in c.execute("PRAGMA table_info(sessions)")]
        for col in ("worker_name", "worker_area"):
            if col not in scols:
                c.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT")

def get_session(phone):
    with db() as c:
        row = c.execute("SELECT * FROM sessions WHERE phone=?", (phone,)).fetchone()
        if not row:
            c.execute("INSERT INTO sessions (phone, state, updated_at) VALUES (?, 'start', ?)",
                      (phone, datetime.now().isoformat()))
            return {"phone": phone, "state": "start"}
        return dict(row)

def update_session(phone, **kwargs):
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    with db() as c:
        c.execute(f"UPDATE sessions SET {sets} WHERE phone=?", (*kwargs.values(), phone))

def reset_session(phone):
    update_session(phone, state="start", category=None, service=None,
                   address=None, date=None, slot=None, payment=None,
                   worker_name=None, worker_area=None)

def already_processed(message_id):
    """WhatsApp re-delivers a webhook if we don't ACK with 200 fast enough, so the
    same message can arrive 2-3 times — that is what made the bot reply twice or
    thrice. We record each message id; the first delivery inserts it and is handled,
    any retry hits the PRIMARY KEY and is skipped."""
    if not message_id:
        return False
    try:
        with db() as c:
            c.execute("INSERT INTO processed_messages (id, created_at) VALUES (?, ?)",
                      (message_id, datetime.now().isoformat()))
            # keep the table small — retries only happen within a few hours
            cutoff = (datetime.now() - timedelta(days=1)).isoformat()
            c.execute("DELETE FROM processed_messages WHERE created_at < ?", (cutoff,))
        return False    # inserted just now -> first time we've seen this message
    except sqlite3.IntegrityError:
        return True     # already recorded -> retried/duplicate delivery
    except Exception as e:
        # Fail open: a rare duplicate is better than silently dropping a message.
        print("DEDUP ERROR:", e)
        return False

# ---------------- WHATSAPP SEND HELPERS ----------------
def send(payload):
    payload["messaging_product"] = "whatsapp"
    r = requests.post(API_URL, headers=HEADERS, json=payload, timeout=15)
    if r.status_code != 200:
        print("SEND ERROR:", r.status_code, r.text)
    return r

def send_text(to, text):
    send({"to": to, "type": "text", "text": {"body": text}})

def send_list(to, header, body, button_label, section_title, rows):
    """rows = [{"id": ..., "title": ..., "description": ...}] max 10"""
    send({
        "to": to, "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header[:60]},
            "body": {"text": body[:1024]},
            "action": {
                "button": button_label[:20],
                "sections": [{"title": section_title[:24], "rows": rows[:10]}],
            },
        },
    })

def send_buttons(to, body, buttons):
    """buttons = [{"id": ..., "title": ...}] max 3"""
    send({
        "to": to, "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
                for b in buttons[:3]
            ]},
        },
    })

# ---------------- BOT FLOW STEPS ----------------
def show_welcome(phone):
    send_buttons(phone,
        f"Namaste! 🙏 {BUSINESS_NAME} mein aapka swagat hai.\n\n"
        "Hum ghar ki cleaning services provide karte hain — verified professionals, "
        "fixed pricing, aapke time pe.",
        [{"id": "show_menu", "title": "Services dekhein"},
         {"id": "join_worker", "title": "Kaam karna hai"}])
    update_session(phone, state="welcome_sent")

def show_categories(phone):
    rows = [{"id": cid, "title": cat["title"][:24],
             "description": ", ".join(s["name"] for s in cat["services"].values())[:72]}
            for cid, cat in CATALOG.items()]
    send_list(phone, "Kaunsi service chahiye?",
              "Category chuniye — agle step mein services aur pricing dikhegi.",
              "Category chunein", "Categories", rows)
    update_session(phone, state="choosing_category")

def show_services(phone, cat_id):
    cat = CATALOG[cat_id]
    rows = [{"id": sid, "title": s["name"][:24], "description": f"₹{s['price']}"}
            for sid, s in cat["services"].items()]
    send_list(phone, cat["title"], "Service chuniye:", "Service chunein",
              cat["title"][:24], rows)
    update_session(phone, state="choosing_service", category=cat_id)

def ask_address(phone, svc_id):
    svc = find_service(svc_id)
    send_text(phone,
        f"✅ {svc['name']} — ₹{svc['price']}\n\n"
        "📍 Apna address bhejein (ghar number / mohalla + landmark):")
    update_session(phone, state="awaiting_address", service=svc_id)

def ask_date(phone):
    send_buttons(phone, "📅 Kis din service chahiye?",
        [{"id": "date_today", "title": "Aaj"},
         {"id": "date_tomorrow", "title": "Kal"},
         {"id": "date_other", "title": "Koi aur din"}])
    update_session(phone, state="awaiting_date")

def ask_slot(phone):
    send_buttons(phone, "⏰ Kaunsa time slot suitable hai?",
        [{"id": sid, "title": label.split(" (")[0]} for sid, label in SLOTS.items()])
    update_session(phone, state="awaiting_slot")

def upi_link(price, order_id):
    # WhatsApp only makes https:// links tappable, so we send our own pay page,
    # which opens the customer's UPI app with amount + order note pre-filled.
    return f"https://sevasaathi.co.in/pay.html?am={price}&o={order_id}"

def confirm_order(phone, session):
    svc = find_service(session["service"])
    order_date = session["date"]
    slot_label = SLOTS.get(session["slot"], session["slot"])
    pay_label = "UPI"
    with db() as c:
        cur = c.execute(
            "INSERT INTO orders (phone, service, price, address, date, slot, payment, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (phone, svc["name"], svc["price"], session["address"],
             order_date, slot_label, pay_label, datetime.now().isoformat()))
        order_id = cur.lastrowid

    send_text(phone,
        f"✅ Booking confirm ho gayi! (Order #{order_id})\n\n"
        f"Service: {svc['name']}\n"
        f"Date: {order_date}\nTime: {slot_label}\n"
        f"Address: {session['address']}\n"
        f"Amount: ₹{svc['price']}\n"
        f"Payment: {pay_label}\n\n"
        "Hamara professional jaldi confirm karega. Koi sawal ho to yahi reply karein. "
        "Nayi booking ke liye 'menu' likhein.")

    send_text(phone,
        f"💳 UPI se ₹{svc['price']} payment ke liye is link par tap karein:\n\n"
        f"{upi_link(svc['price'], order_id)}\n\n"
        f"Link aapka UPI app (GPay/PhonePe/Paytm) khol dega — amount aur "
        f"order number pehle se bhare honge.\n\n"
        f"Ya seedha is UPI ID par bhejein: {UPI_ID}\n"
        f"(Note mein 'Order #{order_id}' zaroor likhein)")

    # dispatch notification to admin
    if ADMIN_NUMBER:
        send_text(ADMIN_NUMBER,
            f"🔔 NEW ORDER #{order_id}\n"
            f"Customer: {phone}\nService: {svc['name']} — ₹{svc['price']}\n"
            f"Date/Time: {order_date} — {slot_label}\n"
            f"Address: {session['address']}\n"
            f"Payment: UPI — link bheja gaya, apne UPI app mein payment check karein"
            "\n\nProfessional assign karke customer ko inform karein.")
    reset_session(phone)

def find_service(svc_id):
    for cat in CATALOG.values():
        if svc_id in cat["services"]:
            return cat["services"][svc_id]
    return {"name": "Unknown", "price": 0}

# ---------------- WORKER SIGN-UP (people who want to work WITH us) ----------------
WORKER_KEYWORDS = (
    "worker", "wanna be a worker", "want to be a worker", "become a worker",
    "want to work", "work with you", "work for you", "join as", "apply for work",
    "job", "naukri", "rozgar", "kaam karna", "kaam chahiye", "kaam karunga",
    "kaam karungi", "partner banna", "become a partner",
)

def is_worker_intent(text_lower):
    """True when the message is about wanting a JOB with us, not booking a service."""
    return any(k in text_lower for k in WORKER_KEYWORDS)

def start_worker_signup(phone):
    send_text(phone,
        "Namaste! 🙏 SevaSaathi ke saath *kaam* karna chahte hain? Zabardast!\n\n"
        "Hum verified safai professionals ko unke area ke customers se jodte hain — "
        "aapke time pe kaam, seedha aapko payment.\n\n"
        "Shuru karte hain — aapka *pura naam* kya hai?")
    update_session(phone, state="worker_name")

def finish_worker_signup(phone, skills_text):
    session = get_session(phone)
    name = session.get("worker_name") or ""
    area = session.get("worker_area") or ""
    with db() as c:
        cur = c.execute(
            "INSERT INTO worker_applications (phone, name, area, skills, created_at) "
            "VALUES (?,?,?,?,?)",
            (phone, name, area, skills_text, datetime.now().isoformat()))
        app_id = cur.lastrowid
    send_text(phone,
        f"✅ Aapki application mil gayi! (Ref #{app_id})\n\n"
        f"Naam: {name}\nArea: {area}\nKaam: {skills_text}\n\n"
        "Hamari team 1-2 din mein aapko call karegi verification ke liye. "
        "Dhanyavaad! 🙏\n\n"
        "Service book karni ho to 'menu' likhein.")
    if ADMIN_NUMBER:
        send_text(ADMIN_NUMBER,
            f"🧑‍🔧 NEW WORKER APPLICATION #{app_id}\n"
            f"Name: {name}\nPhone: {phone}\nArea: {area}\n"
            f"Skills/Experience: {skills_text}\n\n"
            "Call karke verify karein aur onboard karein.")
    reset_session(phone)

FALLBACK = ("Samajh nahi paya 🙏\n'menu' likhein services dekhne ke liye, "
            "ya apna sawal likhein — hum jaldi reply karenge.")

# ---------------- MESSAGE ROUTER ----------------
def handle_message(phone, text, interactive_id):
    session = get_session(phone)
    state = session.get("state", "start")
    text_lower = (text or "").strip().lower()

    # global commands
    if text_lower in ("menu", "hi", "hello", "namaste", "start", "hii"):
        if state == "start":
            show_welcome(phone)
        else:
            show_categories(phone)
        return

    # "I want to work / be a worker" -> job sign-up, NOT a booking. Checked
    # before the website booking detection below, so "hi sevasaathi, i wanna be
    # a worker" (and the website's "Join as Worker" button) isn't mistaken for a
    # booking. Never interrupt someone mid-flow typing an address/date or the
    # worker form.
    if (text and is_worker_intent(text_lower)
            and state not in ("awaiting_address", "awaiting_custom_date",
                              "worker_name", "worker_area", "worker_work")):
        start_worker_signup(phone)
        return

    # website pre-filled messages, e.g. "Hi SevaSaathi, I'd like to book
    # Bathroom Deep Clean (₹99)." — detect the service name and jump straight to
    # the address step (but never while the customer is mid-flow typing an
    # address/date, or filling the worker form — those answers can contain
    # service words like "bathroom")
    if text and state not in ("awaiting_address", "awaiting_custom_date",
                              "worker_name", "worker_area", "worker_work"):
        for cat in CATALOG.values():
            for sid, s in cat["services"].items():
                if s["name"].lower() in text_lower:
                    ask_address(phone, sid)
                    return
        # generic booking intent from the website without a specific service
        if "sevasaathi" in text_lower or "book" in text_lower:
            show_categories(phone)
            return

    # button/list replies
    if interactive_id:
        if interactive_id == "show_menu":
            show_categories(phone); return
        if interactive_id == "join_worker":
            start_worker_signup(phone); return
        if interactive_id in CATALOG:
            show_services(phone, interactive_id); return
        if find_service(interactive_id)["price"] > 0 or interactive_id.startswith("svc_"):
            ask_address(phone, interactive_id); return
        if interactive_id == "date_today":
            update_session(phone, date=datetime.now().strftime("%d-%m-%Y"))
            ask_slot(phone); return
        if interactive_id == "date_tomorrow":
            from datetime import timedelta
            update_session(phone, date=(datetime.now() + timedelta(days=1)).strftime("%d-%m-%Y"))
            ask_slot(phone); return
        if interactive_id == "date_other":
            send_text(phone, "📅 Date likhein (jaise: 15-07-2026):")
            update_session(phone, state="awaiting_custom_date"); return
        if interactive_id in SLOTS:
            update_session(phone, slot=interactive_id, payment="upi")
            session = get_session(phone)
            confirm_order(phone, session); return

    # free-text states
    if state == "awaiting_address" and text:
        update_session(phone, address=text.strip())
        ask_date(phone); return
    if state == "awaiting_custom_date" and text:
        update_session(phone, date=text.strip(), state="awaiting_slot")
        ask_slot(phone); return
    # worker sign-up form (name -> area -> skills)
    if state == "worker_name" and text:
        update_session(phone, worker_name=text.strip(), state="worker_area")
        send_text(phone,
            f"Shukriya {text.strip()}! 📍 Aap *kaunse area / mohalle* mein kaam "
            "kar sakte hain? (jaise: Model Town, Dhand, Kaithal city)")
        return
    if state == "worker_area" and text:
        update_session(phone, worker_area=text.strip(), state="worker_work")
        send_text(phone,
            "Aap *kaunsa kaam* kar sakte hain aur kitna *experience* hai?\n"
            "(jaise: floor & bathroom cleaning, 2 saal ka experience)")
        return
    if state == "worker_work" and text:
        finish_worker_signup(phone, text.strip())
        return
    if state == "start":
        show_welcome(phone); return

    send_text(phone, FALLBACK)

# ---------------- WEBHOOK ENDPOINTS ----------------
@app.route("/webhook", methods=["GET"])
def verify():
    if (request.args.get("hub.mode") == "subscribe"
            and request.args.get("hub.verify_token") == VERIFY_TOKEN):
        return request.args.get("hub.challenge"), 200
    return "Verification failed", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    # Skip duplicate/retried webhook deliveries of the same message.
                    if already_processed(msg.get("id")):
                        continue
                    phone = msg["from"]
                    text, interactive_id = None, None
                    if msg["type"] == "text":
                        text = msg["text"]["body"]
                    elif msg["type"] == "interactive":
                        inter = msg["interactive"]
                        if inter["type"] == "button_reply":
                            interactive_id = inter["button_reply"]["id"]
                        elif inter["type"] == "list_reply":
                            interactive_id = inter["list_reply"]["id"]
                    handle_message(phone, text, interactive_id)
    except Exception as e:
        print("WEBHOOK ERROR:", e)
    return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return f"{BUSINESS_NAME} bot is running ✅", 200

# Create tables at import time too, so the bot works under gunicorn/production
# servers that never execute the __main__ block.
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))