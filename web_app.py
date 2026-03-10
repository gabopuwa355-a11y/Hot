import json, os, requests
from datetime import datetime
from flask import Flask, jsonify, render_template_string

DB_FILE = "store_db.json"

BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = os.getenv("ADMIN_ID", "")

app = Flask(__name__)


def load_db():
    if not os.path.exists(DB_FILE):
        return {"requests": {}, "payment_wallets": {}}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def time_left(order):
    try:
        exp = datetime.strptime(order["expires_at"], "%Y-%m-%d %H:%M:%S")
        delta = exp - datetime.now()
        sec = int(delta.total_seconds())
        if sec <= 0:
            return "Expired"
        return f"{sec//60:02d}:{sec%60:02d}"
    except Exception:
        return "-"


def tx_used(db, tx):
    for order in db["requests"].values():
        if order.get("txid") == tx:
            return True
    return False


def amount_match(value, amount):
    return abs(float(value) - float(amount)) < 0.001


def notify_admin(order):
    if not BOT_TOKEN or not ADMIN_ID:
        return

    msg = f"""
💰 PAYMENT RECEIVED

Order: {order['id']}
User: {order.get('name', '-')}

Quantity: {order.get('qty', '-')}
Amount: {order['total_price']}$

TXID:
{order['txid']}
"""

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        requests.post(
            url,
            json={
                "chat_id": ADMIN_ID,
                "text": msg
            },
            timeout=15
        )
    except Exception:
        pass


def check_ltc(address, amount):
    url = f"https://sochain.com/api/v2/address/LTC/{address}"

    try:
        r = requests.get(url, timeout=15).json()
    except Exception:
        return False

    if r.get("status") != "success":
        return False

    for tx in r.get("data", {}).get("txs", []):
        try:
            value = float(tx["value"])
            if amount_match(value, amount):
                return tx["txid"]
        except Exception:
            continue

    return False


def check_bsc_native(address, amount):
    url = (
        f"https://api.bscscan.com/api"
        f"?module=account&action=txlist&address={address}&sort=desc&apikey={BSCSCAN_API_KEY}"
    )

    try:
        r = requests.get(url, timeout=15).json()
    except Exception:
        return False

    if r.get("status") != "1":
        return False

    for tx in r.get("result", []):
        try:
            value = float(tx["value"]) / 10**18
            to_addr = str(tx.get("to", "")).lower()
            if to_addr == address.lower() and amount_match(value, amount):
                return tx["hash"]
        except Exception:
            continue

    return False


def check_bsc_usdt(address, amount):
    url = (
        f"https://api.bscscan.com/api"
        f"?module=account&action=tokentx&address={address}&sort=desc&apikey={BSCSCAN_API_KEY}"
    )

    try:
        r = requests.get(url, timeout=15).json()
    except Exception:
        return False

    if r.get("status") != "1":
        return False

    for tx in r.get("result", []):
        try:
            symbol = str(tx.get("tokenSymbol", "")).upper()
            decimals = int(tx.get("tokenDecimal", "18"))
            value = float(tx["value"]) / (10 ** decimals)
            to_addr = str(tx.get("to", "")).lower()

            if symbol == "USDT" and to_addr == address.lower() and amount_match(value, amount):
                return tx["hash"]
        except Exception:
            continue

    return False


def check_trc20(address, amount):
    url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20?limit=50"
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}

    try:
        r = requests.get(url, headers=headers, timeout=15).json()
    except Exception:
        return False

    if "data" not in r:
        return False

    for tx in r["data"]:
        try:
            val = float(tx["value"]) / 10**6
            to_addr = str(tx.get("to", ""))
            if to_addr == address and amount_match(val, amount):
                return tx["transaction_id"]
        except Exception:
            continue

    return False


def check_payment(order):
    if order["status"] != "pending_payment":
        return False

    wallet = order["wallet_key"]
    address = order["wallet_address"]
    amount = order["total_price"]

    if wallet == "wallet_3":
        return check_ltc(address, amount)

    if wallet == "wallet_4":
        return check_bsc_native(address, amount)

    if wallet == "wallet_5":
        return check_bsc_usdt(address, amount)

    if wallet == "wallet_2":
        return check_trc20(address, amount)

    return False


HTML = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Payment</title>
<style>
body{font-family:Arial;padding:20px;max-width:520px;margin:auto;background:#f7f7f7}
.card{background:#fff;padding:16px;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
.big{font-size:24px;font-weight:bold;margin:8px 0}
.mono{font-family:monospace;word-break:break-all;background:#f1f1f1;padding:10px;border-radius:8px}
.warn{background:#fff3cd;padding:10px;border-radius:8px;margin:10px 0}
.small{color:#666;font-size:14px}
</style>
<script>
async function refresh(){
  let r = await fetch("/api/status/{{order.id}}");
  let d = await r.json();
  document.getElementById("status").innerText = d.status;
  document.getElementById("timer").innerText = d.time_left;
}
setInterval(refresh, 2000);
</script>
</head>
<body>
<div class="card">
<h2>Order {{order.id}}</h2>

<p>Type: {{order.mode}}</p>
<p>Quantity: {{order.qty}} accounts</p>
<p>Base Amount: {{order.base_total}}</p>
<p>Extra Added: {{order.markup_percent}}%</p>
<p class="big">Final Exact Amount: {{order.total_price}}</p>

<p>Status: <b id="status">{{order.status}}</b></p>
<p>Time left: <span id="timer">{{time_left}}</span></p>

<div class="warn">
Send the exact amount only.<br>
If you send less, payment will not be accepted.
</div>

<p>Network:</p>
<pre>{{wallet.label}}</pre>

<p>Address:</p>
<pre class="mono">{{wallet.address}}</pre>
</div>
</body>
</html>
"""


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/pay/<order_id>")
def pay(order_id):
    db = load_db()
    order = db["requests"].get(str(order_id))

    if not order:
        return "Order not found", 404

    wallet_key = order.get("wallet_key")
    if wallet_key not in db["payment_wallets"]:
        wallet_key = "wallet_3"
        order["wallet_key"] = wallet_key

    wallet = db["payment_wallets"][wallet_key]
    order["wallet_address"] = wallet["address"]

    db["requests"][str(order_id)] = order
    save_db(db)

    return render_template_string(
        HTML,
        order=order,
        wallet=wallet,
        time_left=time_left(order)
    )


@app.get("/api/status/<order_id>")
def status(order_id):
    db = load_db()
    order = db["requests"].get(str(order_id))

    if not order:
        return jsonify({"status": "not_found", "time_left": "-"})

    if time_left(order) == "Expired" and order["status"] == "pending_payment":
        order["status"] = "expired"
        db["requests"][str(order_id)] = order
        save_db(db)

    if order["status"] == "pending_payment" and time_left(order) != "Expired":
        tx = check_payment(order)

        if tx and not tx_used(db, tx):
            order["status"] = "paid"
            order["txid"] = tx
            db["requests"][str(order_id)] = order
            save_db(db)
            notify_admin(order)

    return jsonify({
        "status": order["status"],
        "time_left": time_left(order)
    })


def start_web():
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080"))
    )



