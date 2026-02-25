from fastapi import FastAPI, Request
import requests
import sqlite3
import os
import random
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# ================= DATABASE =================

conn = sqlite3.connect("tracking.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tracking(
user TEXT,
awb TEXT UNIQUE,
service TEXT,
last_status TEXT
)
""")

conn.commit()

# ================= HEADERS =================

USER_AGENTS = [
"Mozilla/5.0 (Windows NT 10.0; Win64)",
"Mozilla/5.0 (Macintosh)",
"Mozilla/5.0 (X11; Linux)"
]

def random_headers():

    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
        "Origin": "https://www.delhivery.com",
        "Referer": "https://www.delhivery.com/"
    }

# ================= WHATSAPP SEND =================

def send_whatsapp(to, message):

    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product":"whatsapp",
        "to":to,
        "type":"text",
        "text":{"body":message}
    }

    r = requests.post(url,headers=headers,json=payload)

    print("WhatsApp:",r.status_code,r.text)

# ================= DELHIVERY =================

def delhivery_tracking(awb):

    try:

        url=f"https://dlv-api.delhivery.com/v3/unified-tracking-new?wbn={awb}"

        r=requests.get(url,headers=random_headers(),timeout=15)

        data=r.json()

        shipment=data["data"]["shipments"][0]

        status=shipment.get("status","In Transit")

        scans=shipment.get("scans",[])

        history=[]

        for s in scans:

            history.append(
f"""📍 {s.get('location','')}
✅ {s.get('status','')}
🕒 {s.get('time','')}"""
            )

        return status,"\n\n".join(history)

    except Exception as e:

        print("Delhivery error",e)
        return "Unknown","Tracking unavailable"


# ================= SHIPMOZO =================

def shipmozo_tracking(awb):

    try:

        url=f"https://shipmozo.com/track/{awb}"

        r=requests.get(url,headers=random_headers(),timeout=15)

        data=r.json()

        events=data.get("tracking_data",[])

        history=[]

        for e in events:

            history.append(
f"""📍 {e.get('location','')}
✅ {e.get('status','')}
🕒 {e.get('date','')}"""
            )

        latest=events[0]["status"] if events else "In Transit"

        return latest,"\n\n".join(history)

    except Exception as e:

        print("Shipmozo error",e)
        return "Unknown","Tracking unavailable"


# ================= ROUTER =================

def tracker(service,awb):

    if service=="delhivery":
        return delhivery_tracking(awb)

    if service=="shipmozo":
        return shipmozo_tracking(awb)

    return "Unknown","Tracking unavailable"


# ================= COMMAND STATE =================

user_state={}

# ================= WEBHOOK VERIFY =================

@app.get("/webhook")
def verify(mode:str=None,challenge:str=None,hub_verify_token:str=None):

    if hub_verify_token==VERIFY_TOKEN:
        return int(challenge)

    return "error"


# ================= RECEIVE MESSAGE =================

@app.post("/webhook")
async def webhook(req:Request):

    data=await req.json()

    try:

        value=data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return {"ok":True}

        msg=value["messages"][0]

        sender=msg["from"]

        text=msg["text"]["body"].lower().strip()

        print("Incoming:",text)

    except:
        return {"ok":True}

# ---------- TRACK ----------

    if text=="track":

        user_state[sender]="choose"

        send_whatsapp(
sender,
"""📦 *Start Tracking*

Choose courier:

shipmozo
delhivery"""
)

        return {"ok":True}

# ---------- COURIER SELECT ----------

    if sender in user_state and user_state[sender]=="choose":

        if text in ["shipmozo","delhivery"]:

            user_state[sender]=text

            send_whatsapp(sender,"📦 Send Tracking Number")

        return {"ok":True}

# ---------- ADD AWB ----------

    if sender in user_state:

        service=user_state[sender]

        awb=text.upper()

        cursor.execute(
        "SELECT * FROM tracking WHERE awb=?",(awb,)
        )

        if cursor.fetchone():

            send_whatsapp(sender,"⚠ Already tracking")

            user_state.pop(sender,None)
            return {"ok":True}

        status,history=tracker(service,awb)

        cursor.execute(
        "INSERT INTO tracking VALUES(?,?,?,?)",
        (sender,awb,service,status)
        )

        conn.commit()

        send_whatsapp(
sender,
f"""✅ Tracking Started

📦 AWB : {awb}
🚚 Courier : {service}

📜 Full History

{history}"""
)

        user_state.pop(sender,None)

        return {"ok":True}

# ---------- LIST ----------

    if text=="list":

        cursor.execute(
        "SELECT awb,service FROM tracking WHERE user=?",
        (sender,)
        )

        rows=cursor.fetchall()

        if not rows:

            send_whatsapp(sender,"No active shipments.")
            return {"ok":True}

        msg="📦 Active Shipments\n\n"

        for r in rows:
            msg+=f"• {r[0]} ({r[1]})\n"

        send_whatsapp(sender,msg)

        return {"ok":True}

# ---------- HISTORY ----------

    if text.startswith("history"):

        parts=text.split()

        if len(parts)<2:
            return {"ok":True}

        awb=parts[1]

        cursor.execute(
        "SELECT service FROM tracking WHERE awb=?",
        (awb,)
        )

        row=cursor.fetchone()

        if not row:

            send_whatsapp(sender,"Shipment not found")
            return {"ok":True}

        service=row[0]

        status,history=tracker(service,awb)

        send_whatsapp(
sender,
f"""📜 Full Tracking History

AWB : {awb}

{history}"""
)

        return {"ok":True}

    return {"ok":True}


# ================= AUTO CHECK UPDATES =================

def check_updates():

    cursor.execute("SELECT user,awb,service,last_status FROM tracking")

    rows=cursor.fetchall()

    for user,awb,service,last in rows:

        status,_=tracker(service,awb)

        if status!=last:

            send_whatsapp(
user,
f"""🚚 Shipment Update

AWB : {awb}

Status : {status}"""
)

            cursor.execute(
            "UPDATE tracking SET last_status=? WHERE awb=?",
            (status,awb)
            )

            if "delivered" in status.lower():

                cursor.execute(
                "DELETE FROM tracking WHERE awb=?",
                (awb,)
                )

            conn.commit()


scheduler=BackgroundScheduler()
scheduler.add_job(check_updates,"interval",minutes=10)
scheduler.start()
