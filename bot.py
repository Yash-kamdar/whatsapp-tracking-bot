from fastapi import FastAPI, Request
import requests
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI()

VERIFY_TOKEN = "shipmentbot123"
WHATSAPP_TOKEN = "EAA01wBtIopkBQ5OkahNmFScV5uD28AIZBXvimY3YQrwXQ1clazENoZAX4gQlqwdikCqUBo0soGCcLymZCwgP8OXvoV6PwEaZBVYTjCb0nq7vLmcqEM3V3bvC6mrx33rjkpcSRo71dgJH0RizrvMwsXAsVYWezTkUwZABGk0eEgKWn3VQxiTcy2ifkf2dGOtdUCAZDZD"
PHONE_NUMBER_ID = "983841231479330"

# ================= DATABASE =================

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

# ================= HEALTH CHECK =================

@app.get("/")
def home():
    return {"status": "Bot Running ✅"}

# ================= WHATSAPP SEND =================

def send_whatsapp_message(to, message):

    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }

    response = requests.post(url, headers=headers, json=data)

    print("WhatsApp API Response:", response.status_code)
    print(response.text)

# ================= PROGRESS =================

def progress_bar(status):

    s=status.upper()

    picked="⬜"
    transit="⬜"
    ofd="⬜"
    delivered="⬜"

    if "TRANSIT" in s:
        picked="✅"
        transit="✅"

    if "OUT" in s:
        picked=transit="✅"
        ofd="⏳"

    if "DELIVERED" in s:
        picked=transit=ofd="✅"
        delivered="🎉"

    return f"""
📦 Shipment Progress

📝 Booked ✅
📦 Picked Up {picked}
🚚 In Transit {transit}
🚚 Out for Delivery {ofd}
🎉 Delivered {delivered}

━━━━━━━━━━━━━━
"""

# ================= SHIPMOZO =================

def shipmozo_history(user,awb):

    url=f"https://webparex.in/public/api/customer/btp/track-order?tracking_number={awb}&public_key=&type=awb_number&from=WEB"

    r=requests.get(url).json()

    shipment=r["data"][0]

    scans=shipment["scan"]

    message="📦 Shipment Timeline\n"

    for scan in reversed(scans):

        message+=f"""
📍 {scan['location']}
🕒 {scan['date']} | {scan['time']}

📦 {scan['status']}
──────────────
"""

    send_whatsapp_message(user,message)

# ================= DELHIVERY =================

def delhivery_history(user,awb):

    headers={
        "Origin":"https://www.delhivery.com",
        "Referer":"https://www.delhivery.com/",
        "User-Agent":"Mozilla/5.0"
    }

    url=f"https://dlv-api.delhivery.com/v3/unified-tracking-new?wbn={awb}"

    r=requests.get(url,headers=headers).json()

    shipment=r["data"][0]

    message="🚛 Delhivery Timeline\n"

    for state in shipment["trackingStates"]:

        scans=state.get("scans")

        if not scans:
            continue

        for scan in reversed(scans):

            message+=f"""
📍 {scan.get('scannedLocation')}

📝 {scan.get('scanNslRemark')}
──────────────
"""

    send_whatsapp_message(user,message)

# ================= AUTO CHECK =================

def check_shipments():

    rows=cursor.execute(
        "SELECT user,awb,service,last_update FROM tracking"
    ).fetchall()

    for user,awb,service,last in rows:

        try:

            if service=="shipmozo":

                url=f"https://webparex.in/public/api/customer/btp/track-order?tracking_number={awb}&public_key=&type=awb_number&from=WEB"

                r=requests.get(url).json()

                latest=r["data"][0]["scan"][0]

                status=latest["status"]

                update=latest["date"]+latest["time"]

            else:

                headers={
                    "Origin":"https://www.delhivery.com",
                    "Referer":"https://www.delhivery.com/",
                    "User-Agent":"Mozilla/5.0"
                }

                url=f"https://dlv-api.delhivery.com/v3/unified-tracking-new?wbn={awb}"

                r=requests.get(url,headers=headers).json()

                latest=r["data"][0]["currentScan"]

                status=latest["ss"]

                update=latest["sd"]

            if update!=last:

                msg=progress_bar(status)

                msg+=f"\n📦 AWB {awb}\nStatus: {status}"

                send_whatsapp_message(user,msg)

                cursor.execute(
                    "UPDATE tracking SET last_update=? WHERE awb=?",
                    (update,awb)
                )

                if "DELIVERED" in status.upper():

                    send_whatsapp_message(
                        user,
                        f"🎉 Delivered\nAWB {awb}"
                    )

                    cursor.execute(
                        "DELETE FROM tracking WHERE awb=?",
                        (awb,)
                    )

                conn.commit()

        except Exception as e:
            print("Tracking error:",e)

# ================= SCHEDULER =================

scheduler=BackgroundScheduler()
scheduler.add_job(check_shipments,"interval",minutes=20)
scheduler.start()

# ================= WEBHOOK VERIFY =================

@app.get("/webhook")
def verify(mode:str=None,hub_challenge:str=None,hub_verify_token:str=None):

    if hub_verify_token==VERIFY_TOKEN:
        return hub_challenge

    return "error"

# ================= RECEIVE MESSAGE =================

@app.post("/webhook")
async def receive_message(request: Request):

    data = await request.json()

    try:
        entry = data.get("entry", [])

        if not entry:
            return "ok"

        changes = entry[0].get("changes", [])

        if not changes:
            return "ok"

        value = changes[0].get("value", {})

        messages = value.get("messages")

        # Ignore delivery/status updates
        if not messages:
            return "ok"

        msg = messages[0]

        sender = msg.get("from")

        if "text" not in msg:
            return "ok"

        text = msg["text"]["body"].lower()

        print("Incoming:", text)

    except Exception as e:
        print("Webhook error:", e)
        return "ok"

    # ===== COMMANDS =====

    if text == "track":

        send_whatsapp_message(
            sender,
            "Choose service:\nshipmozo\ndelhivery"
        )

        return "ok"

    if text == "list":

        rows = cursor.execute(
            "SELECT awb,service FROM tracking WHERE user=?",
            (sender,)
        ).fetchall()

        if not rows:
            send_whatsapp_message(sender,"No active tracking.")
            return "ok"

        message="📦 Active Shipments\n"

        for awb,service in rows:
            message+=f"{service} → {awb}\n"

        send_whatsapp_message(sender,message)

        return "ok"

    if text.startswith("history"):

        parts=text.split()

        if len(parts)!=2:
            send_whatsapp_message(sender,"history <awb>")
            return "ok"

        awb=parts[1]

        result=cursor.execute(
            "SELECT service FROM tracking WHERE awb=?",
            (awb,)
        ).fetchone()

        if not result:
            send_whatsapp_message(sender,"Not tracked")
            return "ok"

        service=result[0]

        if service=="shipmozo":
            shipmozo_history(sender,awb)
        else:
            delhivery_history(sender,awb)

        return "ok"

    return "ok"

# ---------- TRACK ----------

    if text=="track":

        send_whatsapp_message(
            sender,
            "Choose service:\nshipmozo\ndelhivery"
        )

        return "ok"

# ---------- LIST ----------

    if text=="list":

        rows=cursor.execute(
            "SELECT awb,service FROM tracking WHERE user=?",
            (sender,)
        ).fetchall()

        message="📦 Active Shipments\n"

        for awb,service in rows:
            message+=f"{service} → {awb}\n"

        send_whatsapp_message(sender,message)

        return "ok"

# ---------- HISTORY ----------

    if text.startswith("history"):

        parts=text.split()

        if len(parts)!=2:
            send_whatsapp_message(sender,"history <awb>")
            return "ok"

        awb=parts[1]

        result=cursor.execute(
            "SELECT service FROM tracking WHERE awb=?",
            (awb,)
        ).fetchone()

        if not result:
            send_whatsapp_message(sender,"Not tracked")
            return "ok"

        service=result[0]

        if service=="shipmozo":
            shipmozo_history(sender,awb)
        else:
            delhivery_history(sender,awb)

        return "ok"

    return "ok"
