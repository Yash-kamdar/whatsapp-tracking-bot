from fastapi import FastAPI, Request
import requests
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler
import os

app = FastAPI()

# ================= WHATSAPP =================

VERIFY_TOKEN = "shipmentbot123"

ACCESS_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

GRAPH_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"


# ================= DATABASE =================

conn = sqlite3.connect("tracking.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tracking(
user TEXT,
awb TEXT,
service TEXT,
last_update TEXT,
status TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS user_state(
user TEXT PRIMARY KEY,
state TEXT,
service TEXT
)
""")

conn.commit()


# ================= WHATSAPP SEND =================

def send_whatsapp_message(to, message):

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message
        }
    }

    r = requests.post(GRAPH_URL, headers=headers, json=payload)

    print("WhatsApp:", r.status_code, r.text)


# ================= USER STATE =================

def set_state(user, state, service=None):

    cursor.execute("""
    INSERT OR REPLACE INTO user_state(user,state,service)
    VALUES(?,?,?)
    """, (user, state, service))

    conn.commit()


def get_state(user):

    cursor.execute(
        "SELECT state,service FROM user_state WHERE user=?",
        (user,)
    )

    row = cursor.fetchone()

    if row:
        return row[0], row[1]

    return None, None


def clear_state(user):
    cursor.execute(
        "DELETE FROM user_state WHERE user=?",
        (user,)
    )
    conn.commit()


# ================= WEBHOOK VERIFY =================

@app.get("/webhook")
async def verify(request: Request):

    params = request.query_params

    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))

    return "error"


# ================= RECEIVE MESSAGE =================

@app.post("/webhook")
async def receive_message(request: Request):

    data = await request.json()

    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
    except:
        return {"status": "ok"}

    sender = msg["from"]
    text = msg["text"]["body"].lower().strip()

    print("Incoming:", text)

    state, service = get_state(sender)

    # ================= TRACK =================

    if text == "track":

        set_state(sender, "choose_service")

        send_whatsapp_message(
            sender,
            "📦 *Start Tracking*\n\n"
            "Choose courier:\n"
            "🚚 shipmozo\n"
            "🚛 delhivery"
        )
        return {"ok": True}

    # ================= SERVICE =================

    if state == "choose_service":

        if text not in ["shipmozo", "delhivery"]:

            send_whatsapp_message(
                sender,
                "❌ Please choose:\nshipmozo or delhivery"
            )
            return {"ok": True}

        set_state(sender, "await_awb", text)

        send_whatsapp_message(
            sender,
            f"✅ *{text.title()} Selected*\n\n"
            "📦 Send Tracking Number"
        )

        return {"ok": True}

    # ================= AWB =================

    if state == "await_awb":

        awb = text

        cursor.execute("""
        SELECT * FROM tracking
        WHERE user=? AND awb=?
        """, (sender, awb))

        if cursor.fetchone():

            send_whatsapp_message(
                sender,
                "⚠️ Already tracking this AWB."
            )
            return {"ok": True}

        cursor.execute("""
        INSERT INTO tracking
        VALUES(?,?,?,?,?)
        """, (sender, awb, service, "", "ACTIVE"))

        conn.commit()

        clear_state(sender)

        send_whatsapp_message(
            sender,
            f"✅ *Tracking Started*\n"
            f"📦 AWB: {awb}\n"
            f"🚚 Service: {service.title()}"
        )

        return {"ok": True}

    # ================= LIST =================

    if text == "list":

        cursor.execute(
            "SELECT awb,service FROM tracking WHERE user=?",
            (sender,)
        )

        rows = cursor.fetchall()

        if not rows:
            send_whatsapp_message(
                sender,
                "📭 No active tracking."
            )
            return {"ok": True}

        msg = "📦 *Active Shipments*\n\n"

        for r in rows:
            msg += f"• {r[0]} ({r[1]})\n"

        send_whatsapp_message(sender, msg)

        return {"ok": True}

    return {"ok": True}


# ================= SHIPMOZO =================

def shipmozo_track(awb):

    url = f"https://webparex.in/public/api/customer/btp/track-order?tracking_number={awb}&public_key=&type=awb_number&from=WEB"

    r = requests.get(url, timeout=15)

    data = r.json()

    scans = data["data"][0]["scan"]

    latest = scans[0]

    update = f"{latest['date']} {latest['time']} {latest['status']}"

    delivered = "DELIVERED" in latest["status"].upper()

    return update, delivered


# ================= DELHIVERY =================

def delhivery_track(awb):

    headers = {
        "Origin": "https://www.delhivery.com",
        "Referer": "https://www.delhivery.com/",
        "User-Agent": "Mozilla/5.0"
    }

    url = f"https://dlv-api.delhivery.com/v3/unified-tracking-new?wbn={awb}"

    r = requests.get(url, headers=headers, timeout=15)

    data = r.json()

    status = data["data"][0]["status"]

    update = f"{status['status']} - {status['instructions']}"

    delivered = status["status"] == "DELIVERED"

    return update, delivered


# ================= BACKGROUND CHECK =================

def check_updates():

    cursor.execute(
        "SELECT user,awb,service,last_update FROM tracking"
    )

    rows = cursor.fetchall()

    for user, awb, service, last in rows:

        try:

            if service == "shipmozo":
                update, delivered = shipmozo_track(awb)
            else:
                update, delivered = delhivery_track(awb)

            if update != last:

                message = (
                    "🚚 *Shipment Update*\n\n"
                    f"📦 AWB: {awb}\n"
                    f"📍 {update}"
                )

                send_whatsapp_message(user, message)

                cursor.execute("""
                UPDATE tracking
                SET last_update=?
                WHERE awb=?
                """, (update, awb))

                conn.commit()

            if delivered:

                send_whatsapp_message(
                    user,
                    f"✅ *Delivered*\n📦 {awb}"
                )

                cursor.execute(
                    "DELETE FROM tracking WHERE awb=?",
                    (awb,)
                )

                conn.commit()

        except Exception as e:
            print("Tracking error:", e)


# ================= SCHEDULER =================

scheduler = BackgroundScheduler()
scheduler.add_job(check_updates, "interval", minutes=20)
scheduler.start()


@app.get("/")
def home():
    return {"status": "running"}
