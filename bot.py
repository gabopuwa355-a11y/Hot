import json, os, random, io
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
BASE_WEB_URL = os.getenv("BASE_WEB_URL", "https://your-app.up.railway.app").rstrip("/")

DB_FILE = "store_db.json"

SERVICE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SCOPES = ["https://www.googleapis.com/auth/drive"]

DRIVE_FILE_ID = None

QTY_LIST = [1, 10, 50, 100, 200, 300, 500, 1000, 3000]

PDF_DIR = "slips"
os.makedirs(PDF_DIR, exist_ok=True)

DEFAULT_DB = {
    "prices": {"disable": 0.299, "enable": 0.349},
    "users": {},
    "requests": {},
    "next_request_id": 1,
    "payment_wallets": {
        "wallet_2": {"label": "USDT (TRC20)", "address": "YOUR_TRC20_ADDRESS"},
        "wallet_3": {"label": "LTC", "address": "YOUR_LTC_ADDRESS"},
        "wallet_4": {"label": "BNB Smart Chain (BNB)", "address": "YOUR_BNB_ADDRESS"},
        "wallet_5": {"label": "USDT (BEP20)", "address": "YOUR_BEP20_ADDRESS"}
    }
}


def load_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_DB, f, indent=2, ensure_ascii=False)
        return json.loads(json.dumps(DEFAULT_DB))
    with open(DB_FILE, "r", encoding="utf-8") as f:
        db = json.load(f)

    changed = False
    for k, v in DEFAULT_DB.items():
        if k not in db:
            db[k] = v
            changed = True
    for k, v in DEFAULT_DB["payment_wallets"].items():
        if k not in db["payment_wallets"]:
            db["payment_wallets"][k] = v
            changed = True

    if changed:
        save_db(db)

    return db


def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def drive_service():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(SERVICE_JSON),
        scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def drive_backup():
    service = drive_service()
    media = MediaFileUpload(DB_FILE, mimetype="application/json")

    service.files().update(
        fileId=DRIVE_FILE_ID,
        media_body=media
    ).execute()


def drive_restore():
    service = drive_service()
    request = service.files().get_media(fileId=DRIVE_FILE_ID)

    fh = io.FileIO(DB_FILE, "wb")
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while done is False:
        _, done = downloader.next_chunk()


def ensure_user(user):
    db = load_db()
    uid = str(user.id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "id": user.id,
            "name": user.full_name,
            "username": user.username or "",
            "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_db(db)


def user_menu():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("BUY")], [KeyboardButton("HISTORY")]],
        resize_keyboard=True
    )


def admin_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("REQUESTS"), KeyboardButton("ALL USER")],
        [KeyboardButton("SET PRICE"), KeyboardButton("SET WALLET 3 (LTC)")],
        [KeyboardButton("SET WALLET 4 (BNB)"), KeyboardButton("SET WALLET 2 (TRC20)")],
        [KeyboardButton("SET WALLET 5 (BEP20)")],
        [KeyboardButton("📊 DASHBOARD")],
        [KeyboardButton("☁️ BACKUP DRIVE"), KeyboardButton("⬇️ RESTORE DRIVE")],
        [KeyboardButton("📎 SET DRIVE FILE ID")]
    ], resize_keyboard=True)


def item_text(q):
    return f"{q} account" if q == 1 else f"{q} accounts"


def format_price(x):
    return f"{float(x):.3f}$"


def add_markup(base):
    p = random.uniform(0.3, 0.5)
    return round(base * (1 + p / 100), 6), round(p, 4)


def build_shop(mode="disable", qty=1):
    left = "✓ 📕 DISABLE 2FA ✓" if mode == "disable" else "📕 DISABLE 2FA"
    right = "✓ 📗 ENABLE 2FA ✓" if mode == "enable" else "📗 ENABLE 2FA "

    rows = [[
        InlineKeyboardButton(left, callback_data="mode:disable"),
        InlineKeyboardButton(right, callback_data="mode:enable")
    ]]

    for q in QTY_LIST:
        txt = item_text(q)
        if q == qty:
            txt = f"✓ {txt} ✓"
        rows.append([InlineKeyboardButton(txt, callback_data=f"qty:{q}")])

    rows.append([InlineKeyboardButton("💳 CREATE PAYMENT PAGE", callback_data="create_order")])

    return InlineKeyboardMarkup(rows)


def generate_slip_pdf(order):
    filename = os.path.join(PDF_DIR, f"slip_{order['id']}.pdf")

    c = canvas.Canvas(filename, pagesize=A4)
    y = 800

    def line(txt):
        nonlocal y
        c.drawString(50, y, str(txt))
        y -= 20

    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "PAYMENT SLIP")
    y -= 30

    c.setFont("Helvetica", 11)

    line(f"Order ID: {order.get('id', '-')}")
    line(f"User: {order.get('name', '-')}")
    line(f"Username: @{order.get('username', '')}" if order.get("username") else "Username: -")
    line(f"Quantity: {order.get('qty', '-')}")
    line(f"Base Amount: {format_price(order.get('base_total', 0))}")
    line(f"Extra Added: {order.get('markup_percent', 0)}%")
    line(f"Final Amount: {format_price(order.get('total_price', 0))}")
    line(f"TXID: {order.get('txid', '-')}")
    line(f"Status: {order.get('status', '-')}")
    line(f"Created At: {order.get('created_at', '-')}")
    line(f"Completed At: {order.get('completed_at', '-')}")
    line(f"Delivered File: {order.get('delivered_file_name', '-')}")

    c.save()
    return filename


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(
        "WELCOME\n\nChoose an option:",
        reply_markup=user_menu()
    )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "Admin Menu",
        reply_markup=admin_menu()
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    db = load_db()
    state = context.user_data.setdefault("shop", {"mode": "disable", "qty": 1})
    data = q.data

    if data.startswith("mode:"):
        state["mode"] = data.split(":")[1]
        await q.edit_message_text(
            "Choose quantity:",
            reply_markup=build_shop(state["mode"], state["qty"])
        )
        return

    if data.startswith("qty:"):
        state["qty"] = int(data.split(":")[1])
        await q.edit_message_text(
            "Choose quantity:",
            reply_markup=build_shop(state["mode"], state["qty"])
        )
        return

    if data.startswith("complete_"):
        order_id = data.split("_", 1)[1]
        order = db["requests"].get(order_id)

        if not order:
            await q.edit_message_text("Order not found")
            return

        context.user_data["await_delivery_file_for"] = order_id

        await q.edit_message_text(
            f"Send delivery file now for Order {order_id}"
        )
        return

    if data == "create_order":
        req_id = str(db["next_request_id"])
        db["next_request_id"] += 1

        base = float(db["prices"][state["mode"]]) * state["qty"]
        total, pct = add_markup(base)
        now = datetime.now()

        db["requests"][req_id] = {
            "id": req_id,
            "user_id": q.from_user.id,
            "name": q.from_user.full_name,
            "username": q.from_user.username or "",
            "mode": state["mode"],
            "qty": state["qty"],
            "base_total": round(base, 6),
            "markup_percent": pct,
            "total_price": total,
            "status": "pending_payment",
            "wallet_key": "wallet_3",
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "expires_at": (now + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        }

        save_db(db)

        url = f"{BASE_WEB_URL}/pay/{req_id}"

        txt = (
            f"Order #{req_id}\n"
            f"Type: {state['mode']}\n"
            f"Quantity: {item_text(state['qty'])}\n"
            f"Base Amount: {format_price(base)}\n"
            f"Extra Added: {pct}%\n"
            f"Final Exact Amount: {format_price(total)}\n"
            f"Timeout: 30 minutes"
        )

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 PAY NOW", url=url)]])
        await q.edit_message_text(txt, reply_markup=kb)
        return


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    order_id = context.user_data.get("await_delivery_file_for")
    if not order_id:
        return

    db = load_db()
    order = db["requests"].get(order_id)

    if not order:
        await update.message.reply_text("Order not found")
        return

    doc = update.message.document

    await context.bot.send_document(
        chat_id=order["user_id"],
        document=doc.file_id,
        caption="✅ Your order has been completed successfully.\n\nYour delivery file is attached below."
    )

    order["status"] = "completed"
    order["delivered_file_name"] = doc.file_name or ""
    order["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    slip_file = generate_slip_pdf(order)
    order["slip_file"] = slip_file

    db["requests"][order_id] = order
    save_db(db)

    with open(slip_file, "rb") as f:
        await context.bot.send_document(
            chat_id=order["user_id"],
            document=f,
            caption="📄 Payment slip attached."
        )

    with open(slip_file, "rb") as f:
        await context.bot.send_document(
            chat_id=ADMIN_ID,
            document=f,
            caption=f"📄 Slip saved for Order {order_id}"
        )

    context.user_data.pop("await_delivery_file_for", None)

    await update.message.reply_text(
        f"✅ Order {order_id} completed.\nSlip sent to user and saved for admin."
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global DRIVE_FILE_ID

    text = (update.message.text or "").strip()
    user = update.effective_user
    db = load_db()

    if user.id != ADMIN_ID:
        if text == "BUY":
            context.user_data.setdefault("shop", {"mode": "disable", "qty": 1})
            await update.message.reply_text("Choose quantity:", reply_markup=build_shop())
            return

        if text == "HISTORY":
            orders = [r for r in db["requests"].values() if r["user_id"] == user.id]

            if not orders:
                await update.message.reply_text("No buying history found.", reply_markup=user_menu())
                return

            orders.sort(key=lambda x: int(x["id"]))
            msg = "YOUR BUYING HISTORY\n\n"

            for r in orders[-10:]:
                msg += (
                    f"Order #{r['id']} | {r['qty']} accounts | "
                    f"{format_price(r['total_price'])} | {r['status']}\n"
                )

            await update.message.reply_text(msg, reply_markup=user_menu())
            return

        return

    if text == "📊 DASHBOARD":
        total_orders = 0
        paid_orders = 0
        expired_orders = 0
        revenue = 0.0

        for o in db["requests"].values():
            total_orders += 1

            if o["status"] in ["paid", "completed"]:
                paid_orders += 1
                revenue += float(o["total_price"])

            if o["status"] == "expired":
                expired_orders += 1

        msg = f"""
📊 ADMIN DASHBOARD

Total Orders: {total_orders}

Paid Orders: {paid_orders}

Expired Orders: {expired_orders}

Total Revenue: {round(revenue,3)}$
"""
        await update.message.reply_text(msg)
        return

    if text == "REQUESTS":
        found = False

        for order in db["requests"].values():
            if order["status"] == "paid":
                found = True

                msg = f"""
💰 PAID ORDER

Order: {order['id']}
User: {order['name']}
Quantity: {order['qty']}

Amount: {order['total_price']}$
TXID: {order.get('txid','-')}
"""

                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "✅ COMPLETE ORDER",
                        callback_data=f"complete_{order['id']}"
                    )
                ]])

                await update.message.reply_text(msg, reply_markup=kb)

        if not found:
            await update.message.reply_text("No paid orders")

        return

    if text == "ALL USER":
        lines = ["ALL USER\n"]
        for uid, info in list(db["users"].items())[:100]:
            lines.append(f"{uid} | {info['name']} | @{info['username']}")
        await update.message.reply_text("\n".join(lines), reply_markup=admin_menu())
        return

    if text == "SET PRICE":
        context.user_data["await_price_mode"] = True
        await update.message.reply_text("Send mode name:\n\nenable\ndisable")
        return

    if context.user_data.get("await_price_mode"):
        mode = text.lower()
        if mode not in ["enable", "disable"]:
            await update.message.reply_text("Invalid mode.\nSend: enable or disable")
            return

        context.user_data.pop("await_price_mode")
        context.user_data["set_price_for"] = mode
        await update.message.reply_text(f"Send new USD price for {mode}\n\nExample: 0.299")
        return

    if context.user_data.get("set_price_for"):
        mode = context.user_data.pop("set_price_for")
        try:
            db["prices"][mode] = float(text)
            save_db(db)
            await update.message.reply_text("Price updated.", reply_markup=admin_menu())
        except Exception:
            await update.message.reply_text("Invalid price.", reply_markup=admin_menu())
        return

    wallet_map = {
        "SET WALLET 3 (LTC)": "wallet_3",
        "SET WALLET 4 (BNB)": "wallet_4",
        "SET WALLET 2 (TRC20)": "wallet_2",
        "SET WALLET 5 (BEP20)": "wallet_5"
    }

    if text in wallet_map:
        context.user_data["await_wallet"] = wallet_map[text]
        await update.message.reply_text("Send new wallet address:")
        return

    if context.user_data.get("await_wallet"):
        key = context.user_data.pop("await_wallet")
        db["payment_wallets"][key]["address"] = text
        save_db(db)
        await update.message.reply_text("Wallet updated.", reply_markup=admin_menu())
        return

    if text == "📎 SET DRIVE FILE ID":
        context.user_data["await_drive_id"] = True
        await update.message.reply_text("Send Drive File ID")
        return

    if context.user_data.get("await_drive_id"):
        DRIVE_FILE_ID = text
        context.user_data["await_drive_id"] = False
        await update.message.reply_text("Drive File ID saved")
        return

    if text == "☁️ BACKUP DRIVE":
        drive_backup()
        await update.message.reply_text("Drive backup complete")
        return

    if text == "⬇️ RESTORE DRIVE":
        drive_restore()
        await update.message.reply_text("Drive restore complete")
        return


async def daily_backup(context):
    try:
        drive_backup()
        await context.bot.send_message(
            ADMIN_ID,
            "Daily Google Drive backup completed"
        )
    except Exception as e:
        await context.bot.send_message(
            ADMIN_ID,
            f"Backup error: {e}"
        )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.job_queue.run_repeating(
        daily_backup,
        interval=86400,
        first=60
    )

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
                        
