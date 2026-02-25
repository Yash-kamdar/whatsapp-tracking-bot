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

GRAPH_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

# ================= DATABASE =================

conn = sqlite3.connect("tracking.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tracking(
user TEXT,
awb TEXT PRIMARY KEY,
service TEXT,
last_status TEXT
)
""")

conn.commit()

# ================= WHATSAPP SEND =================

def send_text(user, text):

    payload = {
        "messaging_product": "whatsapp",
        "to": user,
        "type": "text",
        "text": {"body": text}
    }

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    r = requests.post(GRAPH_URL, json=payload, headers=headers)

    print("SEND:", r.status_code, r.text)


def send_buttons(user, text, buttons):

    payload = {
        "messaging_product": "whatsapp",
        "to": user,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": text},
            "action": {"buttons": buttons}
        }
    }

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    r = requests.post(GRAPH_URL, json=payload, headers=headers)

    print("BUTTON:", r.status_code, r.text)


# ================= COURIER APIs =================

def delhivery_tracking(awb):

    url=f"https://track.delhivery.com/api/v1/packages/json/?waybill={awb}"

    try:
        r=requests.get(url)
        data=r.json()

        scans=data["ShipmentData"][0]["Shipment"]["Scans"]

        history=[]

        for s in scans:
            history.append(
                f"📍 {s['ScanDetail']['ScannedLocation']}\n"
                f"✅ {s['ScanDetail']['Scan']}\n"
                f"🕒 {s['ScanDetail']['ScanDateTime']}"
            )

        latest=scans[-1]["ScanDetail"]["Scan"]

        return latest,"\n\n".join(history)

    except:
        return "Unknown","Tracking unavailable"


def shipmozo_tracking(awb):

    # placeholder (replace api if needed)
    return "In Transit","Shipmozo tracking active"


# ================= MENU =================

def start_tracking(user):

    buttons = [
        {
            "type": "reply",
            "reply": {"id": "shipmozo", "title": "🚚 Shipmozo"}
        },
        {
            "type": "reply",
            "reply": {"id": "delhivery", "title": "📦 Delhivery"}
        }
    ]

    send_buttons(
        user,
        "📦 *Start Shipment Tracking*\n\nChoose courier:",
        buttons
    )


# ================= LIST =================

def list_shipments(user):

    cursor.execute("SELECT awb,service FROM tracking WHERE user=?",(user,))
    rows=cursor.fetchall()

    if not rows:
        send_text(user,"📭 No active shipments.")
        return

    msg="📦 *Active Shipments*\n\n"

    for r in rows:
        msg+=f"• {r[0]} ({r[1]})\n"

    send_text(user,msg)


# ================= HISTORY =================

def history(user,awb):

    cursor.execute("SELECT service FROM tracking WHERE awb=?",(awb,))
    row=cursor.fetchone()

    if not row:
        send_text(user,"❌ AWB not found.")
        return

    service=row[0]

    if service=="delhivery":
        _,hist=delhivery_tracking(awb)
    else:
        _,hist=shipmozo_tracking(awb)

    send_text(user,f"📜 Tracking History\n\n{hist}")


# ================= TRACK CHECK =================

def check_updates():

    cursor.execute("SELECT user,awb,service,last_status FROM tracking")
    rows=cursor.fetchall()

    for user,awb,service,last_status in rows:

        if service=="delhivery":
            status,_=delhivery_tracking(awb)
        else:
            status,_=shipmozo_tracking(awb)

        if status!=last_status:

            send_text(
                user,
                f"📦 Update\n\nAWB: {awb}\nStatus: {status}"
            )

            cursor.execute(
                "UPDATE tracking SET last_status=? WHERE awb=?",
                (status,awb)
            )

            if "Delivered" in status:
                cursor.execute(
                    "DELETE FROM tracking WHERE awb=?",(awb,)
                )

            conn.commit()


scheduler=BackgroundScheduler()
scheduler.add_job(check_updates,"interval",minutes=10)
scheduler.start()


# ================= WEBHOOK VERIFY =================

@app.get("/webhook")
async def verify(request:Request):

    params=request.query_params

    if params.get("hub.verify_token")==VERIFY_TOKEN:

        return int(params.get("hub.challenge"))

    return "Error"


# ================= RECEIVE =================

@app.post("/webhook")
async def receive(request:Request):

    data=await request.json()

    try:

        value=data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return {"ok":True}

        msg=value["messages"][0]

        user=msg["from"]

        text=""

        if msg["type"]=="text":
            text=msg["text"]["body"].lower()

        elif msg["type"]=="interactive":
            text=msg["interactive"]["button_reply"]["id"]

        print("Incoming:",text)

    except Exception as e:
        print("Webhook parse error",e)
        return {"ok":True}

    # ===== COMMANDS =====

    if text in ["hi","start","menu"]:
        start_tracking(user)

    elif text=="track":
        start_tracking(user)

    elif text=="list":
        list_shipments(user)

    elif text.startswith("history"):
        parts=text.split()
        if len(parts)==2:
            history(user,parts[1])

    elif text in ["shipmozo","delhivery"]:

        cursor.execute(
            "INSERT OR REPLACE INTO tracking VALUES(?,?,?,?)",
            (user,"waiting_awb",text,"")
        )

        conn.commit()

        send_text(user,"📦 Send Tracking Number")

    else:

        cursor.execute(
            "SELECT service FROM tracking WHERE user=? AND awb='waiting_awb'",
            (user,)
        )

        row=cursor.fetchone()

        if row:

            service=row[0]
            awb=text.strip()

            if service=="delhivery":
                status,history_text=delhivery_tracking(awb)
            else:
                status,history_text=shipmozo_tracking(awb)

            cursor.execute(
                "DELETE FROM tracking WHERE awb='waiting_awb'"
            )

            cursor.execute(
                "INSERT OR REPLACE INTO tracking VALUES(?,?,?,?)",
                (user,awb,service,status)
            )

            conn.commit()

            send_text(
                user,
                f"✅ Tracking Started\n\n"
                f"AWB: {awb}\n"
                f"Courier: {service}\n"
                f"Status: {status}"
            )

            send_text(
                user,
                f"📜 Full Tracking History\n\n{history_text}"
            )

    return {"ok":True}


@app.get("/")
def home():
    return {"status":"running"}
