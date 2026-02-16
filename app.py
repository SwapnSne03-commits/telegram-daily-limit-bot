import os
import sqlite3
from datetime import datetime, timedelta

from telegram import Update, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- ENV ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
PORT = int(os.environ.get("PORT", 10000))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# ---------------- DATABASE ----------------

conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS groups (
    group_id INTEGER PRIMARY KEY,
    message_limit INTEGER DEFAULT 3,
    mute_enabled INTEGER DEFAULT 1,
    mute_time TEXT DEFAULT '5m'
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER,
    group_id INTEGER,
    message_count INTEGER DEFAULT 0,
    extended_limit INTEGER DEFAULT NULL,
    is_special INTEGER DEFAULT 0,
    last_reset TEXT,
    PRIMARY KEY (user_id, group_id)
)
""")

conn.commit()

# ---------------- HELPERS ----------------

def reset_if_new_day(user_id, group_id):
    today = datetime.utcnow().date().isoformat()
    cur.execute("""
    SELECT last_reset FROM users
    WHERE user_id=? AND group_id=?
    """, (user_id, group_id))
    row = cur.fetchone()

    if row and row[0] != today:
        cur.execute("""
        UPDATE users
        SET message_count=0, last_reset=?
        WHERE user_id=? AND group_id=?
        """, (today, user_id, group_id))
        conn.commit()

def get_limit(user_id, group_id):
    cur.execute("SELECT message_limit FROM groups WHERE group_id=?", (group_id,))
    base = cur.fetchone()
    base_limit = base[0] if base else 3

    cur.execute("""
    SELECT extended_limit FROM users
    WHERE user_id=? AND group_id=?
    """, (user_id, group_id))
    row = cur.fetchone()

    if row and row[0]:
        return row[0]
    return base_limit

def parse_mute_time(mute_str):
    num = int(mute_str[:-1])
    unit = mute_str[-1]
    if unit == "m":
        return timedelta(minutes=num)
    if unit == "h":
        return timedelta(hours=num)
    if unit == "d":
        return timedelta(days=num)
    return timedelta(minutes=5)

# ---------------- MESSAGE TRACKER ----------------

async def track_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ["group", "supergroup"]:
        return

    group_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id

    # Group authorized?
    cur.execute("SELECT group_id FROM groups WHERE group_id=?", (group_id,))
    if not cur.fetchone():
        return

    today = datetime.utcnow().date().isoformat()

    cur.execute("""
    INSERT OR IGNORE INTO users(user_id, group_id, last_reset)
    VALUES(?,?,?)
    """, (user_id, group_id, today))
    conn.commit()

    reset_if_new_day(user_id, group_id)

    cur.execute("""
    UPDATE users SET message_count = message_count + 1
    WHERE user_id=? AND group_id=?
    """, (user_id, group_id))
    conn.commit()

    cur.execute("""
    SELECT message_count, is_special FROM users
    WHERE user_id=? AND group_id=?
    """, (user_id, group_id))
    count, is_special = cur.fetchone()

    if is_special:
        return

    limit = get_limit(user_id, group_id)

    if count > limit:
        cur.execute("""
        SELECT mute_enabled, mute_time FROM groups
        WHERE group_id=?
        """, (group_id,))
        row = cur.fetchone()

        if not row:
            return

        mute_enabled, mute_time = row

        await update.message.reply_html(
            f"⚠️ {user.mention_html()} আপনি মেসেজ লিমিট অতিক্রম করেছেন!"
        )

        if mute_enabled:
            delta = parse_mute_time(mute_time)
            until = datetime.utcnow() + delta

            permissions = ChatPermissions(can_send_messages=False)

            await context.bot.restrict_chat_member(
                group_id,
                user_id,
                permissions=permissions,
                until_date=until
            )

# ---------------- COMMANDS ----------------

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id
    user_id = int(context.args[0]) if context.args else update.effective_user.id

    cur.execute("""
    SELECT message_count FROM users
    WHERE user_id=? AND group_id=?
    """, (user_id, group_id))
    row = cur.fetchone()

    if not row:
        await update.message.reply_text("No data found.")
        return

    count = row[0]
    limit = get_limit(user_id, group_id)

    await update.message.reply_text(
        f"User ID: {user_id}\nTotal request: {count}/{limit}"
    )

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    group_id = int(context.args[0])
    cur.execute("INSERT OR IGNORE INTO groups(group_id) VALUES(?)", (group_id,))
    conn.commit()

    await update.message.reply_text("Group authorized.")

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Available Commands:\n\n"
        "/stats [id]\n"
        "/Sp_mem [id]\n"
        "/Ext_lim [id] [limit]\n"
        "/Mute on/off\n"
        "/Set_mute 5m/5h/5d\n"
        "/Add_grp [group_id]\n"
        "/cmd"
    )

# ---------------- MAIN ----------------

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_messages))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("Add_grp", add_group))
    application.add_handler(CommandHandler("cmd", cmd_list))

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",   # TOKEN না, simple path ব্যবহার করবো
        webhook_url=f"{RENDER_EXTERNAL_URL}/webhook",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
