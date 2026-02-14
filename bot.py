import json
import os
from datetime import date, datetime, timedelta
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters
)

TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = "data.json"
DEFAULT_LIMIT = 4

# ---------------- LOAD / SAVE ----------------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "limits": {},
            "warn_limits": {},
            "mute_settings": {},
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
    with open(DATA_FILE, "r") as f:
        return json.load(f)

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
        await context.bot.send_message(int(data["log_channel"]), text)

# ---------------- BOT ADDED ----------------
async def bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id not in data["enabled_chats"]:
        data["enabled_chats"].append(chat_id)
        save_data()

# ---------------- MESSAGE HANDLER ----------------
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if user.is_bot:
        return

    if chat_id not in data["enabled_chats"]:
        return

    user_id = str(user.id)
    name = user.first_name
    today = str(date.today())

    limit = data["limits"].get(chat_id, DEFAULT_LIMIT)
    warn_limit = data["warn_limits"].get(chat_id, limit - 1)

    if chat_id not in data["user_data"]:
        data["user_data"][chat_id] = {}

    if user_id not in data["user_data"][chat_id]:
        data["user_data"][chat_id][user_id] = {"count": 0, "date": today}

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
        await update.message.reply_text(data["messages"]["limit"])
        await log_action(context, f"LIMIT CROSS → {name}")

        if chat_id in data["mute_settings"]:
            m = data["mute_settings"][chat_id]
            if m["enabled"]:
                duration = m["duration"]
                until = datetime.utcnow() + timedelta(seconds=duration)

                await context.bot.restrict_chat_member(
                    int(chat_id),
                    int(user_id),
                    ChatPermissions(can_send_messages=False),
                    until_date=until
                )

                mute_text = data["messages"]["mute"].format(
                    name=name,
                    duration=duration
                )

                await update.message.reply_text(mute_text)
                await log_action(context, f"MUTED → {name}")

# ---------------- USER STATS ----------------
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)

    if context.args and has_permission(chat_id, user_id):
        user_id = context.args[0]

    if chat_id not in data["user_data"] or user_id not in data["user_data"][chat_id]:
        await update.message.reply_text("No data.")
        return

    u = data["user_data"][chat_id][user_id]
    limit = data["limits"].get(chat_id, DEFAULT_LIMIT)

    await update.message.reply_text(
        f"Messages today: {u['count']}\n"
        f"Last date: {u['date']}\n"
        f"Limit: {limit}"
    )

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id not in data["user_data"]:
        return

    users = data["user_data"][chat_id]
    sorted_users = sorted(users.items(), key=lambda x: x[1]["count"], reverse=True)

    text = "Top Users:\n"
    for uid, info in sorted_users[:5]:
        text += f"{uid} → {info['count']}\n"

    await update.message.reply_text(text)

# ---------------- ROLE MANAGEMENT ----------------
async def addadmin(update, context):
    if not is_super(update.effective_user.id):
        return
    chat_id = str(update.effective_chat.id)
    uid = context.args[0]
    data.setdefault("permissions", {}).setdefault(chat_id, {"admins": [], "mods": []})
    data["permissions"][chat_id]["admins"].append(uid)
    save_data()
    await update.message.reply_text("Admin added.")

async def addmod(update, context):
    if not is_super(update.effective_user.id):
        return
    chat_id = str(update.effective_chat.id)
    uid = context.args[0]
    data.setdefault("permissions", {}).setdefault(chat_id, {"admins": [], "mods": []})
    data["permissions"][chat_id]["mods"].append(uid)
    save_data()
    await update.message.reply_text("Mod added.")

async def removeadmin(update, context):
    if not is_super(update.effective_user.id):
        return
    chat_id = str(update.effective_chat.id)
    uid = context.args[0]
    data["permissions"][chat_id]["admins"].remove(uid)
    save_data()
    await update.message.reply_text("Admin removed.")

async def removemod(update, context):
    if not is_super(update.effective_user.id):
        return
    chat_id = str(update.effective_chat.id)
    uid = context.args[0]
    data["permissions"][chat_id]["mods"].remove(uid)
    save_data()
    await update.message.reply_text("Mod removed.")

# ---------------- SETTINGS ----------------
async def setlimit(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    chat_id = str(update.effective_chat.id)
    data["limits"][chat_id] = int(context.args[0])
    save_data()
    await update.message.reply_text("Limit updated.")

async def warnlimit(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    chat_id = str(update.effective_chat.id)
    data["warn_limits"][chat_id] = int(context.args[0])
    save_data()
    await update.message.reply_text("Warn limit updated.")

async def setwarnmsg(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    data["messages"]["warn"] = " ".join(context.args)
    save_data()
    await update.message.reply_text("Warn message updated.")

async def setlimitmsg(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    data["messages"]["limit"] = " ".join(context.args)
    save_data()
    await update.message.reply_text("Limit message updated.")

async def setmutemsg(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    data["messages"]["mute"] = " ".join(context.args)
    save_data()
    await update.message.reply_text("Mute message updated.")

async def muteon(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    chat_id = str(update.effective_chat.id)
    data["mute_settings"][chat_id] = {"enabled": True, "duration": 600}
    save_data()
    await update.message.reply_text("Auto mute ON.")

async def muteoff(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    chat_id = str(update.effective_chat.id)
    if chat_id in data["mute_settings"]:
        data["mute_settings"][chat_id]["enabled"] = False
    save_data()
    await update.message.reply_text("Auto mute OFF.")

async def setmutetime(update, context):
    if not has_permission(update.effective_chat.id, update.effective_user.id):
        return
    chat_id = str(update.effective_chat.id)
    seconds = int(context.args[0])
    data["mute_settings"].setdefault(chat_id, {"enabled": True, "duration": seconds})
    data["mute_settings"][chat_id]["duration"] = seconds
    save_data()
    await update.message.reply_text("Mute time updated.")

async def setlog(update, context):
    if not is_super(update.effective_user.id):
        return
    data["log_channel"] = context.args[0]
    save_data()
    await update.message.reply_text("Log channel set.")

async def panel(update, context):
    await update.message.reply_text(
        "/setlimit\n/warnlimit\n/setwarnmsg\n/setlimitmsg\n"
        "/setmutemsg\n/muteon\n/muteoff\n/setmutetime\n"
        "/addadmin\n/addmod\n/removeadmin\n/removemod\n"
        "/stats\n/top\n/setlog"
    )

# ---------------- MAIN ----------------
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bot_added))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))

    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("addmod", addmod))
    app.add_handler(CommandHandler("removeadmin", removeadmin))
    app.add_handler(CommandHandler("removemod", removemod))
    app.add_handler(CommandHandler("setlimit", setlimit))
    app.add_handler(CommandHandler("warnlimit", warnlimit))
    app.add_handler(CommandHandler("setwarnmsg", setwarnmsg))
    app.add_handler(CommandHandler("setlimitmsg", setlimitmsg))
    app.add_handler(CommandHandler("setmutemsg", setmutemsg))
    app.add_handler(CommandHandler("muteon", muteon))
    app.add_handler(CommandHandler("muteoff", muteoff))
    app.add_handler(CommandHandler("setmutetime", setmutetime))
    app.add_handler(CommandHandler("setlog", setlog))
    app.add_handler(CommandHandler("panel", panel))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await app.updater.idle()
    await app.stop()
    await app.shutdown()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
