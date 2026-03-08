"""
FINAL PRO BOT - safe generic digital delivery bot

Features
- Clean user UI with dynamic quantity + mode selection
- Advanced admin reply-menu
- Manual payment review flow
- QR payment instruction flow (send_document based, avoids photo errors)
- Google Drive backup / restore for JSON database
- Railway-ready
- Webhook-ready
- Admin completes order by uploading a file
- Safe edit handling for Telegram "Message is not modified"

Install:
    pip install -U python-telegram-bot==20.7 google-api-python-client google-auth google-auth-oauthlib

Required env vars:
    BOT_TOKEN=...
    ADMIN_ID=123456789

Optional env vars:
    WEBHOOK_MODE=1
    WEBHOOK_URL=https://your-app.up.railway.app
    WEBHOOK_PATH=/telegram
    HOST=0.0.0.0
    PORT=8080

    TRC20_ADDRESS=...
    LTC_ADDRESS=...
    BNB_ADDRESS=...
    BEP20_ADDRESS=...

    DB_FILE=store_db.json
    GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
"""

import io
import json
import logging
import os
import re
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from pathlib import Path
portlab.lib.pagesizes import A4
portlab.pdfgen import canvas

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# CONFIG
# =========================================================
TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

WEBHOOK_MODE = os.getenv("WEBHOOK_MODE", "1") == "1"
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))

DB_FILE = os.getenv("DB_FILE", "store_db.json")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

QTY_LIST = [1, 10, 50, 100, 200, 300, 500, 1000, 3000]
TXID_REGEX = re.compile(r"^[A-Za-z0-9]{20,128}$")

# Generic/safe labels for digital delivery
MODE_LABELS = {
    "disable": "📕 Standard Access",
    "enable": "📗 Premium Access",
}
MODE_EMOJI = {
    "disable": "📕",
    "enable": "📗",
}

DEFAULT_DB = {
    "prices": {
        "disable": 0.299,
        "enable": 0.349,
    },
    "users": {},
    "requests": {},
    "next_request_id": 1,
    "payment_wallets": {
        "wallet_2": {
            "label": "USDT (TRC20)",
            "address": os.getenv("TRC20_ADDRESS", "YOUR_TRC20_ADDRESS"),
        },
        "wallet_3": {
            "label": "LTC",
            "address": os.getenv("LTC_ADDRESS", "YOUR_LTC_ADDRESS"),
        },
        "wallet_4": {
            "label": "BNB Smart Chain (BNB)",
            "address": os.getenv("BNB_ADDRESS", "YOUR_BNB_ADDRESS"),
        },
        "wallet_5": {
            "label": "USDT (BEP20)",
            "address": os.getenv("BEP20_ADDRESS", "YOUR_BEP20_ADDRESS"),
        },
    },
    "payment_qr_files": {
        "wallet_2": "photo2.jpg",
        "wallet_3": "photo3.jpg",
        "wallet_4": "photo4.jpg",
        "wallet_5": "photo5.jpg",
    },
    "drive": {
        "file_id": "",
        "service_account_file": SERVICE_ACCOUNT_FILE,
    },
    "settings": {
        "admin_notes": "",
        "quote_validity_minutes": 30,
    },
}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("final_pro_bot")

# =========================================================
# STORAGE HELPERS
# =========================================================
PDF_DIR = Path("payment_slips")
PDF_DIR.mkdir(exist_ok=True)

def generate_single_payment_slip_pdf(req: dict) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = PDF_DIR / f"payment_slip_req_{req['id']}_{timestamp}.pdf"

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    width, height = A4
    y = height - 50

    def line(text, size=10, gap=16):
        nonlocal y
        c.setFont("Helvetica", size)
        c.drawString(40, y, str(text))
        y -= gap

    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, "Payment Slip")
    y -= 25

    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Generated At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    y -= 25

    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, f"Request #{req.get('id', '-')}")
    y -= 20

    line(f"User: {req.get('name', '-')}")
    line(f"Username: @{req.get('username', '-')}" if req.get("username") else "Username: -")
    line(f"User ID: {req.get('user_id', '-')}")
    line(f"Type: {MODE_LABELS.get(req.get('mode', ''), req.get('mode', '-'))}")
    line(f"Accounts: {req.get('qty', '-')}")
    line(f"Price Per Account: {format_price(float(req.get('unit_price', 0)))}")
    line(f"Total Amount: {format_price(float(req.get('total_price', 0)))}")
    line(f"Wallet: {req.get('wallet_label', '-')}")
    line(f"Wallet Address: {req.get('wallet_address', '-')}")
    line(f"TXID: {req.get('txid', '-') or '-'}")
    line(f"Status: {req.get('status', '-')}")
    line(f"Created At: {req.get('created_at', '-')}")
    line(f"Updated At: {req.get('updated_at', '-')}")

    if req.get("delivered_file_name"):
        line(f"Delivered File: {req.get('delivered_file_name')}")

    if req.get("cancel_reason"):
        line(f"Cancel Reason: {req.get('cancel_reason')}")

    y -= 10
    c.line(40, y, width - 40, y)
    y -= 25

    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Delivery Status: COMPLETED")

    c.save()
    return str(pdf_path)
def load_db() -> dict:
    if not os.path.exists(DB_FILE):
        save_db(DEFAULT_DB)
        return DEFAULT_DB
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(data: dict) -> None:
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def ensure_user(user) -> None:
    db = load_db()
    uid = str(user.id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if uid not in db["users"]:
        db["users"][uid] = {
            "id": user.id,
            "name": user.full_name,
            "username": user.username or "",
            "joined_at": now,
            "last_seen_at": now,
            "orders_count": 0,
        }
    else:
        db["users"][uid]["last_seen_at"] = now
    save_db(db)


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def format_price(x: float) -> str:
    return f"{x:.3f}$"


def item_text(qty: int) -> str:
    return f"{qty} account" if qty == 1 else f"{qty} accounts"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# =========================================================
# GOOGLE DRIVE BACKUP / RESTORE
# =========================================================
def get_drive_service():
    db = load_db()
    sa_file = db.get("drive", {}).get("service_account_file", SERVICE_ACCOUNT_FILE)

    credentials = service_account.Credentials.from_service_account_file(
        sa_file,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=credentials)


def drive_backup_store_db():
    db = load_db()
    drive_service = get_drive_service()
    file_id = db.get("drive", {}).get("file_id", "").strip()

    media = MediaFileUpload(DB_FILE, mimetype="application/json", resumable=False)

    if file_id:
        updated = drive_service.files().update(
            fileId=file_id,
            media_body=media,
        ).execute()
        return updated["id"], "updated"

    created = drive_service.files().create(
        body={"name": DB_FILE},
        media_body=media,
        fields="id",
    ).execute()
    new_id = created["id"]
    db["drive"]["file_id"] = new_id
    save_db(db)
    return new_id, "created"


def drive_restore_store_db():
    db = load_db()
    drive_service = get_drive_service()
    file_id = db.get("drive", {}).get("file_id", "").strip()

    if not file_id:
        raise ValueError("Drive file ID is not set.")

    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    with open(DB_FILE, "wb") as f:
        f.write(fh.getvalue())

    return True


# =========================================================
# UI HELPERS
# =========================================================
def top_text(prices: dict, selected_mode: str) -> str:
    current_price = float(prices.get(selected_mode, 0.0))
    return (
        f"The current price is {format_price(current_price)}, but it may change later.\n"
        f"The price depends on the number of accounts in the system.\n\n"
        f"You will receive your delivery after payment verification."
    )


def build_main_menu(prices: dict, selected_mode: str = "disable", selected_qty: int = 1):
    unit_price = float(prices.get(selected_mode, 0.0))
    total_price = unit_price * selected_qty

    left_text = MODE_LABELS["disable"]
    right_text = MODE_LABELS["enable"]

    if selected_mode == "disable":
        left_text = f"✓ {left_text} ✓"
    elif selected_mode == "enable":
        right_text = f"✓ {right_text} ✓"

    keyboard = [
        [
            InlineKeyboardButton(left_text, callback_data="mode:disable"),
            InlineKeyboardButton(right_text, callback_data="mode:enable"),
        ]
    ]

    for qty in QTY_LIST:
        text = item_text(qty)
        if qty == selected_qty:
            text = f"✓ {text} ✓"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"qty:{qty}")])

    pay_emoji = MODE_EMOJI.get(selected_mode, "💳")
    pay_text = f"{pay_emoji} PAY {format_price(total_price)} for {item_text(selected_qty)}"
    keyboard.append([InlineKeyboardButton(pay_text, callback_data="go_payment")])

    return top_text(prices, selected_mode), InlineKeyboardMarkup(keyboard)


def build_payment_menu(req_id: str):
    keyboard = [
        [InlineKeyboardButton("💵 USDT (TRC20)", callback_data=f"paywallet:{req_id}:wallet_2")],
        [InlineKeyboardButton("🪙 LTC", callback_data=f"paywallet:{req_id}:wallet_3")],
        [InlineKeyboardButton("🟡 BNB Smart Chain", callback_data=f"paywallet:{req_id}:wallet_4")],
        [InlineKeyboardButton("💵 USDT (BEP20)", callback_data=f"paywallet:{req_id}:wallet_5")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"back_order:{req_id}")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_i_paid_menu(req_id: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I HAVE PAID", callback_data=f"ihavepaid:{req_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"back_wallets:{req_id}")],
    ])


def request_action_menu(req_id: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ CANCELED REQUEST", callback_data=f"admin:reqcancel:{req_id}")],
        [InlineKeyboardButton("✅ COMPLETE ORDER", callback_data=f"admin:reqcomplete:{req_id}")],
        [InlineKeyboardButton("🔄 MARK REVIEWING", callback_data=f"admin:reqreview:{req_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin:requests_inline")],
    ])


def cancel_reason_menu(req_id: str):
    reasons = [
        "Transaction not found on blockchain.",
        "Wrong network used for payment.",
        "Amount received is less than required.",
        "Invalid transaction ID submitted.",
        "Payment is still pending / unconfirmed.",
        "Payment proof could not be verified.",
    ]
    rows = []
    for i, reason in enumerate(reasons, start=1):
        rows.append([InlineKeyboardButton(f"{i}. {reason}", callback_data=f"admin:cancelreason:{req_id}:{i}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"admin:reqview:{req_id}")])
    return InlineKeyboardMarkup(rows)


def admin_reply_menu():
    keyboard = [
        [KeyboardButton("SET PRICE"), KeyboardButton("ALL USER")],
        [KeyboardButton("REQUESTS"), KeyboardButton("STATS")],
        [KeyboardButton("☁️ Backup (Drive)"), KeyboardButton("⬇️ Restore (Drive)")],
        [KeyboardButton("📎 Set Drive File ID"), KeyboardButton("📝 Set Admin Notes")],
        [KeyboardButton("🔚 Close Admin Menu")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def close_reply_menu():
    return ReplyKeyboardRemove()


# =========================================================
# SAFE EDIT
# =========================================================
async def safe_edit_message(query, text, reply_markup=None, parse_mode=None):
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


# =========================================================
# ORDER HELPERS
# =========================================================
def create_request(db: dict, user, mode: str, qty: int) -> str:
    req_id = str(db["next_request_id"])
    db["next_request_id"] += 1

    unit_price = float(db["prices"][mode])
    total_price = unit_price * qty

    db["requests"][req_id] = {
        "id": req_id,
        "user_id": user.id,
        "username": user.username or "",
        "name": user.full_name,
        "mode": mode,
        "qty": qty,
        "unit_price": unit_price,
        "total_price": total_price,
        "status": "awaiting_wallet_selection",
        "wallet_key": "",
        "wallet_label": "",
        "wallet_address": "",
        "txid": "",
        "created_at": now_text(),
        "updated_at": now_text(),
        "cancel_reason": "",
        "admin_note": "",
        "delivered_file_name": "",
    }
    return req_id


def update_request_status(req: dict, status: str):
    req["status"] = status
    req["updated_at"] = now_text()


def get_user_pending_txid_request(db: dict, user_id: int):
    for req in db["requests"].values():
        if req["user_id"] == user_id and req["status"] == "awaiting_txid":
            return req
    return None


def admin_request_text(req: dict) -> str:
    username_line = f"@{req['username']}" if req["username"] else "-"
    return (
        f"Request #{req['id']}\n\n"
        f"User: {req['name']}\n"
        f"Username: {username_line}\n"
        f"User ID: {req['user_id']}\n"
        f"Type: {MODE_LABELS.get(req['mode'], req['mode'])}\n"
        f"Quantity: {item_text(req['qty'])}\n"
        f"Price Per Account: {format_price(req['unit_price'])}\n"
        f"Total: {format_price(req['total_price'])}\n"
        f"Wallet: {req['wallet_label']}\n"
        f"Address: {req['wallet_address']}\n"
        f"TXID: {req['txid'] or '-'}\n"
        f"Status: {req['status']}\n"
        f"Created: {req['created_at']}\n"
        f"Updated: {req['updated_at']}"
    )


def user_processing_msg() -> str:
    return (
        "✅ THANK YOU 👍\n\n"
        "Your payment request has been received.\n"
        "It is currently being verified.\n\n"
        "After checking, your order will be delivered."
    )


# =========================================================
# COMMANDS
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user)

    state = context.user_data.setdefault("shop_state", {
        "mode": "disable",
        "qty": 1,
    })

    db = load_db()
    text, markup = build_main_menu(db["prices"], state["mode"], state["qty"])
    await update.message.reply_text(text, reply_markup=markup)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/start - Open product menu\n"
        "/admin - Open admin menu (admin only)\n"
        "/help - Show help"
    )
    await update.message.reply_text(text)


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("Admin Menu opened.", reply_markup=admin_reply_menu())


# =========================================================
# CALLBACKS
# =========================================================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    db = load_db()

    state = context.user_data.setdefault("shop_state", {
        "mode": "disable",
        "qty": 1,
    })

    # ------------- USER SELECTION -------------
    if data.startswith("mode:"):
        state["mode"] = data.split(":")[1]
        text, markup = build_main_menu(db["prices"], state["mode"], state["qty"])
        await safe_edit_message(query, text, reply_markup=markup)
        return

    if data.startswith("qty:"):
        state["qty"] = int(data.split(":")[1])
        text, markup = build_main_menu(db["prices"], state["mode"], state["qty"])
        await safe_edit_message(query, text, reply_markup=markup)
        return

    if data == "go_payment":
        req_id = create_request(db, query.from_user, state["mode"], state["qty"])
        save_db(db)

        req = db["requests"][req_id]
        summary = (
            f"Selected Type: {MODE_LABELS[req['mode']]}\n"
            f"Selected Quantity: {item_text(req['qty'])}\n"
            f"Price Per Account: {format_price(req['unit_price'])}\n"
            f"Total Amount: {format_price(req['total_price'])}\n\n"
            f"Now choose your payment method:"
        )
        await safe_edit_message(query, summary, reply_markup=build_payment_menu(req_id))
        return

    if data.startswith("back_order:"):
        text, markup = build_main_menu(db["prices"], state["mode"], state["qty"])
        await safe_edit_message(query, text, reply_markup=markup)
        return

    if data.startswith("back_wallets:"):
        req_id = data.split(":")[1]
        req = db["requests"].get(req_id)
        if not req:
            await safe_edit_message(query, "Request not found.")
            return

        text = (
            f"Selected Type: {MODE_LABELS[req['mode']]}\n"
            f"Selected Quantity: {item_text(req['qty'])}\n"
            f"Price Per Account: {format_price(req['unit_price'])}\n"
            f"Total Amount: {format_price(req['total_price'])}\n\n"
            f"Now choose your payment method:"
        )
        await safe_edit_message(query, text, reply_markup=build_payment_menu(req_id))
        return

    if data.startswith("paywallet:"):
        _, req_id, wallet_key = data.split(":")
        req = db["requests"].get(req_id)
        if not req:
            await safe_edit_message(query, "Request not found.")
            return

        wallet = db["payment_wallets"][wallet_key]
        req["wallet_key"] = wallet_key
        req["wallet_label"] = wallet["label"]
        req["wallet_address"] = wallet["address"]
        update_request_status(req, "wallet_selected")
        save_db(db)

        qr_file = db["payment_qr_files"].get(wallet_key)
        amount_text = format_price(req["total_price"])
        qr_caption = f"Scan this QR to pay {amount_text}\nvia {wallet['label']}."

        if qr_file and os.path.exists(qr_file):
            try:
                with open(qr_file, "rb") as f:
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=f,
                        caption=qr_caption,
                    )
            except Exception as e:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"QR file send failed.\nError: {e}\n\nPlease use the wallet address below."
                )
        else:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="QR file not found.\nPlease use the wallet address below."
            )

        details_text = (
            f"💳 PAYMENT DETAILS\n\n"
            f"🔐 Type: {MODE_LABELS.get(req['mode'], req['mode'])}\n"
            f"👤 Accounts: {req['qty']}\n"
            f"💵 Price Per Account: {format_price(req['unit_price'])}\n"
            f"💰 Total Amount: {amount_text}\n\n"
            f"🪙 Payment Method: {wallet['label']}\n\n"
            f"⚠️ Important:\n"
            f"Send only {wallet['label']} to this address.\n"
            f"Do not use any other coin or network."
        )
        await context.bot.send_message(chat_id=query.message.chat_id, text=details_text)

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"Wallet Address:\n`{wallet['address']}`",
            parse_mode=ParseMode.MARKDOWN,
        )

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="After completing the payment, tap the button below.",
            reply_markup=build_i_paid_menu(req_id),
        )

        await safe_edit_message(query, f"Payment instructions for {wallet['label']} have been sent above.")
        return

    if data.startswith("ihavepaid:"):
        req_id = data.split(":")[1]
        req = db["requests"].get(req_id)
        if not req:
            await safe_edit_message(query, "Request not found.")
            return

        update_request_status(req, "awaiting_txid")
        save_db(db)

        await safe_edit_message(
            query,
            text=(
                "Please send your Transaction ID (TXID / Hash) now.\n\n"
                "Example:\n"
                "`a3f9c7e12b45d67890abcdef1234567890abcdef1234567890`"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ------------- ADMIN REQUEST ACTIONS -------------
    if data == "admin:requests_inline":
        pending = [r for r in db["requests"].values() if r["status"] in ["submitted", "reviewing", "awaiting_admin_file"]]
        if not pending:
            await safe_edit_message(query, "No requests available.")
            return

        rows = []
        for req in pending[:50]:
            rows.append([
                InlineKeyboardButton(
                    f"#{req['id']} | {req['name']} | {format_price(req['total_price'])} | {req['status']}",
                    callback_data=f"admin:reqview:{req['id']}",
                )
            ])
        await safe_edit_message(query, "Requests List", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("admin:reqview:"):
        req_id = data.split(":")[2]
        req = db["requests"].get(req_id)
        if not req:
            await safe_edit_message(query, "Request not found.")
            return
        await safe_edit_message(query, admin_request_text(req), reply_markup=request_action_menu(req_id))
        return

    if data.startswith("admin:reqreview:"):
        req_id = data.split(":")[2]
        req = db["requests"].get(req_id)
        if not req:
            await safe_edit_message(query, "Request not found.")
            return
        update_request_status(req, "reviewing")
        save_db(db)
        await context.bot.send_message(
            chat_id=req["user_id"],
            text="🔄 Your payment is under review.\nWe will update you after checking."
        )
        await safe_edit_message(query, admin_request_text(req), reply_markup=request_action_menu(req_id))
        return

    if data.startswith("admin:reqcancel:"):
        req_id = data.split(":")[2]
        await safe_edit_message(query, f"Choose cancel reason for request #{req_id}", reply_markup=cancel_reason_menu(req_id))
        return

    if data.startswith("admin:cancelreason:"):
        _, _, req_id, idx = data.split(":")
        req = db["requests"].get(req_id)
        if not req:
            await safe_edit_message(query, "Request not found.")
            return

        reasons = {
            "1": "Transaction not found on blockchain.",
            "2": "Wrong network used for payment.",
            "3": "Amount received is less than required.",
            "4": "Invalid transaction ID submitted.",
            "5": "Payment is still pending / unconfirmed.",
            "6": "Payment proof could not be verified.",
        }
        reason = reasons.get(idx, "Payment verification failed.")
        req["cancel_reason"] = reason
        update_request_status(req, "cancelled")
        save_db(db)

        await context.bot.send_message(
            chat_id=req["user_id"],
            text=(
                "❌ Your payment request has been cancelled.\n\n"
                f"Reason: {reason}\n\n"
                "Please check the payment details and submit a new request if needed."
            ),
        )
        await safe_edit_message(query, f"Request #{req_id} cancelled.\nReason: {reason}")
        return

    if data.startswith("admin:reqcomplete:"):
        req_id = data.split(":")[2]
        req = db["requests"].get(req_id)
        if not req:
            await safe_edit_message(query, "Request not found.")
            return

        update_request_status(req, "awaiting_admin_file")
        save_db(db)
        context.user_data["complete_request_id"] = req_id

        await safe_edit_message(query, f"Send the delivery file now for request #{req_id}.")
        return


# =========================================================
# TEXT HANDLER
# =========================================================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    db = load_db()

    if is_admin(user.id):
        if text == "SET PRICE":
            context.user_data["await_price_mode"] = True
            await update.message.reply_text("Send mode name:\n\nenable\ndisable")
            return

        if text == "ALL USER":
            if not db["users"]:
                await update.message.reply_text("No users found.", reply_markup=admin_reply_menu())
                return

            lines = ["All Users:\n"]
            for uid, info in list(db["users"].items())[:100]:
                uname = f"@{info['username']}" if info["username"] else "-"
                lines.append(f"ID: {uid} | {info['name']} | {uname} | Orders: {info.get('orders_count', 0)}")

            await update.message.reply_text("\n".join(lines), reply_markup=admin_reply_menu())
            return

        if text == "REQUESTS":
            pending = [r for r in db["requests"].values() if r["status"] in ["submitted", "reviewing", "awaiting_admin_file"]]
            if not pending:
                await update.message.reply_text("No requests available.", reply_markup=admin_reply_menu())
                return

            rows = []
            for req in pending[:50]:
                rows.append([
                    InlineKeyboardButton(
                        f"#{req['id']} | {req['name']} | {format_price(req['total_price'])} | {req['status']}",
                        callback_data=f"admin:reqview:{req['id']}",
                    )
                ])
            await update.message.reply_text("Requests List", reply_markup=InlineKeyboardMarkup(rows))
            return

        if text == "STATS":
            users_count = len(db["users"])
            reqs = list(db["requests"].values())
            total_orders = len(reqs)
            completed = sum(1 for r in reqs if r["status"] == "completed")
            pending = sum(1 for r in reqs if r["status"] in ["submitted", "reviewing", "awaiting_admin_file"])
            cancelled = sum(1 for r in reqs if r["status"] == "cancelled")

            stats_text = (
                f"📊 STATS\n\n"
                f"Users: {users_count}\n"
                f"Orders: {total_orders}\n"
                f"Completed: {completed}\n"
                f"Pending: {pending}\n"
                f"Cancelled: {cancelled}\n"
            )
            await update.message.reply_text(stats_text, reply_markup=admin_reply_menu())
            return

        if text == "☁️ Backup (Drive)":
            try:
                file_id, action = drive_backup_store_db()
                await update.message.reply_text(
                    f"✅ Drive backup successful.\n\nAction: {action}\nFile ID: {file_id}",
                    reply_markup=admin_reply_menu(),
                )
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Drive backup failed.\n\nError: {e}",
                    reply_markup=admin_reply_menu(),
                )
            return

        if text == "⬇️ Restore (Drive)":
            try:
                drive_restore_store_db()
                await update.message.reply_text(
                    "✅ Drive restore successful.\n\nstore_db.json restored from Google Drive.",
                    reply_markup=admin_reply_menu(),
                )
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Drive restore failed.\n\nError: {e}",
                    reply_markup=admin_reply_menu(),
                )
            return

        if text == "📎 Set Drive File ID":
            context.user_data["await_drive_file_id"] = True
            await update.message.reply_text("Send Google Drive File ID now.")
            return

        if text == "📝 Set Admin Notes":
            context.user_data["await_admin_notes"] = True
            await update.message.reply_text("Send new admin notes text now.")
            return

        if text == "🔚 Close Admin Menu":
            await update.message.reply_text("Admin menu closed.", reply_markup=close_reply_menu())
            return

        if context.user_data.get("await_drive_file_id"):
            context.user_data.pop("await_drive_file_id", None)
            db["drive"]["file_id"] = text
            save_db(db)
            await update.message.reply_text(f"✅ Drive File ID saved.\n\n{text}", reply_markup=admin_reply_menu())
            return

        if context.user_data.get("await_admin_notes"):
            context.user_data.pop("await_admin_notes", None)
            db["settings"]["admin_notes"] = text
            save_db(db)
            await update.message.reply_text("✅ Admin notes updated.", reply_markup=admin_reply_menu())
            return

        if context.user_data.get("await_price_mode"):
            mode = text.lower()
            if mode not in ["enable", "disable"]:
                await update.message.reply_text("Invalid mode.\nSend: enable or disable")
                return
            context.user_data.pop("await_price_mode", None)
            context.user_data["set_price_for"] = mode
            await update.message.reply_text(f"Send new USD price for {mode}\n\nExample: 0.299")
            return

        if context.user_data.get("set_price_for"):
            mode = context.user_data.pop("set_price_for")
            try:
                price = float(text)
                if price <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("Invalid price. Example: 0.299")
                return

            db["prices"][mode] = price
            save_db(db)
            await update.message.reply_text(
                f"✅ Price updated.\n\nMode: {mode}\nNew Price: {format_price(price)}",
                reply_markup=admin_reply_menu(),
            )
            return

    pending_req = get_user_pending_txid_request(db, user.id)
    if pending_req:
        if not TXID_REGEX.match(text):
            await update.message.reply_text("Invalid Transaction ID format.\nPlease send a valid TXID / transaction hash.")
            return

        pending_req["txid"] = text
        update_request_status(pending_req, "submitted")
        save_db(db)

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"New Payment Request Received\n\n{admin_request_text(pending_req)}",
            reply_markup=request_action_menu(pending_req["id"]),
        )

        await update.message.reply_text(user_processing_msg())
        return


# =========================================================
# DOCUMENT HANDLER (ADMIN DELIVERY)
# =========================================================
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return

    req_id = context.user_data.get("complete_request_id")
    if not req_id:
        return

    db = load_db()
    req = db["requests"].get(req_id)
    if not req:
        await update.message.reply_text("Request not found.", reply_markup=admin_reply_menu())
        return

    doc = update.message.document

    # 1) delivery file user ko bhejo
    await context.bot.send_document(
        chat_id=req["user_id"],
        document=doc.file_id,
        caption=(
            "✅ Your order has been completed successfully.\n\n"
            "Thank you for your patience.\n"
            "Your delivery file is attached below."
        ),
    )

    # 2) request update karo
    req["delivered_file_name"] = doc.file_name or ""
    update_request_status(req, "completed")

    uid = str(req["user_id"])
    if uid in db["users"]:
        db["users"][uid]["orders_count"] = int(db["users"][uid].get("orders_count", 0)) + 1

    save_db(db)

    # 3) slip pdf banao
    pdf_file = generate_single_payment_slip_pdf(req)

    # 4) user ko slip pdf bhejo
    try:
        with open(pdf_file, "rb") as f:
            await context.bot.send_document(
                chat_id=req["user_id"],
                document=f,
                caption=(
                    "📄 Payment Slip\n\n"
                    "Your payment slip PDF is attached below.\n"
                    "Please keep it for your records."
                )
            )
    except Exception as e:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"Slip PDF send to user failed for request #{req_id}.\nError: {e}"
        )

    # 5) admin ko bhi slip pdf bhejo
    try:
        with open(pdf_file, "rb") as f:
            await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=f,
                caption=f"📄 Payment slip PDF for request #{req_id}."
            )
    except Exception as e:
        await update.message.reply_text(f"Slip PDF admin send failed.\nError: {e}")

    context.user_data.pop("complete_request_id", None)

    await update.message.reply_text(
        f"✅ Order #{req_id} completed, delivered, and slip generated.",
        reply_markup=admin_reply_menu(),
    )
# =========================================================
# ERROR HANDLER
# =========================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception", exc_info=context.error)


# =========================================================
# MAIN
# =========================================================

# =========================================================
# PHOTO HANDLER (ADMIN QR UPLOAD)
# =========================================================
async def on_photo(update, context):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    if not context.user_data.get("await_qr_wallet"):
        return

    wallet_key = context.user_data.pop("await_qr_wallet")

    db = load_db()
    db.setdefault("payment_qr_file_ids", {})

    photo = update.message.photo[-1]
    db["payment_qr_file_ids"][wallet_key] = photo.file_id

    save_db(db)

    label = db["payment_wallets"][wallet_key]["label"]
    await update.message.reply_text(f"QR saved for {label}")


def build_app() -> Application:
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo),
        MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.add_error_handler(error_handler)
    return app


def main():
    if TOKEN == "YOUR_BOT_TOKEN":
        raise RuntimeError("Set BOT_TOKEN first.")

    app = build_app()

    if WEBHOOK_MODE:
        if not WEBHOOK_URL:
            raise RuntimeError("WEBHOOK_MODE is enabled but WEBHOOK_URL is not set.")
        full_webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
        logger.info("Starting in webhook mode on %s", full_webhook_url)
        app.run_webhook(
            listen=HOST,
            port=PORT,
            webhook_url=full_webhook_url,
            url_path=WEBHOOK_PATH.lstrip("/"),
            drop_pending_updates=True,
        )
    else:
        logger.info("Starting in polling mode")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()