#!/usr/bin/env python3
import os
import json
import asyncio
import logging
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import aiohttp
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.requests import Request

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =========================
# Setup & Config
# =========================

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
BLOCKONOMICS_API_KEY = os.getenv("BLOCKONOMICS_API_KEY", "").strip()
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
SPICY_MODE = os.getenv("SPICY_MODE", "true").lower() == "true"  # edgy language toggle
WELCOME_VIDEO = os.getenv(
    "WELCOME_VIDEO",
    "https://ik.imagekit.io/myrnjevjk/game%20over.mp4?updatedAt=1754980438031",
)
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "change-me")
BLOCKONOMICS_CALLBACK_SECRET = os.getenv("BLOCKONOMICS_CALLBACK_SECRET", "change-me")

CATEGORIES_FILE = os.getenv("CATEGORIES_FILE", "categories.json")
ITEMS_FILE = os.getenv("ITEMS_FILE", "items.json")
ORDERS_FILE = os.getenv("ORDERS_FILE", "orders.json")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN environment variable is required.")
if not BLOCKONOMICS_API_KEY:
    logging.warning("BLOCKONOMICS_API_KEY is missing. Payments will fail.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("shopbot")

# =========================
# Helpers: JSON persistence
# =========================

def load_json(filepath: str, default: Any) -> Any:
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Failed to load {filepath}: {e}")
    return default

def save_json(filepath: str, data: Any) -> None:
    tmp = filepath + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, filepath)
    except Exception as e:
        log.error(f"Failed to save {filepath}: {e}")

# initial defaults (used if files absent)
CATEGORIES: Dict[str, List[str]] = load_json(
    CATEGORIES_FILE,
    {"cards": ["item1", "item3", "item7"], "tutorials": ["item2", "item5", "item6", "item9"], "pages": ["item4", "item8", "item10"]},
)
ITEMS: Dict[str, Dict[str, Any]] = load_json(
    ITEMS_FILE,
    {
        "item1": {"name": "Dark Secret Card", "price_btc": 0.0001, "file_path": "items/secret.pdf"},
        "item2": {"name": "Forbidden Tutorial", "price_btc": 0.0002, "file_path": "items/archive.zip"},
        "item3": {"name": "Blackout Blackjack Guide", "price_btc": 0.0003, "file_path": "items/blackjack.pdf"},
        "item4": {"name": "Cryptic Code Pages", "price_btc": 0.00015, "file_path": "items/codepages.pdf"},
        "item5": {"name": "Cybersecurity Masterclass", "price_btc": 0.0005, "file_path": "items/malware.mp4"},
        "item6": {"name": "Phantom Code Manual", "price_btc": 0.00025, "file_path": "items/phishing.pdf"},
        "item7": {"name": "Ghost Scripts Collection", "price_btc": 0.0004, "file_path": "items/ghostscripts.zip"},
        "item8": {"name": "Shadow Pages Vol.1", "price_btc": 0.00012, "file_path": "items/shadowpages.pdf"},
        "item9": {"name": "Underground Tips", "price_btc": 0.00035, "file_path": "items/hacktips.pdf"},
        "item10": {"name": "Market Blueprints", "price_btc": 0.0006, "file_path": "items/blueprints.pdf"},
    },
)
# orders are keyed by BTC address
ORDERS: Dict[str, Dict[str, Any]] = load_json(ORDERS_FILE, {})

orders_lock = asyncio.Lock()

# =========================
# Flavor text
# =========================
def spicy(nice: str, spicy_txt: str) -> str:
    return spicy_txt if SPICY_MODE else nice

WELCOME_TEXT = spicy("Welcome.", "Welcome to the dark side, fucker.")
NO_ITEMS_TEXT = spicy("No items found in this category.", "No items found in this category, asshole.")
NO_PENDING_TEXT = spicy("No pending payment.", "No pending payment, asshole. Buy something first.")
SEND_AFTER_PAY_TEXT = spicy("Run /confirm when you’ve paid.", "Run /confirm when you’ve paid, or I’ll know you’re a cheap fuck.")
NOT_CONFIRMED_TEXT = spicy("Payment not confirmed yet.", "Payment not confirmed yet. Don’t fuck with me.")
PAYMENT_CHECK_FAIL_TEXT = spicy("Payment check failed. Try again later.", "Payment check failed. Try again, dumbass.")
FILE_MISSING_TEXT = spicy("File not found. Please contact the admin.", "File’s fucked. Fix the path, moron.")
NOT_ADMIN_TEXT = spicy("You are not authorized to use admin mode.", "You’re not admin, get lost.")

# =========================
# Utility: Context-safe message edit/reply
# =========================
async def safe_reply_or_edit(
    update: Update,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = None,
):
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode
            )
    else:
        await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)

# =========================
# Payment utils (Blockonomics)
# =========================
aiohttp_session: Optional[aiohttp.ClientSession] = None

async def get_http() -> aiohttp.ClientSession:
    global aiohttp_session
    if aiohttp_session is None or aiohttp_session.closed:
        aiohttp_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    return aiohttp_session

def btc_link(address: str, amount: float) -> str:
    # bitcoin URI for wallet deep-linking
    return f"bitcoin:{address}?amount={amount}"

async def blockonomics_new_address() -> str:
    """Get a fresh receiving address bound to your Blockonomics account."""
    if not BLOCKONOMICS_API_KEY:
        raise RuntimeError("BLOCKONOMICS_API_KEY not set")
    session = await get_http()
    headers = {"Authorization": f"Bearer {BLOCKONOMICS_API_KEY}"}
    url = "https://www.blockonomics.co/api/new_address"
    async with session.post(url, headers=headers) as resp:
        if resp.status != 200:
            txt = await resp.text()
            raise RuntimeError(f"new_address failed: HTTP {resp.status} - {txt}")
        data = await resp.json()
    address = data.get("address")
    if not address:
        raise RuntimeError("No address returned")
    return address

async def blockonomics_confirmed_btc(address: str) -> float:
    """Return confirmed balance for the given address (BTC)."""
    if not BLOCKONOMICS_API_KEY:
        raise RuntimeError("BLOCKONOMICS_API_KEY not set")
    session = await get_http()
    headers = {"Authorization": f"Bearer {BLOCKONOMICS_API_KEY}"}
    # Primary
    url = f"https://www.blockonomics.co/api/address?addr={address}"
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            sat = float(data.get("confirmed", 0))
            return sat / 1e8
    # Fallback
    url2 = "https://www.blockonomics.co/api/balance"
    async with session.post(url2, json={"addr": [address]}, headers=headers) as resp:
        if resp.status != 200:
            txt = await resp.text()
            raise RuntimeError(f"balance failed: HTTP {resp.status} - {txt}")
        data = await resp.json()
    sat = float(data["data"][0]["confirmed"])
    return sat / 1e8

# =========================
# Bot: UI
# =========================
def categories_keyboard() -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(cat.title(), callback_data=f"cat_{cat}")]
                for cat in CATEGORIES.keys()]
    return InlineKeyboardMarkup(keyboard)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if WELCOME_VIDEO:
            await update.effective_message.reply_video(video=WELCOME_VIDEO, caption=WELCOME_TEXT)
        else:
            await update.effective_message.reply_text(WELCOME_TEXT)
    except Exception as e:
        await update.effective_message.reply_text(f"Error sending welcome: {e}")
    await update.effective_message.reply_text("Choose a category:", reply_markup=categories_keyboard())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Commands:\n"
        "/start – Open shop\n"
        "/confirm – Confirm payment after sending BTC\n"
        "/help – This help\n"
    )
    await update.effective_message.reply_text(text)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = (query.data or "").strip()
    log.info(f"[DEBUG] callback: {data}")

    if data.startswith("cat_"):
        cat_key = data[4:]
        items_in_cat = CATEGORIES.get(cat_key, [])
        if not items_in_cat:
            await query.message.edit_text(NO_ITEMS_TEXT)
            return

        keyboard = [
            [InlineKeyboardButton(ITEMS[key]["name"], callback_data=f"item_{key}")]
            for key in items_in_cat
            if key in ITEMS
        ]
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_categories")])
        await query.message.edit_text(
            f"Items in *{cat_key.title()}*:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "back_to_categories":
        await query.message.edit_text("Choose a category:", reply_markup=categories_keyboard())

    elif data.startswith("item_"):
        item_key = data[5:]
        item = ITEMS.get(item_key)
        if not item:
            await query.message.reply_text(spicy("Item no longer exists.", "Item’s gone, asshole. Pick something else."))
            return

        try:
            address = await blockonomics_new_address()
            log.info(f"[DEBUG] new BTC address: {address}")
        except Exception as e:
            await query.message.reply_text(
                spicy(f"Failed to get BTC address: {e}", f"Failed to get BTC address: {e}. Try again, dipshit.")
            )
            return

        order = {
            "item_key": item_key,
            "user_id": update.effective_user.id if update.effective_user else None,
            "chat_id": query.message.chat_id,
            "address": address,
            "amount_btc": float(item["price_btc"]),
            "status": "pending",
            "txid": None,
            "received_sats": 0,
        }
        async with orders_lock:
            ORDERS[address] = order
            save_json(ORDERS_FILE, ORDERS)

        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Open in wallet", url=btc_link(address, item["price_btc"]))],
                [InlineKeyboardButton("⬅️ Back", callback_data="back_to_categories")],
            ]
        )

        msg = (
            f"Pay *{item['price_btc']} BTC* to:\n`{address}`\n\n"
            f"Item: *{item['name']}*\n\n"
            f"{SEND_AFTER_PAY_TEXT}"
        )
        await query.message.edit_text(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    else:
        await query.message.reply_text("Unhandled action.")

async def confirm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual payment check fallback (polls Blockonomics)."""
    # find latest pending order for user
    uid = update.effective_user.id if update.effective_user else None
    last_order = None
    for addr, od in reversed(list(ORDERS.items())):
        if od.get("user_id") == uid and od.get("status") == "pending":
            last_order = od
            break
    if not last_order:
        await update.effective_message.reply_text(NO_PENDING_TEXT)
        return

    address = last_order["address"]
    required = float(last_order["amount_btc"])
    item = ITEMS.get(last_order["item_key"], {})
    try:
        received = await blockonomics_confirmed_btc(address)
    except Exception as e:
        await update.effective_message.reply_text(f"{PAYMENT_CHECK_FAIL_TEXT}\n\nDetails: {e}")
        return

    if received + 1e-12 >= required:
        fpath = item.get("file_path")
        if not fpath or not os.path.exists(fpath):
            await update.effective_message.reply_text(FILE_MISSING_TEXT)
            return
        try:
            with open(fpath, "rb") as fp:
                await update.effective_message.reply_document(
                    document=InputFile(fp),
                    caption=spicy(f"Here's your {item['name']}.", f"Here's your {item['name']}. Enjoy, you sick fuck."),
                )
            async with orders_lock:
                last_order["status"] = "delivered"
                save_json(ORDERS_FILE, ORDERS)
        except Exception as e:
            await update.effective_message.reply_text(f"Failed to deliver file: {e}")
    else:
        shortfall = required - received
        await update.effective_message.reply_text(
            f"{NOT_CONFIRMED_TEXT}\n\n"
            f"Received: {received:.8f} BTC\n"
            f"Needed: {required:.8f} BTC\n"
            f"Short: {shortfall:.8f} BTC"
        )

# =========================
# PTB Application
# =========================
application = Application.builder().token(TELEGRAM_TOKEN).updater(None).build()
application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CommandHandler("confirm", confirm_cmd))
application.add_handler(CallbackQueryHandler(button_callback))

# =========================
# Starlette routes
# =========================

async def telegram_webhook(request: Request):
    """Accepts Telegram webhooks and forwards them into PTB's update queue."""
    # Verify Telegram secret token header for security
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != TELEGRAM_WEBHOOK_SECRET:
        return PlainTextResponse("forbidden", status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return PlainTextResponse("bad request", status_code=400)

    update = Update.de_json(payload, application.bot)
    await application.update_queue.put(update)
    return PlainTextResponse("ok", status_code=200)

async def healthcheck(request: Request):
    return JSONResponse({"ok": True})

async def blockonomics_callback(request: Request):
    """
    Blockonomics HTTP Callback for address-based payments.
    Typical query params:
      status: -1(not started), 0(unconfirmed), 1(partially confirmed), 2(confirmed)
      addr:   bitcoin address
      value:  received amount in satoshis
      txid:   transaction id
      (optional) secret: your shared secret
    """
    params = dict(request.query_params)
    secret = params.get("secret", "")
    if secret != BLOCKONOMICS_CALLBACK_SECRET:
        return PlainTextResponse("forbidden", status_code=403)

    try:
        status = int(params.get("status", "-1"))
        addr = params.get("addr", "")
        value_sats = int(params.get("value", "0") or "0")
        txid = params.get("txid", None)
    except Exception as e:
        log.error(f"Callback parse error: {e}")
        return PlainTextResponse("bad request", status_code=400)

    # Lookup order by address
    async with orders_lock:
        order = ORDERS.get(addr)
    if not order:
        log.warning(f"Callback for unknown addr {addr}")
        return PlainTextResponse("ok", status_code=200)

    required_sats = int(round(float(order["amount_btc"]) * 1e8))
    log.info(f"[CALLBACK] addr={addr} status={status} value_sats={value_sats} txid={txid} required={required_sats}")

    # Update order info
    async with orders_lock:
        order["received_sats"] = value_sats
        order["txid"] = txid
        # Only deliver when fully confirmed
        if status == 2 and value_sats >= required_sats and order.get("status") != "delivered":
            # Deliver the file to chat
            item = ITEMS.get(order["item_key"], {})
            fpath = item.get("file_path")
            if fpath and os.path.exists(fpath):
                try:
                    with open(fpath, "rb") as fp:
                        await application.bot.send_document(
                            chat_id=order["chat_id"],
                            document=InputFile(fp),
                            caption=spicy(
                                f"Payment confirmed ✅\nHere’s your {item.get('name','file')}.",
                                f"Paid. Confirmed. Delivered. Take your {item.get('name','file')}, you degenerate."
                            ),
                        )
                    order["status"] = "delivered"
                except Exception as e:
                    log.error(f"Delivery failed: {e}")
            else:
                await application.bot.send_message(
                    chat_id=order["chat_id"],
                    text=FILE_MISSING_TEXT,
                )
        else:
            order["status"] = "pending"

        save_json(ORDERS_FILE, ORDERS)

    return PlainTextResponse("ok", status_code=200)

routes = [
    Route("/telegram", telegram_webhook, methods=["POST"]),
    Route("/healthz", healthcheck, methods=["GET"]),
    Route("/blockonomics/callback", blockonomics_callback, methods=["GET"]),
]

app = Starlette(routes=routes)

# =========================
# Starlette startup/shutdown
# =========================
@app.on_event("startup")
async def on_startup():
    log.info("Starting bot & setting Telegram webhook …")
    await application.initialize()
    await application.start()

    # Set Telegram webhook (requires PUBLIC_URL)
    if PUBLIC_URL:
        try:
            await application.bot.set_webhook(
                url=f"{PUBLIC_URL}/telegram",
                secret_token=TELEGRAM_WEBHOOK_SECRET,
                drop_pending_updates=True,
            )
            log.info(f"Webhook set to {PUBLIC_URL}/telegram")
        except Exception as e:
            log.error(f"Failed to set webhook: {e}")
    else:
        log.warning("PUBLIC_URL not set. Set it to your Render service URL to enable Telegram webhooks.")

@app.on_event("shutdown")
async def on_shutdown():
    log.info("Shutting down …")
    try:
        await application.bot.delete_webhook()
    except Exception:
        pass
    await application.stop()
    await application.shutdown()
    if aiohttp_session and not aiohttp_session.closed:
        await aiohttp_session.close()
