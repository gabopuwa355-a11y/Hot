import json, os, requests
from datetime import datetime
from flask import Flask, jsonify, render_template_string, request

DB_FILE="store_db.json"

BSCSCAN_API_KEY=os.getenv("BSCSCAN_API_KEY","")
TRONGRID_API_KEY=os.getenv("TRONGRID_API_KEY","")

app=Flask(__name__)


def load_db():
    if not os.path.exists(DB_FILE):
        return {"requests":{}, "payment_wallets":{}}
    with open(DB_FILE,"r",encoding="utf-8") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE,"w",encoding="utf-8") as f:
        json.dump(db,f,indent=2,ensure_ascii=False)


def time_left(order):

    try:

        exp = datetime.strptime(order["expires_at"], "%Y-%m-%d %H:%M:%S")

        now = datetime.now()

        if now >= exp:

            return "Expired"

        delta = exp - now

        sec = int(delta.total_seconds())

        minutes = sec // 60

        seconds = sec % 60

        return f"{minutes:02d}:{seconds:02d}"

    except:

        return "-"
# =====================================
# AUTO PAYMENT CHECK
# =====================================

def check_ltc(address, amount):

    url=f"https://sochain.com/api/v2/address/LTC/{address}"

    r=requests.get(url).json()

    if r["status"]!="success":
        return False

    for tx in r["data"]["txs"]:
        if float(tx["value"])==float(amount):
            return tx["txid"]

    return False



def check_bsc(address, amount):

    url=f"https://api.bscscan.com/api?module=account&action=txlist&address={address}&apikey={BSCSCAN_API_KEY}"

    r=requests.get(url).json()

    if r["status"]!="1":
        return False

    for tx in r["result"]:

        value=float(tx["value"])/10**18

        if value==float(amount):
            return tx["hash"]

    return False



def check_trc20(address, amount):

    url=f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"

    headers={"TRON-PRO-API-KEY":TRONGRID_API_KEY}

    r=requests.get(url,headers=headers).json()

    if "data" not in r:
        return False

    for tx in r["data"]:

        val=float(tx["value"])/10**6

        if val==float(amount):
            return tx["transaction_id"]

    return False



def check_payment(order):

    wallet=order["wallet_key"]
    address=order["wallet_address"]
    amount=order["total_price"]

    if wallet=="wallet_3":
        return check_ltc(address,amount)

    if wallet=="wallet_4":
        return check_bsc(address,amount)

    if wallet=="wallet_5":
        return check_bsc(address,amount)

    if wallet=="wallet_2":
        return check_trc20(address,amount)

    return False


# =====================================
# WEBSITE
# =====================================

HTML="""
<h2>Order {{order.id}}</h2>

<p>Amount: {{order.total_price}}</p>

<p>Status: <b id="status">{{order.status}}</b></p>

<p>Time left: <span id="timer">{{time_left}}</span></p>

<p>Address:</p>

<pre>{{wallet.address}}</pre>

<script>

async function refresh(){

let r=await fetch("/api/status/{{order.id}}")

let d=await r.json()

document.getElementById("status").innerText=d.status
document.getElementById("timer").innerText=d.time_left

}

setInterval(refresh,2000)

</script>
"""


@app.get("/pay/<order_id>")
def pay(order_id):

    db=load_db()

    order=db["requests"].get(str(order_id))

    if not order:
        return "Order not found"

    wallet_key=request.args.get("wallet") or order.get("wallet_key")

    order["wallet_key"]=wallet_key

    wallet=db["payment_wallets"][wallet_key]

    order["wallet_address"]=wallet["address"]

    db["requests"][str(order_id)]=order

    save_db(db)

    return render_template_string(
        HTML,
        order=order,
        wallet=wallet,
        time_left=time_left(order)
    )


@app.get("/api/status/<order_id>")
def status(order_id):

    db=load_db()

    order=db["requests"].get(str(order_id))

    if not order:
        return jsonify({"status":"not_found","time_left":"-"})

    if order["status"]=="pending_payment":

        tx=check_payment(order)

        if tx:

            order["status"]="paid"

            order["txid"]=tx

            db["requests"][str(order_id)]=order

            save_db(db)

    if time_left(order)=="Expired":
        order["status"]="expired"
        db["requests"][str(order_id)]=order
        save_db(db)

    return jsonify({
        "status":order["status"],
        "time_left":time_left(order)
    })


def start_web():
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT","8080"))
    )