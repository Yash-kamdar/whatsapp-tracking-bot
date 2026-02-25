from fastapi import FastAPI, Request
import requests
import sqlite3
import os
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI()

# ================= ENV =================

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
ACCESS_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# ================= DATABASE =================

conn = sqlite3.connect("tracking.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tracking(
user TEXT,
awb TEXT UNIQUE,
service TEXT,
last_update TEXT,
delivered INTEGER DEFAULT 0
)
""")

conn.commit()

user_state = {}

# ================= WHATSAPP SEND =================

def send_message(to, text):

    url=f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

    headers={
        "Authorization":f"Bearer {ACCESS_TOKEN}",
        "Content-Type":"application/json"
    }

    payload={
        "messaging_product":"whatsapp",
        "to":to,
        "type":"text",
        "text":{"body":text}
    }

    requests.post(url,json=payload,headers=headers)


# ================= BUTTON UI =================

def send_buttons(user,text,buttons):

    url=f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

    payload={
        "messaging_product":"whatsapp",
        "to":user,
        "type":"interactive",
        "interactive":{
            "type":"button",
            "body":{"text":text},
            "action":{"buttons":buttons}
        }
    }

    headers={
        "Authorization":f"Bearer {ACCESS_TOKEN}",
        "Content-Type":"application/json"
    }

    requests.post(url,json=payload,headers=headers)


def main_menu(user):

    buttons=[
        {"type":"reply","reply":{"id":"track","title":"📦 Track"}},
        {"type":"reply","reply":{"id":"list","title":"📋 Shipments"}},
        {"type":"reply","reply":{"id":"history","title":"📜 History"}}
    ]

    send_buttons(
        user,
        "👋 *Shipment Tracker*\nChoose an option:",
        buttons
    )


def courier_menu(user):

    buttons=[
        {"type":"reply","reply":{"id":"shipmozo","title":"🚚 Shipmozo"}},
        {"type":"reply","reply":{"id":"delhivery","title":"🚛 Delhivery"}}
    ]

    send_buttons(
        user,
        "📦 Select Courier",
        buttons
    )


# ================= SHIPMOZO =================

def shipmozo_track(awb):

    url=f"https://webparex.in/public/api/customer/btp/track-order?tracking_number={awb}&public_key=&type=awb_number&from=WEB"

    r=requests.get(url).json()

    scans=r["data"][0]["scan"]

    return scans


# ================= DELHIVERY =================

def delhivery_track(awb):

    url=f"https://dlv-api.delhivery.com/v3/unified-tracking-new?wbn={awb}"

    headers={
        "origin":"https://www.delhivery.com",
        "referer":"https://www.delhivery.com/"
    }

    r=requests.get(url,headers=headers).json()

    scans=[]

    states=r["data"][0]["trackingStates"]

    for s in states:

        if s.get("scans"):

            for scan in s["scans"]:

                scans.append({
                    "status":scan["scanNslRemark"],
                    "location":scan["scannedLocation"]
                })

    return scans


# ================= FORMAT =================

def format_history(service,awb,scans):

    msg=f"📦 *Tracking History*\nAWB: {awb}\nService: {service}\n\n"

    for s in scans[-10:]:

        msg+=f"📍 {s.get('location','')}\n"
        msg+=f"✅ {s.get('status','')}\n"
        msg+="────────────\n"

    return msg


# ================= ADD TRACK =================

def add_tracking(user,awb,service):

    cursor.execute("SELECT * FROM tracking WHERE awb=?",(awb,))
    if cursor.fetchone():
        send_message(user,"⚠️ Already tracking this AWB")
        return

    cursor.execute(
        "INSERT INTO tracking(user,awb,service,last_update) VALUES(?,?,?,?)",
        (user,awb,service,"")
    )

    conn.commit()

    send_message(user,f"✅ Tracking Started\nAWB: {awb}")

    # send FULL history first time
    if service=="shipmozo":
        scans=shipmozo_track(awb)
    else:
        scans=delhivery_track(awb)

    send_message(user,format_history(service,awb,scans))


# ================= HISTORY =================

def history(user,awb):

    cursor.execute("SELECT service FROM tracking WHERE awb=?",(awb,))
    row=cursor.fetchone()

    if not row:
        send_message(user,"❌ AWB not tracked")
        return

    service=row[0]

    if service=="shipmozo":
        scans=shipmozo_track(awb)
    else:
        scans=delhivery_track(awb)

    send_message(user,format_history(service,awb,scans))


# ================= LIST =================

def list_awb(user):

    cursor.execute("SELECT awb,service FROM tracking WHERE delivered=0")

    rows=cursor.fetchall()

    if not rows:
        send_message(user,"📭 No Active Shipments")
        return

    msg="📦 *Active Shipments*\n\n"

    for r in rows:
        msg+=f"• {r[0]} ({r[1]})\n"

    send_message(user,msg)


# ================= AUTO CHECK =================

def check_updates():

    cursor.execute("SELECT user,awb,service,last_update FROM tracking WHERE delivered=0")

    rows=cursor.fetchall()

    for user,awb,service,last in rows:

        try:

            scans=shipmozo_track(awb) if service=="shipmozo" else delhivery_track(awb)

            latest=scans[-1]["status"]

            if latest!=last:

                send_message(
                    user,
                    f"🚚 Update for {awb}\n\n✅ {latest}"
                )

                if "out for delivery" in latest.lower():

                    send_message(user,"🚀 Out For Delivery Today!")

                if "delivered" in latest.lower():

                    cursor.execute(
                        "UPDATE tracking SET delivered=1 WHERE awb=?",
                        (awb,)
                    )

                    send_message(user,"📦 Delivered ✅")

                cursor.execute(
                    "UPDATE tracking SET last_update=? WHERE awb=?",
                    (latest,awb)
                )

                conn.commit()

        except Exception as e:
            print("Tracker error",e)


scheduler=BackgroundScheduler()
scheduler.add_job(check_updates,"interval",minutes=20)
scheduler.start()


# ================= WEBHOOK VERIFY =================

@app.get("/webhook")
def verify(mode:str=None,hub_challenge:str=None,hub_verify_token:str=None):

    if hub_verify_token==VERIFY_TOKEN:
        return hub_challenge

    return "error"


# ================= RECEIVE =================

@app.post("/webhook")
async def receive(req:Request):

    data=await req.json()

    try:
        value=data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return {"ok":True}

        msg=value["messages"][0]

        sender=msg["from"]

        text=None
        button=None

        if msg["type"]=="interactive":
            button=msg["interactive"]["button_reply"]["id"]

        elif msg["type"]=="text":
            text=msg["text"]["body"].lower()

        # ===== MENU =====

        if text in ["hi","hello","menu","start"]:
            main_menu(sender)
            return {"ok":True}

        if button=="track":
            courier_menu(sender)
            user_state[sender]="choose"
            return {"ok":True}

        if button=="shipmozo":
            user_state[sender]="shipmozo"
            send_message(sender,"Send AWB")
            return {"ok":True}

        if button=="delhivery":
            user_state[sender]="delhivery"
            send_message(sender,"Send AWB")
            return {"ok":True}

        if button=="list":
            list_awb(sender)
            return {"ok":True}

        if button=="history":
            send_message(sender,"Send:\nhistory AWB")
            return {"ok":True}

        # ===== HISTORY TEXT =====

        if text and text.startswith("history"):
            awb=text.split(" ")[1]
            history(sender,awb)
            return {"ok":True}

        # ===== ADD AWB =====

        if sender in user_state:

            service=user_state[sender]

            if service in ["shipmozo","delhivery"]:

                add_tracking(sender,text.strip(),service)

                del user_state[sender]

    except Exception as e:
        print("Webhook error:",e)

    return {"ok":True}
