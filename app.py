import os
import json
import logging
import aiohttp
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# -------------------
# Load Environment
# -------------------
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
BLOCKONOMICS_API_KEY = os.getenv("BLOCKONOMICS_API_KEY")

# -------------------
# Logging
# -------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# -------------------
# Data Storage
# -------------------
ITEMS_FILE = "items.json"

def load_items():
    if not os.path.exists(ITEMS_FILE):
        return []
    with open(ITEMS_FILE, "r") as f:
        return json.load(f)

def save_items(items):
    with open(ITEMS_FILE, "w") as f:
        json.dump(items, f, indent=2)

# -------------------
# Handlers
# -------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📚 Browse Items", callback_data="browse_items")],
        [InlineKeyboardButton("💳 Buy Item", callback_data="buy_item")],
    ]
    if update.effective_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Menu", callback_data="admin_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome to the Digital Goods Store!\nChoose an option:",
        reply_markup=reply_markup
    )

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "browse_items":
        items = load_items()
        if not items:
            await query.edit_message_text("No items available.")
            return
        text = "\n".join([f"{i+1}. {item['name']} - ${item['price']}" for i, item in enumerate(items)])
        await query.edit_message_text(f"📚 Available Items:\n{text}")

    elif query.data == "buy_item":
        await query.edit_message_text("Buying not fully implemented yet.")

    elif query.data == "admin_menu":
        if query.from_user.id != ADMIN_ID:
            await query.edit_message_text("❌ Access denied.")
            return
        keyboard = [
            [InlineKeyboardButton("➕ Add Item", callback_data="admin_add")],
            [InlineKeyboardButton("❌ Delete Item", callback_data="admin_delete")],
        ]
        await query.edit_message_text("👑 Admin Menu", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    await update.message.reply_text("Send item in format: `Name,Price`", parse_mode="Markdown")
    context.user_data["adding_item"] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("adding_item"):
        try:
            name, price = update.message.text.split(",")
            items = load_items()
            items.append({"name": name.strip(), "price": float(price.strip())})
            save_items(items)
            await update.message.reply_text(f"✅ Item '{name.strip()}' added.")
        except Exception:
            await update.message.reply_text("❌ Format error. Use: Name,Price")
        context.user_data["adding_item"] = False

# -------------------
# Main Entry
# -------------------
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", admin_add_start))
    app.add_handler(CallbackQueryHandler(menu_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Local dev (polling)
    if os.getenv("MODE", "polling") == "polling":
        app.run_polling()
    else:
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.getenv("PORT", 5000)),
            url_path=TOKEN,
            webhook_url=f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{TOKEN}"
        )

if __name__ == "__main__":
    main()
