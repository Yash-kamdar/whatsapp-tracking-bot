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
last_update TEXT,
ofd_sent INTEGER DEFAULT 0
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

    requests.post(GRAPH_URL, headers=headers, json=payload)


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
    cursor.execute("DELETE FROM state WHERE user=?", (user,))
    conn.commit()


# ================= SHIPMOZO =================

def shipmozo_data(awb):

    url=f"https://webparex.in/public/api/customer/btp/track-order?tracking_number={awb}&public_key=&type=awb_number&from=WEB"

    data=requests.get(url,timeout=15).json()

    info=data["data"][0]
    scans=info["scan"]

    latest=scans[0]

    history=""

    for s in reversed(scans):

        history+=(
f"""🕒 {s['date']} {s['time']}
📍 {s['location']}
✅ {s['status'].strip()}

────────────
"""
        )

    message=f"""
📦 *Shipment Tracking*

🚚 Courier : {info['courier']}
📍 Status : {info['current_status']}
📅 Expected : {info['expected_delivery_date']}

━━━━━━━━━━━━━━
📜 *Timeline*

{history}
"""

    update=latest["status"]

    delivered="DELIVERED" in latest["status"].upper()

    ofd="OUT FOR DELIVERY" in latest["status"].upper()

    return message,update,delivered,ofd


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

    history=""

    for state in info["trackingStates"]:

        scans=state.get("scans")

        if scans:
            for s in scans:

                history+=(
f"""🕒 {s.get('scanNslRemark')}
📍 {s.get('scannedLocation')}

────────────
"""
                )

    message=f"""
📦 *Delhivery Shipment*

🚛 AWB : {awb}
📍 Status : {status['status']}
📝 {status['instructions']}

📅 Delivery :
{info.get('deliveryDate','Updating')}

━━━━━━━━━━━━━━
📜 *Timeline*

{history if history else "Updating..."}
"""

    update=status["instructions"]

    delivered=status["status"]=="DELIVERED"

    ofd="OUT FOR DELIVERY" in status["instructions"].upper()

    return message,update,delivered,ofd


# ================= VERIFY =================

@app.get("/webhook")
async def verify(request: Request):

    qp=request.query_params

    if qp.get("hub.verify_token")==VERIFY_TOKEN:
        return int(qp.get("hub.challenge"))

    return "error"


# ================= RECEIVE =================

@app.post("/webhook")
async def receive(request: Request):

    data=await request.json()

    try:
        msg=data["entry"][0]["changes"][0]["value"]["messages"][0]
    except:
        return {"ok":True}

    sender=msg["from"]
    text=msg["text"]["body"].lower().strip()

    step,service=get_state(sender)


# ---------- TRACK ----------

    if text=="track":

        set_state(sender,"choose")

        send_whatsapp_message(sender,
"""📦 *Start Tracking*

Choose courier:

🚚 shipmozo
🚛 delhivery
""")
        return {"ok":True}


# ---------- SERVICE ----------

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


# ---------- ADD AWB ----------

    if step=="awb":

        awb=text

        cursor.execute(
            "SELECT * FROM tracking WHERE user=? AND awb=?",
            (sender,awb)
        )

        if cursor.fetchone():
            send_whatsapp_message(sender,"⚠️ Already tracking this AWB.")
            return {"ok":True}

        cursor.execute(
            "INSERT INTO tracking VALUES(?,?,?,?,0)",
            (sender,awb,service,"")
        )

        conn.commit()
        clear_state(sender)

        send_whatsapp_message(
            sender,
            "⏳ Fetching full shipment history..."
        )

        try:

            if service=="shipmozo":
                msg,update,_,_=shipmozo_data(awb)
            else:
                msg,update,_,_=delhivery_data(awb)

            cursor.execute(
                "UPDATE tracking SET last_update=? WHERE awb=?",
                (update,awb)
            )

            conn.commit()

            send_whatsapp_message(sender,msg)

        except:
            send_whatsapp_message(sender,"⚠️ Could not fetch tracking.")

        return {"ok":True}


# ---------- HISTORY ----------

    if text.startswith("history"):

        parts=text.split()

        if len(parts)<2:
            send_whatsapp_message(sender,"Usage:\nhistory AWB")
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

        if service=="shipmozo":
            msg,_,_,_=shipmozo_data(awb)
        else:
            msg,_,_,_=delhivery_data(awb)

        send_whatsapp_message(sender,msg)

        return {"ok":True}


# ---------- LIST ----------

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

    return {"ok":True}


# ================= AUTO CHECK =================

def check_updates():

    cursor.execute(
        "SELECT user,awb,service,last_update,ofd_sent FROM tracking"
    )

    rows=cursor.fetchall()

    for user,awb,service,last,ofd_sent in rows:

        try:

            if service=="shipmozo":
                _,update,delivered,ofd=shipmozo_data(awb)
            else:
                _,update,delivered,ofd=delhivery_data(awb)

            if update!=last:

                send_whatsapp_message(
                    user,
f"""🚚 *Shipment Update*

📦 {awb}
📍 {update}
"""
                )

                cursor.execute(
                    "UPDATE tracking SET last_update=? WHERE awb=?",
                    (update,awb)
                )

                conn.commit()

            # OUT FOR DELIVERY ALERT
            if ofd and ofd_sent==0:

                send_whatsapp_message(
                    user,
f"""🚚 *Out For Delivery*

📦 {awb}

Your package should arrive today 🎉
"""
                )

                cursor.execute(
                    "UPDATE tracking SET ofd_sent=1 WHERE awb=?",
                    (awb,)
                )

                conn.commit()

            # DELIVERED
            if delivered:

                send_whatsapp_message(
                    user,
f"""✅ *Delivered*

📦 {awb}

Thank you for using tracking bot 🙌
"""
                )

                cursor.execute(
                    "DELETE FROM tracking WHERE awb=?",
                    (awb,)
                )

                conn.commit()

        except Exception as e:
            print("Tracking error:",e)


scheduler=BackgroundScheduler()
scheduler.add_job(check_updates,"interval",minutes=20)
scheduler.start()


@app.get("/")
def home():
    return {"running":True}
