from fastapi import FastAPI, Request
import requests
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler
import os

app = FastAPI()

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
last_update TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS state(
user TEXT PRIMARY KEY,
step TEXT,
service TEXT
)
""")

conn.commit()


# ================= SEND MESSAGE =================

def send_whatsapp_message(to, message):

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }

    r = requests.post(GRAPH_URL, headers=headers, json=payload)

    print("WhatsApp:", r.status_code)


# ================= STATE =================

def set_state(user, step, service=None):

    cursor.execute(
        "INSERT OR REPLACE INTO state VALUES(?,?,?)",
        (user, step, service)
    )

    conn.commit()


def get_state(user):

    cursor.execute(
        "SELECT step,service FROM state WHERE user=?",
        (user,)
    )

    r = cursor.fetchone()

    return r if r else (None, None)


def clear_state(user):

    cursor.execute(
        "DELETE FROM state WHERE user=?",
        (user,)
    )

    conn.commit()


# ================= SHIPMOZO =================

def shipmozo_data(awb):

    url=f"https://webparex.in/public/api/customer/btp/track-order?tracking_number={awb}&public_key=&type=awb_number&from=WEB"

    data=requests.get(url,timeout=15).json()

    info=data["data"][0]

    latest=info["scan"][0]

    msg=f"""
📦 *Shipment Tracking*

🚚 Courier : {info['courier']}
📍 Status : {info['current_status']}
📅 Expected : {info['expected_delivery_date']}

━━━━━━━━━━━━━━

🕒 {latest['date']} {latest['time']}
📍 {latest['location']}
✅ {latest['status'].strip()}
"""

    update=latest["status"]

    delivered="DELIVERED" in latest["status"].upper()

    return msg,update,delivered


# ================= DELHIVERY =================

def delhivery_data(awb):

    headers={
        "Origin":"https://www.delhivery.com",
        "Referer":"https://www.delhivery.com/",
        "User-Agent":"Mozilla/5.0"
    }

    url=f"https://dlv-api.delhivery.com/v3/unified-tracking-new?wbn={awb}"

    data=requests.get(url,headers=headers,timeout=15).json()

    info=data["data"][0]

    status=info["status"]

    msg=f"""
📦 *Delhivery Tracking*

🚛 AWB : {awb}
📍 Status : {status['status']}
📝 {status['instructions']}

📅 Delivery :
{info.get('deliveryDate','Updating')}
"""

    update=status["instructions"]

    delivered=status["status"]=="DELIVERED"

    return msg,update,delivered


# ================= WEBHOOK VERIFY =================

@app.get("/webhook")
async def verify(request:Request):

    qp=request.query_params

    if qp.get("hub.verify_token")==VERIFY_TOKEN:
        return int(qp.get("hub.challenge"))

    return "error"


# ================= RECEIVE =================

@app.post("/webhook")
async def receive(request:Request):

    data=await request.json()

    try:
        msg=data["entry"][0]["changes"][0]["value"]["messages"][0]
    except:
        return {"ok":True}

    sender=msg["from"]
    text=msg["text"]["body"].lower().strip()

    step,service=get_state(sender)

    print("Incoming:",text)


# -------- TRACK --------

    if text=="track":

        set_state(sender,"choose")

        send_whatsapp_message(sender,
"""📦 *Start Tracking*

Choose courier:

🚚 shipmozo
🚛 delhivery
""")

        return {"ok":True}


# -------- SERVICE --------

    if step=="choose":

        if text not in ["shipmozo","delhivery"]:

            send_whatsapp_message(sender,"❌ Choose shipmozo or delhivery")
            return {"ok":True}

        set_state(sender,"awb",text)

        send_whatsapp_message(
            sender,
            f"✅ *{text.title()} Selected*\n\n📦 Send Tracking Number"
        )

        return {"ok":True}


# -------- ADD AWB --------

    if step=="awb":

        awb=text

        cursor.execute(
            "INSERT INTO tracking VALUES(?,?,?,?)",
            (sender,awb,service,"")
        )

        conn.commit()

        clear_state(sender)

        send_whatsapp_message(
            sender,
            "⏳ Fetching shipment details..."
        )

        try:

            if service=="shipmozo":
                msg,update,_=shipmozo_data(awb)
            else:
                msg,update,_=delhivery_data(awb)

            cursor.execute(
                "UPDATE tracking SET last_update=? WHERE awb=?",
                (update,awb)
            )

            conn.commit()

            send_whatsapp_message(sender,msg)

        except Exception as e:

            send_whatsapp_message(
                sender,
                "⚠️ Unable to fetch tracking right now."
            )

        return {"ok":True}


# -------- LIST --------

    if text=="list":

        cursor.execute(
            "SELECT awb,service FROM tracking WHERE user=?",
            (sender,)
        )

        rows=cursor.fetchall()

        if not rows:
            send_whatsapp_message(sender,"📭 No active shipments.")
            return {"ok":True}

        msg="📦 *Active Shipments*\n\n"

        for r in rows:
            msg+=f"• {r[0]} ({r[1]})\n"

        send_whatsapp_message(sender,msg)

        return {"ok":True}


# -------- HISTORY --------

    if text.startswith("history"):

        parts=text.split()

        if len(parts)<2:

            send_whatsapp_message(
                sender,
                "Usage:\n history AWB"
            )
            return {"ok":True}

        awb=parts[1]

        cursor.execute(
            "SELECT service FROM tracking WHERE awb=?",
            (awb,)
        )

        r=cursor.fetchone()

        if not r:
            send_whatsapp_message(sender,"❌ AWB not tracked.")
            return {"ok":True}

        service=r[0]

        try:

            if service=="shipmozo":

                url=f"https://webparex.in/public/api/customer/btp/track-order?tracking_number={awb}&public_key=&type=awb_number&from=WEB"

                scans=requests.get(url).json()["data"][0]["scan"]

                msg="📜 *Shipment History*\n\n"

                for s in reversed(scans):

                    msg+=(
f"""🕒 {s['date']} {s['time']}
📍 {s['location']}
✅ {s['status']}

────────────
""")

                send_whatsapp_message(sender,msg)

        except:

            send_whatsapp_message(sender,"⚠️ Could not fetch history.")

        return {"ok":True}

    return {"ok":True}


# ================= AUTO CHECK =================

def check_updates():

    cursor.execute("SELECT user,awb,service,last_update FROM tracking")

    for user,awb,service,last in cursor.fetchall():

        try:

            if service=="shipmozo":
                _,update,delivered=shipmozo_data(awb)
            else:
                _,update,delivered=delhivery_data(awb)

            if update!=last:

                send_whatsapp_message(
                    user,
                    f"🚚 Update\n📦 {awb}\n📍 {update}"
                )

                cursor.execute(
                    "UPDATE tracking SET last_update=? WHERE awb=?",
                    (update,awb)
                )

                conn.commit()

            if delivered:

                send_whatsapp_message(
                    user,
                    f"✅ Delivered\n📦 {awb}"
                )

                cursor.execute(
                    "DELETE FROM tracking WHERE awb=?",
                    (awb,)
                )

                conn.commit()

        except Exception as e:
            print(e)


scheduler=BackgroundScheduler()
scheduler.add_job(check_updates,"interval",minutes=20)
scheduler.start()


@app.get("/")
def home():
    return {"running":True}
