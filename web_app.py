import json, os, requests
from datetime import datetime
from flask import Flask, jsonify, render_template_string, request, send_file, abort

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

    msg = (
        "💰 PAYMENT RECEIVED\n\n"
        f"Order: {order['id']}\n"
        f"User: {order.get('name', '-')}\n"
        f"Quantity: {order.get('qty', '-')}\n"
        f"Amount: {order['total_price']}$\n"
        f"Network: {order.get('wallet_label', '-')}\n\n"
        f"TXID:\n{order['txid']}"
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        requests.post(
            url,
            json={"chat_id": ADMIN_ID, "text": msg},
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


def get_qr_path(wallet_key):
    mapping = {
        "wallet_2": "photo2.jpg",
        "wallet_3": "photo3.jpg",
        "wallet_4": "photo4.jpg",
        "wallet_5": "photo5.jpg",
    }
    return mapping.get(wallet_key, "photo3.jpg")


HTML = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Payment</title>
<style>
body{font-family:Arial;padding:20px;max-width:560px;margin:auto;background:#f7f7f7}
.card{background:#fff;padding:18px;border-radius:16px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
.big{font-size:24px;font-weight:bold;margin:8px 0}
.mono{font-family:monospace;word-break:break-all;background:#f1f1f1;padding:12px;border-radius:8px}
.warn{background:#fff3cd;padding:12px;border-radius:10px;margin:12px 0}
.small{color:#666;font-size:14px}
.btns{display:grid;gap:10px;margin:14px 0}
.btns a, .action-btn{display:block;text-decoration:none;padding:12px;border-radius:10px;background:#111;color:#fff;text-align:center;border:none;font-size:15px;cursor:pointer}
.wallet-grid{display:grid;gap:10px;margin:12px 0}
.wallet-grid a{text-decoration:none;padding:12px;border-radius:10px;background:#e9ecef;color:#111;text-align:center;font-weight:bold}
.wallet-grid a.active{background:#111;color:#fff}
.qr-wrap{text-align:center;margin:18px 0}
.qr-wrap img{max-width:260px;width:100%;border-radius:12px;border:1px solid #ddd;background:#fff}
.ok{background:#d1e7dd;padding:12px;border-radius:10px;margin:12px 0;display:none}
</style>
<script>
async function refresh(){
  let r = await fetch("/api/status/{{order.id}}");
  let d = await r.json();
  document.getElementById("status").innerText = d.status;
  document.getElementById("timer").innerText = d.time_left;
  if(d.status === "paid"){
    document.getElementById("paidBox").style.display = "block";
  }
}

function copyAddress(){
  const text = document.getElementById("addr").innerText;
  navigator.clipboard.writeText(text).then(()=>{
    alert("Address copied");
  });
}

async function completePayment(){
  let r = await fetch("/api/complete-payment/{{order.id}}", {
    method: "POST"
  });

  let d = await r.json();

  document.getElementById("status").innerText = d.status;
  document.getElementById("timer").innerText = d.time_left;

  if(d.status === "paid"){
    document.getElementById("paidBox").style.display = "block";
    alert("Payment Completed ✅");
  }else if(d.status === "expired"){
    alert("Order expired");
  }else{
    alert("Payment not Completed yet. Please wait for blockchain confirmation.");
  }
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

<div id="paidBox" class="ok">
Payment Completed successfully ✅
</div>

<h3>Select Wallet</h3>
<div class="wallet-grid">
  <a class="{{ 'active' if wallet_key=='wallet_3' else '' }}" href="/pay/{{order.id}}?wallet=wallet_3">LTC</a>
  <a class="{{ 'active' if wallet_key=='wallet_4' else '' }}" href="/pay/{{order.id}}?wallet=wallet_4">BNB</a>
  <a class="{{ 'active' if wallet_key=='wallet_2' else '' }}" href="/pay/{{order.id}}?wallet=wallet_2">USDT TRC20</a>
  <a class="{{ 'active' if wallet_key=='wallet_5' else '' }}" href="/pay/{{order.id}}?wallet=wallet_5">USDT BEP20</a>
</div>

<div class="qr-wrap">
  <img src="/qr/{{wallet_key}}" alt="QR Code">
</div>

<div class="btns">
  <a href="/qr/{{wallet_key}}?download=1">⬇️ Download QR</a>
  <button class="action-btn" onclick="copyAddress()">📋 Copy Address</button>
  <button class="action-btn" onclick="completePayment()">✅ COMPLETE PAYMENT</button>
</div>

<p>Network:</p>
<pre>{{wallet.label}}</pre>

<p>Address:</p>
<pre class="mono" id="addr">{{wallet.address}}</pre>
</div>
</body>
</html>
"""


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/qr/<wallet_key>")
def qr_image(wallet_key):
    path = get_qr_path(wallet_key)
    if not os.path.exists(path):
        abort(404)

    download = request.args.get("download")
    if download == "1":
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))
    return send_file(path)


@app.get("/pay/<order_id>")
def pay(order_id):
    db = load_db()
    order = db["requests"].get(str(order_id))

    if not order:
        return "Order not found", 404

    wallet_key = request.args.get("wallet") or order.get("wallet_key") or "wallet_3"
    if wallet_key not in db["payment_wallets"]:
        wallet_key = "wallet_3"

    wallet = db["payment_wallets"][wallet_key]

    order["wallet_key"] = wallet_key
    order["wallet_label"] = wallet["label"]
    order["wallet_address"] = wallet["address"]

    db["requests"][str(order_id)] = order
    save_db(db)

    return render_template_string(
        HTML,
        order=order,
        wallet=wallet,
        wallet_key=wallet_key,
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


@app.post("/api/complete-payment/<order_id>")
def complete_payment(order_id):
    db = load_db()
    order = db["requests"].get(str(order_id))

    if not order:
        return jsonify({"status": "not_found", "time_left": "-"})

    if time_left(order) == "Expired" and order["status"] == "pending_payment":
        order["status"] = "expired"
        db["requests"][str(order_id)] = order
        save_db(db)
        return jsonify({"status": order["status"], "time_left": time_left(order)})

    if order["status"] == "pending_payment":
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
