from fastapi import FastAPI, Request
import requests
import sqlite3
import os
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI()

# ================= ENV =================

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

if not ACCESS_TOKEN:
    raise Exception("Missing WHATSAPP_ACCESS_TOKEN")

# ================= DATABASE =================

conn = sqlite3.connect("tracking.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tracking(
user TEXT,
awb TEXT PRIMARY KEY,
service TEXT,
last_update TEXT
)
""")

# prevent duplicate webhook execution
cursor.execute("""
CREATE TABLE IF NOT EXISTS processed_messages(
id TEXT PRIMARY KEY
)
""")

conn.commit()

# ================= USER STATE =================

user_state = {}

# ================= WHATSAPP SEND =================

def send_message(to, message):

    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message
        }
    }

    r = requests.post(url, headers=headers, json=data)

    if r.status_code != 200:
        print("WhatsApp Error:", r.text)


# ================= SHIPMOZO =================

def shipmozo_track(awb):

    url = f"https://webparex.in/public/api/customer/btp/track-order?tracking_number={awb}&public_key=&type=awb_number&from=WEB"

    r = requests.get(url, timeout=15)

    data = r.json()

    shipment = data.get("data", [])

    if not shipment:
        return []

    scans_raw = shipment[0].get("scan", [])

    scans = []

    for s in scans_raw:

        scans.append({
            "status": s.get("status", "").strip(),
            "location": s.get("location", ""),
            "date": f"{s.get('date','')} {s.get('time','')}"
        })

    return scans


# ================= DELHIVERY =================

def delhivery_track(awb):

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://www.delhivery.com",
        "Referer": "https://www.delhivery.com/"
    }

    url = f"https://dlv-api.delhivery.com/v3/unified-tracking-new?wbn={awb}"

    r = requests.get(url, headers=headers, timeout=15)

    data = r.json()

    states = data["data"][0]["trackingStates"]

    scans = []

    for s in states:
        if s.get("scans"):
            for scan in s["scans"]:
                scans.append({
                    "status": scan.get("scan"),
                    "location": scan.get("scannedLocation"),
                    "date": s.get("date")
                })

    return scans


# ================= FORMAT =================

def format_history(awb, scans):

    msg = f"📦 *Tracking History*\nAWB: {awb}\n\n"

    for s in scans[::-1]:

        msg += (
            f"━━━━━━━━━━━━━━\n"
            f"📍 {s.get('location','')}\n"
            f"✅ {s.get('status','')}\n"
            f"🕒 {s.get('date','')}\n\n"
        )

    return msg


# ================= CHECK UPDATES =================

def check_updates():

    rows = cursor.execute(
        "SELECT * FROM tracking"
    ).fetchall()

    for user, awb, service, last in rows:

        try:

            scans = (
                shipmozo_track(awb)
                if service == "shipmozo"
                else delhivery_track(awb)
            )

            if not scans:
                continue

            latest = str(scans[-1])

            if latest != last:

                cursor.execute(
                    "UPDATE tracking SET last_update=? WHERE awb=?",
                    (latest, awb)
                )

                conn.commit()

                msg = (
                    f"🚚 *Shipment Update*\n\n"
                    f"AWB: {awb}\n"
                    f"{scans[-1]}"
                )

                send_message(user, msg)

                if "Delivered" in latest:
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

if not scheduler.running:
    scheduler.start()


# ================= VERIFY =================

@app.get("/webhook")
def verify(request: Request):

    params = request.query_params

    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))

    return "Error"


# ================= RECEIVE =================

@app.post("/webhook")
async def receive(req: Request):

    data = await req.json()

    try:
        value = data["entry"][0]["changes"][0]["value"]

        # ignore status updates
        if "messages" not in value:
            return "ok"

        msg = value["messages"][0]

        # duplicate protection
        message_id = msg.get("id")

        exist = cursor.execute(
            "SELECT id FROM processed_messages WHERE id=?",
            (message_id,)
        ).fetchone()

        if exist:
            return "ok"

        cursor.execute(
            "INSERT INTO processed_messages VALUES(?)",
            (message_id,)
        )
        conn.commit()

        if msg.get("type") != "text":
            return "ok"

        sender = msg["from"]
        text = msg["text"]["body"].lower().strip()

    except Exception as e:
        print("Webhook parse error:", e)
        return "ok"

    print("Incoming:", text)

# ---------- TRACK ----------

    if text == "track":

        user_state[sender] = "choose"

        send_message(
            sender,
            "📦 *Start Tracking*\n\nChoose courier:\n🚚 shipmozo\n🚛 delhivery"
        )

        return "ok"

# ---------- FLOW ----------

    if sender in user_state:

        if user_state[sender] == "choose":

            if text not in ["shipmozo", "delhivery"]:
                send_message(sender, "❌ Invalid courier")
                return "ok"

            user_state[sender] = text

            send_message(sender, "📦 Send Tracking Number")

            return "ok"

        else:

            service = user_state[sender]
            awb = text

            exist = cursor.execute(
                "SELECT awb FROM tracking WHERE awb=?",
                (awb,)
            ).fetchone()

            if exist:
                send_message(sender, "⚠ Already tracking")
                return "ok"

            scans = (
                shipmozo_track(awb)
                if service == "shipmozo"
                else delhivery_track(awb)
            )

            if not scans:
                send_message(sender, "❌ Tracking not found")
                return "ok"

            cursor.execute(
                "INSERT INTO tracking VALUES(?,?,?,?)",
                (sender, awb, service, str(scans[-1]))
            )

            conn.commit()

            send_message(sender, "⏳ Fetching shipment history...")

            send_message(
                sender,
                format_history(awb, scans)
            )

            del user_state[sender]

            return "ok"

# ---------- LIST ----------

    if text == "list":

        rows = cursor.execute(
            "SELECT awb,service FROM tracking WHERE user=?",
            (sender,)
        ).fetchall()

        if not rows:
            send_message(sender, "📭 No active tracking")
            return "ok"

        msg = "📦 Active Shipments\n\n"

        for a, s in rows:
            msg += f"• {a} ({s})\n"

        send_message(sender, msg)

        return "ok"

# ---------- HISTORY ----------

    # ---------- HISTORY ----------

if text.startswith("history"):

    try:
        awb = text.split()[1]
    except:
        send_message(sender, "Usage:\nhistory AWB")
        return "ok"

    # try database first
    row = cursor.execute(
        "SELECT service FROM tracking WHERE awb=?",
        (awb,)
    ).fetchone()

    service = None

    if row:
        service = row[0]

    # auto fallback detection
    if not service:

        try:
            scans = shipmozo_track(awb)
            if scans:
                send_message(sender, format_history(awb, scans))
                return "ok"
        except:
            pass

        try:
            scans = delhivery_track(awb)
            if scans:
                send_message(sender, format_history(awb, scans))
                return "ok"
        except:
            pass

        send_message(sender, "❌ Tracking not found")
        return "ok"

    scans = (
        shipmozo_track(awb)
        if service == "shipmozo"
        else delhivery_track(awb)
    )

    send_message(sender, format_history(awb, scans))

    return "ok"

return "ok"
