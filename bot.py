"""
SevaSaathi — WhatsApp Home Services Booking Bot
Built on Meta WhatsApp Cloud API (free tier)

Flow: Welcome -> Category menu -> Service menu -> Address -> Date -> Slot -> Confirm -> Dispatch
"""

import os
import json
import requests
from datetime import datetime, date, timedelta, timezone
from urllib.parse import quote
from flask import Flask, request
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

app = Flask(__name__)

# ---------------- CONFIG (set these as environment variables) ----------------
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_secret_verify_token_123")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")          # Meta permanent access token
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")        # from Meta dashboard
ADMIN_NUMBER = os.environ.get("ADMIN_NUMBER", "")              # one or MORE numbers, comma-separated e.g. 9198...,9199...
# Split into a clean list so a new order/worker can alert several people at once.
ADMIN_NUMBERS = [n.strip() for n in ADMIN_NUMBER.split(",") if n.strip()]
BUSINESS_NAME = os.environ.get("BUSINESS_NAME", "SevaSaathi")
UPI_ID = os.environ.get("UPI_ID", "yourname@upi")

API_URL = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
HEADERS = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

# ---------------- SERVICE CATALOG (edit prices here) ----------------
# "price" = what the customer pays. Optional "mrp" = the original price, shown
# struck through (same discount scheme as the website). Optional "short" = a
# shorter label for WhatsApp list rows (max 24 chars).
# The first 8 regular services mirror the website (seva saathi official/website/
# index.html) — if you change one, change the website too, and vice versa.
CATALOG = {
    # DIWALI (Oct 2026) — only shown while diwali_active(); remove after the offer
    "cat_diwali": {
        "title": "🪔 Diwali Special",
        "desc": "1 house help ₹499 · 2 house help ₹799",
        "services": {
            "svc_diwali_1": {"name": "Diwali Cleaning (1 House Help)", "short": "1 House Help", "price": 499},
            "svc_diwali_2": {"name": "Diwali Cleaning (2 House Help)", "short": "2 House Help", "price": 799},
        },
    },
    "cat_cleaning": {
        "title": "Cleaning services",
        "services": {
            "svc_mopping":   {"name": "Floor Sweeping & Mopping", "price": 199},
            "svc_bathroom":  {"name": "Bathroom Deep Clean",      "price": 199, "mrp": 299},
            "svc_kitchen":   {"name": "Kitchen Cleaning",         "price": 199, "mrp": 349},
            "svc_deepclean": {"name": "Full House Deep Clean",    "price": 499, "mrp": 999},
            "svc_sofa":      {"name": "Carpet & Sofa Cleaning",   "price": 199, "mrp": 499},
        },
    },
    "cat_laundry": {
        "title": "Laundry & wardrobe",
        "services": {
            "svc_laundry":   {"name": "Laundry & Ironing",      "price": 199, "mrp": 249},
            "svc_wardrobe":  {"name": "Wardrobe Cleaning",      "price": 199, "mrp": 299},
        },
    },
    "cat_kitchen": {
        "title": "Kitchen & utensils",
        "services": {
            "svc_utensils":  {"name": "Utensil Washing",        "price": 199, "mrp": 249},
            "svc_kprep":     {"name": "Kitchen Prep",           "price": 199, "mrp": 299},
            "svc_cabinet":   {"name": "Kitchen Cabinet Clean",  "price": 199, "mrp": 299},
        },
    },
    "cat_packing": {
        "title": "Packing & shifting",
        "services": {
            "svc_packing":   {"name": "Packing / Unpacking",    "price": 199, "mrp": 299},
        },
    },
    "cat_party": {
        "title": "Party ready",
        "services": {
            "svc_preparty":  {"name": "Pre-Party Express Clean",   "price": 199, "mrp": 299},
            "svc_afterparty":{"name": "After-Party Express Clean", "short": "After-Party Clean", "price": 199, "mrp": 299},
        },
    },
    "cat_extras": {
        "title": "Extras",
        "services": {
            "svc_window":    {"name": "Window Cleaning",        "price": 199},
            "svc_fan":       {"name": "Fan Cleaning",           "price": 149, "mrp": 249},
            "svc_dusting":   {"name": "Dusting & Wiping",       "price": 199, "mrp": 299},
            "svc_balcony":   {"name": "Balcony Cleaning",       "price": 199, "mrp": 299},
            "svc_fridge":    {"name": "Fridge Cleaning",        "price": 199, "mrp": 299},
            "svc_car":       {"name": "Car Surface Cleaning",   "price": 199, "mrp": 299},
        },
    },
}

SLOTS = {
    "slot_morning":   "Subah (9 AM - 12 PM)",
    "slot_afternoon": "Dopeher (12 PM - 3 PM)",
    "slot_evening":   "Shaam (3 PM - 6 PM)",
}

# ---------------- DIWALI OFFER WINDOW (Oct 2026) ----------------
# Bookings for the Diwali offer are only accepted on these dates (India time).
# Outside the window the Diwali menu/buttons disappear, and any old Diwali button
# a customer taps gets a polite "offer ended" + the regular menu.
# To extend the offer, just change DIWALI_LAST_DAY.
IST = timezone(timedelta(hours=5, minutes=30))
DIWALI_FIRST_DAY = date(2026, 10, 1)
DIWALI_LAST_DAY = date(2026, 10, 31)
DIWALI_SERVICES = set(CATALOG["cat_diwali"]["services"])

def today_ist():
    # Render's servers run on UTC; customers are in India, so "today" uses IST.
    return datetime.now(IST).date()

def diwali_active():
    return DIWALI_FIRST_DAY <= today_ist() <= DIWALI_LAST_DAY

# ---------------- PRICE DISPLAY ----------------
def price_text(svc):
    """For normal WhatsApp text messages: original price struck through, offer price bold."""
    if svc.get("mrp"):
        return f"~₹{svc['mrp']}~ *₹{svc['price']}*"
    return f"*₹{svc['price']}*"

def strike(text):
    """WhatsApp list rows ignore ~strike~ formatting, so draw the line with
    Unicode instead (a combining long stroke after every character)."""
    return "".join(ch + "̶" for ch in text)

def list_price(svc):
    """For WhatsApp list rows: struck original + offer price, e.g. ₹̶2̶9̶9̶ ₹199"""
    if svc.get("mrp"):
        return f"{strike('₹' + str(svc['mrp']))} ₹{svc['price']}"
    return f"₹{svc['price']}"

# ---------------- DATABASE (shared MongoDB with the web app) ----------------
# The bot and the SevaSaathi web app use the SAME MongoDB, so WhatsApp orders
# show up in the web admin dashboard. Set MONGODB_URI to the same Atlas string
# the web app uses. Collections:
#   bot_sessions   -> per-phone conversation state (bot only)
#   whatsapporders -> confirmed orders (read by the web admin dashboard)
#   counters       -> sequential order-number generator
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
MONGODB_DB = os.environ.get("MONGODB_DB", "sevasaathi")

_client = MongoClient(MONGODB_URI)
_db = _client[MONGODB_DB]
sessions_col = _db["bot_sessions"]
orders_col = _db["whatsapporders"]
counters_col = _db["counters"]
processed_col = _db["processed_messages"]   # WhatsApp message ids we've already handled (dedup)
worker_apps_col = _db["worker_applications"] # people who want to work with us (admin follows up)

# fields we persist on a session (mirrors the old SQLite columns)
SESSION_FIELDS = ("state", "category", "service", "address", "date", "slot", "payment")

def init_db():
    # Indexes are idempotent; safe to call on every startup.
    orders_col.create_index("orderNumber")
    orders_col.create_index("status")
    # Auto-expire dedup records 24h after insert, so the collection stays tiny.
    # WhatsApp only retries for a few hours, so a 1-day memory is plenty.
    processed_col.create_index("createdAt", expireAfterSeconds=86400)
    worker_apps_col.create_index("applicationNumber")
    worker_apps_col.create_index("status")

def next_order_number():
    doc = counters_col.find_one_and_update(
        {"_id": "orderNumber"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]

def next_worker_number():
    doc = counters_col.find_one_and_update(
        {"_id": "workerNumber"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]

def get_session(phone):
    row = sessions_col.find_one({"_id": phone})
    if not row:
        sessions_col.insert_one(
            {"_id": phone, "state": "start", "updated_at": datetime.now().isoformat()})
        return {"phone": phone, "state": "start"}
    row["phone"] = phone
    return row

def update_session(phone, **kwargs):
    kwargs["updated_at"] = datetime.now().isoformat()
    sessions_col.update_one({"_id": phone}, {"$set": kwargs}, upsert=True)

def reset_session(phone):
    update_session(phone, state="start", category=None, service=None,
                   address=None, date=None, slot=None, payment=None,
                   worker_name=None, worker_area=None)

def already_processed(message_id):
    """WhatsApp re-delivers a webhook if we don't ACK with 200 fast enough, so
    the same message can arrive 2-3 times — that is what made the bot reply
    twice or thrice. We record each message id atomically: the first delivery
    inserts it and is handled; any retry hits the duplicate key and is skipped."""
    if not message_id:
        return False
    try:
        processed_col.insert_one({"_id": message_id, "createdAt": datetime.utcnow()})
        return False   # inserted just now -> first time we've seen this message
    except DuplicateKeyError:
        return True    # already recorded -> this is a retried/duplicate delivery
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

def notify_admins(text):
    """Alert every configured admin number. ADMIN_NUMBER may hold several numbers
    separated by commas, so an order/worker application can reach both partners."""
    for num in ADMIN_NUMBERS:
        send_text(num, text)

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
    body = (f"Namaste! 🙏 {BUSINESS_NAME} mein aapka swagat hai.\n\n"
            "Hum ghar ki cleaning services provide karte hain — verified professionals, "
            "fixed pricing, aapke time pe.")
    buttons = [{"id": "show_menu", "title": "Services dekhein"},
               {"id": "join_worker", "title": "Kaam karna hai"}]
    if diwali_active():   # DIWALI
        body += ("\n\n🪔 *Diwali Special:* 1 house help ₹499 · 2 house help ₹799 "
                 f"(booking {DIWALI_LAST_DAY.day} October tak)")
        buttons.insert(0, {"id": "show_diwali", "title": "🪔 Diwali Offer"})
    send_buttons(phone, body, buttons)
    update_session(phone, state="welcome_sent")

def show_categories(phone):
    rows = [{"id": cid, "title": cat["title"][:24],
             "description": cat.get("desc", ", ".join(s["name"] for s in cat["services"].values()))[:72]}
            for cid, cat in CATALOG.items()
            if cid != "cat_diwali" or diwali_active()]
    send_list(phone, "Kaunsi service chahiye?",
              "Category chuniye — agle step mein services aur pricing dikhegi.",
              "Category chunein", "Categories", rows)
    update_session(phone, state="choosing_category")

def show_services(phone, cat_id):
    if cat_id == "cat_diwali" and not diwali_active():
        diwali_ended(phone); return
    cat = CATALOG[cat_id]
    rows = [{"id": sid, "title": s.get("short", s["name"])[:24], "description": list_price(s)}
            for sid, s in cat["services"].items()]
    send_list(phone, cat["title"], "Service chuniye:", "Service chunein",
              cat["title"][:24], rows)
    update_session(phone, state="choosing_service", category=cat_id)

def ask_address(phone, svc_id):
    if svc_id in DIWALI_SERVICES and not diwali_active():
        diwali_ended(phone); return
    svc = find_service(svc_id)
    if not svc["price"]:   # an old button for a service we dropped (e.g. Plant Care)
        service_gone(phone); return
    send_text(phone,
        f"✅ {svc['name']} — {price_text(svc)}\n\n"
        "📍 Apna address bhejein (ghar number / mohalla + landmark):")
    update_session(phone, state="awaiting_address", service=svc_id)

def service_gone(phone):
    send_text(phone,
        "🙏 Ye service ab available nahi hai. Hamari baaki services neeche dekhein 👇")
    show_categories(phone)

# ---------------- DIWALI FLOW (Oct 2026) — remove after the offer ----------------
def show_diwali_options(phone):
    if not diwali_active():
        diwali_ended(phone); return
    send_buttons(phone,
        "🪔 *Diwali Cleaning Special*\n\n"
        "Lakshmi Puja se pehle ghar chamkaayein! Kitne house help chahiye?\n\n"
        "• 1 House Help — *₹499*\n"
        "• 2 House Help — *₹799*\n\n"
        f"📅 Booking sirf {DIWALI_LAST_DAY.day} October tak.",
        [{"id": "svc_diwali_1", "title": "1 House Help ₹499"},
         {"id": "svc_diwali_2", "title": "2 House Help ₹799"},
         {"id": "show_menu", "title": "Baaki services"}])
    update_session(phone, state="choosing_service", category="cat_diwali")

def diwali_ended(phone):
    send_text(phone,
        "🪔 Diwali Cleaning offer ab available nahi hai (iski booking "
        f"{DIWALI_FIRST_DAY.day}–{DIWALI_LAST_DAY.day} October tak hi thi). "
        "Hamari baaki services neeche dekhein 👇")
    show_categories(phone)

DIWALI_WORDS = ("diwali", "deepawali", "deepavali", "dipawali", "दिवाली", "दीवाली", "दीपावली")

def is_diwali_text(text_lower):
    # "house help" is the Diwali offer's wording, so it counts only while the offer runs
    return (any(w in text_lower for w in DIWALI_WORDS)
            or (diwali_active() and "house help" in text_lower))

def handle_diwali_text(phone, text_lower):
    """The website's Diwali buttons send '... Diwali Cleaning — 1 house help (₹499).'
    or '— 2 house help (₹799).' — anything else mentioning Diwali gets the options."""
    if not diwali_active():
        diwali_ended(phone)
    elif "2 house help" in text_lower:
        ask_address(phone, "svc_diwali_2")
    elif "1 house help" in text_lower:
        ask_address(phone, "svc_diwali_1")
    else:
        show_diwali_options(phone)

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
    # WhatsApp keeps old buttons tappable: tapping a time slot again after the
    # booking is done (or before an address was given) must NOT create a blank
    # "Unknown — ₹0" order and alert the admins.
    if not (session.get("service") and session.get("address") and session.get("date")):
        send_text(phone,
            "Ye booking pehle hi confirm ho chuki hai ya adhoori reh gayi thi 🙏\n"
            "Nayi booking ke liye 'menu' likhein.")
        return
    # DIWALI: the offer closed while this customer was still mid-booking
    if session["service"] in DIWALI_SERVICES and not diwali_active():
        reset_session(phone)
        diwali_ended(phone); return
    svc = find_service(session["service"])
    if not svc["price"]:   # we dropped this service while they were mid-booking
        reset_session(phone)
        service_gone(phone); return
    order_date = session["date"]
    slot_label = SLOTS.get(session["slot"], session["slot"])
    pay_label = "UPI"
    order_id = next_order_number()
    orders_col.insert_one({
        "orderNumber": order_id,
        "phone": phone,
        "service": svc["name"],
        "price": svc["price"],
        "address": session["address"],
        "date": order_date,
        "slot": slot_label,
        "payment": "upi",
        "status": "new",
        "source": "whatsapp",
        "createdAt": datetime.utcnow(),
    })

    send_text(phone,
        f"✅ Booking confirm ho gayi! (Order #{order_id})\n\n"
        f"Service: {svc['name']}\n"
        f"Date: {order_date}\nTime: {slot_label}\n"
        f"Address: {session['address']}\n"
        f"Amount: {price_text(svc)}\n"
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

    # dispatch notification to admin(s)
    notify_admins(
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
        "Shuru karte hain — aapka *pura naam* kya hai?\n\n"
        "_(Kabhi bhi 'cancel' likh kar ruk sakte hain.)_")
    update_session(phone, state="worker_name")

def finish_worker_signup(phone, skills_text):
    session = get_session(phone)
    name = session.get("worker_name", "")
    area = session.get("worker_area", "")
    app_id = next_worker_number()
    worker_apps_col.insert_one({
        "applicationNumber": app_id,
        "phone": phone,
        "name": name,
        "area": area,
        "skills": skills_text,
        "status": "new",
        "source": "whatsapp",
        "createdAt": datetime.utcnow(),
    })
    send_text(phone,
        f"✅ Aapki application mil gayi! (Ref #{app_id})\n\n"
        f"Naam: {name}\nArea: {area}\nKaam: {skills_text}\n\n"
        "Hamari team 1-2 din mein aapko call karegi verification ke liye. "
        "Dhanyavaad! 🙏\n\n"
        "Service book karni ho to 'menu' likhein.")
    notify_admins(
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

    # let the user bail out of ANY flow (worker sign-up, booking, etc.)
    if text_lower in ("cancel", "stop", "exit", "cancel karo", "band karo",
                      "rehne do", "chhodo", "chodo"):
        reset_session(phone)
        send_text(phone,
            "Theek hai, cancel ho gaya 👍\n"
            "Service book karni ho to 'menu' likhein.")
        return

    # "I want to work / be a worker" -> job sign-up, NOT a booking. Checked
    # before the website booking detection below, so "hi sevasaathi, i wanna be
    # a worker" isn't mistaken for a booking. Never interrupt someone who is
    # mid-flow typing an address/date or already filling the worker form.
    if (text and is_worker_intent(text_lower)
            and state not in ("awaiting_address", "awaiting_custom_date",
                              "worker_name", "worker_area", "worker_work")):
        start_worker_signup(phone)
        return

    # website pre-filled messages, e.g. "Hi SevaSaathi, I'd like to book
    # Bathroom Deep Clean (₹199)." — detect the service name and jump
    # straight to the address step (but never while the customer is mid-flow
    # typing an address/date, or filling the worker form — those answers can
    # contain service words like "bathroom")
    if text and state not in ("awaiting_address", "awaiting_custom_date",
                              "worker_name", "worker_area", "worker_work"):
        for cat in CATALOG.values():
            for sid, s in cat["services"].items():
                if s["name"].lower() in text_lower:
                    ask_address(phone, sid)
                    return
        # DIWALI: the website's Diwali cards ("... Diwali Cleaning — 2 house
        # help (₹799).") or anyone typing about Diwali. Checked after the
        # service names, so "diwali se pehle bathroom deep clean" -> bathroom.
        if is_diwali_text(text_lower):
            handle_diwali_text(phone, text_lower)
            return
        # generic booking intent from the website without a specific service
        if "sevasaathi" in text_lower or "book" in text_lower:
            show_categories(phone)
            return

    # button/list replies
    if interactive_id:
        if interactive_id == "show_menu":
            show_categories(phone); return
        if interactive_id == "show_diwali":   # DIWALI
            show_diwali_options(phone); return
        if interactive_id == "join_worker":
            start_worker_signup(phone); return
        if interactive_id in CATALOG:
            show_services(phone, interactive_id); return
        if find_service(interactive_id)["price"] > 0 or interactive_id.startswith("svc_"):
            ask_address(phone, interactive_id); return
        if interactive_id == "date_today":   # India date, not the server's UTC date
            update_session(phone, date=today_ist().strftime("%d-%m-%Y"))
            ask_slot(phone); return
        if interactive_id == "date_tomorrow":
            update_session(phone, date=(today_ist() + timedelta(days=1)).strftime("%d-%m-%Y"))
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

# Create indexes at import time too, so the bot works under gunicorn/production
# servers that never execute the __main__ block. Wrapped so a wrong/missing
# MONGODB_URI logs a clear error instead of crash-looping the whole service.
try:
    init_db()
except Exception as e:
    print("INIT_DB ERROR — check the MONGODB_URI env var on Render:", e)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))