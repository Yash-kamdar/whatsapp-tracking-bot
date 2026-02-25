from fastapi import FastAPI, Request
import requests
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI()

VERIFY_TOKEN = "shipmentbot123"
ACCESS_TOKEN = "EAA01wBtIopkBQ5y1F3mZCY9DVyTU00uffgIDUJZATFeEjZCZAG7KWJHASWqeZAlgAIgmnx4odtKK5XqVMb7bI8YmBaxbaBac5yH8vndLALT5eBzGMGrAXLEfhgaJxVTVbUkbBNcRZBwEc6BEF306kpHZC6vRaO9n8rSoZBUbBCZCfInJHVKbdMXBGVsm6GBcdpPiCSizw8YWKGUcySsWZA8EiPnOFIjC3seCHEhm1utI7l9hL7pMII2Ar9Q2e4UZASyJe6r5cOADLClrr3ELHlUSi8hLScZC2dqnQ3ZBfI31g5wZDZD"
PHONE_NUMBER_ID = "983841231479330"

# ---------------- DATABASE ----------------
conn = sqlite3.connect("tracking.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tracking(
user TEXT,
awb TEXT,
service TEXT,
last_update TEXT
)
""")
conn.commit()

user_state = {}

# ---------------- DELHIVERY HEADERS ----------------
DELHIVERY_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.delhivery.com",
    "Referer": "https://www.delhivery.com/",
    "User-Agent": "Mozilla/5.0"
}

scheduler = BackgroundScheduler()
scheduler.start()


# ---------------- SEND WHATSAPP ----------------
def send_whatsapp_message(to, message):

    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "text": {"body": message}
    }

    requests.post(url, headers=headers, json=data)


# ---------------- PROGRESS BAR ----------------
def progress_bar(status):

    s = status.upper()

    picked = "⬜"
    transit = "⬜"
    ofd = "⬜"
    delivered = "⬜"

    if "PICK" in s:
        picked = "✅"

    if "TRANSIT" in s:
        picked = transit = "✅"

    if "OUT" in s:
        picked = transit = "✅"
        ofd = "⏳"

    if "DELIVERED" in s:
        picked = transit = ofd = "✅"
        delivered = "🎉"

    return f"""
📦 *Shipment Progress*

📝 Booked ✅
📦 Picked Up {picked}
🚚 In Transit {transit}
🚚 Out for Delivery {ofd}
🎉 Delivered {delivered}

━━━━━━━━━━━━━━
"""


# ---------------- VERIFY ----------------
@app.get("/webhook")
async def verify(hub_mode=None,
                 hub_verify_token=None,
                 hub_challenge=None):

    if hub_verify_token == VERIFY_TOKEN:
        return int(hub_challenge)

    return "failed"


# ---------------- RECEIVE MESSAGE ----------------
@app.post("/webhook")
async def receive_message(request: Request):

    data = await request.json()

    msg = data["entry"][0]["changes"][0]["value"]["messages"][0]

    sender = msg["from"]
    text = msg["text"]["body"].lower().strip()

# ---------- LIST ----------
    if text == "list":

        cursor.execute(
            "SELECT awb,service FROM tracking WHERE user=?",
            (sender,)
        )

        rows = cursor.fetchall()

        if not rows:
            send_whatsapp_message(sender, "📭 No active tracking")
            return "ok"

        shipmozo = []
        delhivery = []

        for awb, service in rows:
            if service == "shipmozo":
                shipmozo.append(awb)
            else:
                delhivery.append(awb)

        message = "📦 *Active Shipments*\n\n"

        if shipmozo:
            message += "🚚 *Shipmozo*\n"
            for a in shipmozo:
                message += f"• {a}\n"

        if delhivery:
            message += "\n🚛 *Delhivery*\n"
            for a in delhivery:
                message += f"• {a}\n"

        message += f"\n━━━━━━━━━━━━━━\nTotal Tracking : *{len(rows)}*"

        send_whatsapp_message(sender, message)
        return "ok"

# ---------- HISTORY ----------
    if text.startswith("history"):

        parts = text.split()

        if len(parts) != 2:
            send_whatsapp_message(
                sender,
                "Usage:\nhistory <awb>"
            )
            return "ok"

        awb = parts[1]

        cursor.execute(
            "SELECT service FROM tracking WHERE user=? AND awb=?",
            (sender, awb)
        )

        result = cursor.fetchone()

        if not result:
            send_whatsapp_message(
                sender,
                "⚠️ This AWB is not being tracked."
            )
            return "ok"

        service = result[0]

        send_whatsapp_message(
            sender,
            f"📜 Fetching history for *{awb}*..."
        )

        if service == "shipmozo":
            shipmozo_history(sender, awb)
        else:
            delhivery_history(sender, awb)

        return "ok"

# ---------- TRACK ----------
    if text == "track":

        user_state[sender] = "choose"

        send_whatsapp_message(
            sender,
            "📦 *Choose Service*\n\nshipmozo\ndelhivery"
        )
        return "ok"

# ---------- SERVICE ----------
    if user_state.get(sender) == "choose":

        if text not in ["shipmozo", "delhivery"]:
            send_whatsapp_message(
                sender,
                "Reply *shipmozo* or *delhivery*"
            )
            return "ok"

        user_state[sender] = {"service": text}

        send_whatsapp_message(sender, "📦 Send tracking number")
        return "ok"

# ---------- SAVE AWB ----------
    state = user_state.get(sender)

    if isinstance(state, dict):

        if not text.isdigit():
            send_whatsapp_message(
                sender,
                "⚠️ Send valid tracking number"
            )
            return "ok"

        awb = text
        service = state["service"]

        cursor.execute(
            "SELECT * FROM tracking WHERE user=? AND awb=?",
            (sender, awb)
        )

        if cursor.fetchone():
            send_whatsapp_message(
                sender,
                "⚠️ This AWB already being tracked."
            )
            return "ok"

        cursor.execute(
            "INSERT INTO tracking VALUES(?,?,?,?)",
            (sender, awb, service, "")
        )

        conn.commit()
        user_state[sender] = None

        send_whatsapp_message(
            sender,
            f"""✅ *Tracking Started*

🔢 AWB: *{awb}*

Fetching shipment history... 📦"""
        )

        if service == "shipmozo":
            shipmozo_history(sender, awb)
        else:
            delhivery_history(sender, awb)

    return "ok"


# ---------------- SHIPMOZO HISTORY ----------------
def shipmozo_history(user, awb):

    try:

        shipment = requests.get(
            f"https://webparex.in/public/api/customer/btp/track-order?tracking_number={awb}&public_key=&type=awb_number&from=WEB"
        ).json()["data"][0]

        message = progress_bar(
            shipment["current_status"]
        )

        for scan in reversed(shipment["scan"]):

            message += f"""
📍 *{scan['location'].title()}*
🕒 {scan['date']} | {scan['time']}

📦 {scan['status']}

──────────────
"""

        send_whatsapp_message(user, message)

    except Exception as e:
        send_whatsapp_message(user, f"❌ {e}")


# ---------------- DELHIVERY HISTORY ----------------
def delhivery_history(user, awb):

    try:

        shipment = requests.get(
            f"https://dlv-api.delhivery.com/v3/unified-tracking-new?wbn={awb}",
            headers=DELHIVERY_HEADERS
        ).json()["data"][0]

        message = progress_bar(
            shipment["status"]["status"]
        )

        message += f"""
📦 AWB: *{awb}*
📅 Delivery: {shipment.get('deliveryDate','N/A')}

━━━━━━━━━━━━━━
📜 *Detailed Movement*
"""

        for state in shipment["trackingStates"]:

            scans = state.get("scans")

            if not scans:
                continue

            for scan in reversed(scans):

                message += f"""
📍 *{scan.get('scannedLocation')}*

📝 {scan.get('scanNslRemark')}

──────────────
"""

        send_whatsapp_message(user, message)

    except Exception as e:
        send_whatsapp_message(user, f"❌ Delhivery Error\n{e}")


# ---------------- AUTO CHECK ----------------
def check_shipments():

    cursor.execute("SELECT rowid,* FROM tracking")

    rows = cursor.fetchall()

    for row in rows:

        rowid, user, awb, service, last_update = row

        try:

            if service == "shipmozo":

                shipment = requests.get(
                    f"https://webparex.in/public/api/customer/btp/track-order?tracking_number={awb}&public_key=&type=awb_number&from=WEB"
                ).json()["data"][0]

                status = shipment["current_status"]

                if "DELIVERED" in status.upper():

                    send_whatsapp_message(
                        user,
                        f"🎉 *Delivered*\n📦 {awb}"
                    )

                    cursor.execute(
                        "DELETE FROM tracking WHERE rowid=?",
                        (rowid,)
                    )
                    conn.commit()
                    continue

                latest = shipment["scan"][0]

                update_id = (
                    latest["date"]
                    + latest["time"]
                    + latest["status"]
                )

                if update_id != last_update:

                    cursor.execute(
                        "UPDATE tracking SET last_update=? WHERE rowid=?",
                        (update_id, rowid)
                    )
                    conn.commit()

                    msg = progress_bar(status)

                    msg += f"""
📍 *{latest['location']}*
🕒 {latest['date']} | {latest['time']}

📦 {latest['status']}
"""

                    send_whatsapp_message(user, msg)

            else:

                shipment = requests.get(
                    f"https://dlv-api.delhivery.com/v3/unified-tracking-new?wbn={awb}",
                    headers=DELHIVERY_HEADERS
                ).json()["data"][0]

                status = shipment["status"]["status"]

                if "DELIVERED" in status.upper():

                    send_whatsapp_message(
                        user,
                        f"🎉 *Delivered*\n📦 {awb}"
                    )

                    cursor.execute(
                        "DELETE FROM tracking WHERE rowid=?",
                        (rowid,)
                    )
                    conn.commit()
                    continue

                latest = shipment["currentScan"]

                update_id = latest["ud"] + latest["sr"]

                if update_id != last_update:

                    cursor.execute(
                        "UPDATE tracking SET last_update=? WHERE rowid=?",
                        (update_id, rowid)
                    )
                    conn.commit()

                    msg = progress_bar(status)

                    msg += f"""
📍 *{latest['sl']}*

📝 {latest['sr']}
"""

                    send_whatsapp_message(user, msg)

        except Exception as e:
            print("Update error:", e)


scheduler.add_job(check_shipments, "interval", minutes=20)