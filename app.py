import os
import json
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ---------------------- Helper Functions ----------------------

def load_items():
    """Load items from JSON file."""
    try:
        with open("items/items.json", "r") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logging.error("items.json must be a list of dictionaries")
            return []
        return data
    except FileNotFoundError:
        logging.warning("items.json not found")
        return []
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON: {e}")
        return []

# ---------------------- Handlers ----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Use /menu to see available items."
    )

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = load_items()
    if not items:
        await update.message.reply_text("No items available right now.")
        return

    text = "\n".join([f"{i+1}. {item['name']} - ${item['price']}" for i, item in enumerate(items)])
    await update.message.reply_text(text)

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Sorry, I didn't understand that command.")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    await update.message.reply_text("Admin command executed.")

# ---------------------- Main ----------------------

def main():
    app = Application.builder().token(TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_handler))
    app.add_handler(CommandHandler("admin", admin_command))
    
    # Unknown commands
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    # ---------------------- Deployment Choice ----------------------
    # Local testing (polling)
    if os.getenv("RENDER") != "true":
        logging.info("Running in polling mode")
        app.run_polling()
    # Render deployment (webhook)
    else:
        logging.info("Running in webhook mode")
        port = int(os.environ.get("PORT", 8443))
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TOKEN,
            webhook_url=f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{TOKEN}"
        )

if __name__ == "__main__":
    main()
