import json
import os
import asyncio
import html
from datetime import date, datetime, timedelta
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters
)
from flask import Flask
import threading

# ---------------- WEB SERVER (Render Fix) ----------------
app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Bot is running"

def run_web():
    app_web.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# ---------------- BASIC CONFIG ----------------
TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = "data.json"
DEFAULT_LIMIT = 4

# ---------------- LOAD / SAVE ----------------
def load_data():
    default_data = {
        "limits": {},
        "warn_limits": {},
        "mute_settings": {},
        "alert_settings": {},
        "reset_time": 86400,
        "enabled_chats": [],
        "super_admins": [],
        "permissions": {},
        "log_channel": "",
        "user_data": {},
        "messages": {
            "warn": "Warning!",
            "limit": "Daily limit crossed.",
            "mute": "{name} muted for {duration} seconds."
        }
    }

    if not os.path.exists(DATA_FILE):
        return default_data

    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
    except:
        return default_data

    # Auto fix missing keys
    for key, value in default_data.items():
        if key not in data:
            data[key] = value

    return data

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

data = load_data()

# ---------------- PERMISSION ----------------
def is_super(uid):
    return str(uid) in data["super_admins"]

def has_permission(chat_id, uid):
    chat_id = str(chat_id)
    uid = str(uid)

    if is_super(uid):
        return True

    if chat_id in data["permissions"]:
        if uid in data["permissions"][chat_id].get("admins", []):
            return True
        if uid in data["permissions"][chat_id].get("mods", []):
            return True

    return False

# ---------------- LOG ----------------
async def log_action(context, text):
    if data["log_channel"]:
        try:
            await context.bot.send_message(int(data["log_channel"]), text)
        except:
            pass

# ---------------- BOT ADDED ----------------
async def bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id not in data["enabled_chats"]:
        data["enabled_chats"].append(chat_id)
        save_data()

# ---------------- MESSAGE HANDLER ----------------
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    if not update.effective_chat or not update.effective_user:
        return

    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if user.is_bot:
        return

    # Auto enable chat
    if chat_id not in data["enabled_chats"]:
        data["enabled_chats"].append(chat_id)
        save_data()

    user_id = str(user.id)
    name = user.first_name or "User"
    today = str(date.today())

    limit = data["limits"].get(chat_id, DEFAULT_LIMIT)
    warn_limit = data["warn_limits"].get(chat_id, limit - 1)

    data["user_data"].setdefault(chat_id, {})
    data["user_data"][chat_id].setdefault(user_id, {"count": 0, "date": today})

    u = data["user_data"][chat_id][user_id]

    if u["date"] != today:
        u["count"] = 0
        u["date"] = today

    u["count"] += 1
    save_data()

    # WARN
    if u["count"] == warn_limit:
        await update.message.reply_text(data["messages"]["warn"])
        await log_action(context, f"WARN → {name}")

    # LIMIT CROSS
    if u["count"] == limit + 1:

        try:
            await update.message.delete()
        except:
            pass

        mute_enabled = False
        duration = 0

        if chat_id in data["mute_settings"]:
            m = data["mute_settings"][chat_id]
            if m.get("enabled"):
                mute_enabled = True
                duration = m.get("duration", 0)

        safe_name = html.escape(name)
        mention = f"<a href=\"tg://user?id={user_id}\">{safe_name}</a>"

        alert_text = (
            f"🚫 <b>ʀᴇǫᴜᴇsᴛ ʟɪᴍɪᴛ ᴇxᴄᴇᴇᴅᴇᴅ</b>\n\n"
            f"👤 <b>ᴜsᴇʀ:</b> {mention}\n"
            f"📌 <b>ᴅᴀɪʟʏ ʟɪᴍɪᴛ:</b> <code>{limit}</code> <b>ᴍᴇssᴀɢᴇs</b>\n"
        )

        if mute_enabled:
            alert_text += f"🔇 <b>ᴍᴜᴛᴇᴅ ғᴏʀ:</b> <code>{duration}</code> <b>sᴇᴄᴏɴᴅs</b>\n"

        alert_text += "\n<b>ʏᴏᴜʀ ᴛᴏᴅᴀʏ's ǫᴜᴏᴛᴀ ɪs ᴏᴠᴇʀ.</b>"

        sent_msg = await context.bot.send_message(
            chat_id=int(chat_id),
            text=alert_text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

        await log_action(context, f"LIMIT CROSS → {name}")

        if mute_enabled:
            until = datetime.utcnow() + timedelta(seconds=duration)
            try:
                await context.bot.restrict_chat_member(
                    int(chat_id),
                    int(user_id),
                    ChatPermissions(can_send_messages=False),
                    until_date=until
                )
                await log_action(context, f"MUTED → {name}")
            except:
                pass

        alert_delete_time = data["alert_settings"].get(chat_id, 30)

        async def delete_alert(context):
            try:
                await sent_msg.delete()
            except:
                pass

        context.job_queue.run_once(delete_alert, alert_delete_time)

# ---------------- COMMAND SAFETY WRAPPER ----------------
def require_args(func):
    async def wrapper(update, context):
        if not context.args:
            await update.message.reply_text("Missing argument.")
            return
        await func(update, context)
    return wrapper

# ---------------- SETTINGS ----------------
@require_args
async def setlimit(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    data["limits"][str(update.effective_chat.id)] = int(context.args[0])
    save_data()
    await update.message.reply_text("Limit updated.")

@require_args
async def warnlimit(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    data["warn_limits"][str(update.effective_chat.id)] = int(context.args[0])
    save_data()
    await update.message.reply_text("Warn limit updated.")

@require_args
async def setmutetime(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    chat_id = str(update.effective_chat.id)
    seconds = int(context.args[0])
    data["mute_settings"].setdefault(chat_id, {"enabled": True})
    data["mute_settings"][chat_id]["duration"] = seconds
    save_data()
    await update.message.reply_text("Mute time updated.")

@require_args
async def setalerttime(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    data["alert_settings"][str(update.effective_chat.id)] = int(context.args[0])
    save_data()
    await update.message.reply_text("Alert auto-delete time updated.")

# ---------------- PANEL ----------------
async def panel(update, context):
    await update.message.reply_text(
        "/setlimit\n/warnlimit\n/setmutetime\n/setalerttime\n/muteon\n/muteoff"
    )

# ---------------- MAIN ----------------
def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN not set")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bot_added))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))

    app.add_handler(CommandHandler("setlimit", setlimit))
    app.add_handler(CommandHandler("warnlimit", warnlimit))
    app.add_handler(CommandHandler("setmutetime", setmutetime))
    app.add_handler(CommandHandler("setalerttime", setalerttime))
    app.add_handler(CommandHandler("panel", panel))

    print("Bot is starting...")
    app.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    main()
