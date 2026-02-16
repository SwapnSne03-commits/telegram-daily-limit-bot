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
    rem_until TEXT DEFAULT NULL,
    last_reset TEXT,
    PRIMARY KEY (user_id, group_id)
)
""")

conn.commit()

# ---------------- HELPERS ----------------

def now():
    return datetime.utcnow()

def parse_time(time_str):
    num = int(time_str[:-1])
    unit = time_str[-1]
    if unit == "s":
        return timedelta(seconds=num)
    if unit == "m":
        return timedelta(minutes=num)
    if unit == "h":
        return timedelta(hours=num)
    if unit == "d":
        return timedelta(days=num)
    return timedelta(minutes=5)

def reset_if_new_day(user_id, group_id):
    today = now().date().isoformat()
    cur.execute("SELECT last_reset FROM users WHERE user_id=? AND group_id=?",
                (user_id, group_id))
    row = cur.fetchone()
    if row and row[0] != today:
        cur.execute("""
        UPDATE users SET message_count=0, last_reset=?
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

# ---------------- MESSAGE TRACKER ----------------

async def track_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ["group", "supergroup"]:
        return

    group_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id

    cur.execute("SELECT group_id FROM groups WHERE group_id=?", (group_id,))
    if not cur.fetchone():
        return

    today = now().date().isoformat()

    cur.execute("""
    INSERT OR IGNORE INTO users(user_id, group_id, last_reset)
    VALUES(?,?,?)
    """, (user_id, group_id, today))
    conn.commit()

    reset_if_new_day(user_id, group_id)

    cur.execute("""
    SELECT message_count, is_special, rem_until
    FROM users WHERE user_id=? AND group_id=?
    """, (user_id, group_id))
    count, is_special, rem_until = cur.fetchone()

    if rem_until:
        if now() < datetime.fromisoformat(rem_until):
            return

    if is_special:
        return

    count += 1
    cur.execute("""
    UPDATE users SET message_count=?
    WHERE user_id=? AND group_id=?
    """, (count, user_id, group_id))
    conn.commit()

    limit = get_limit(user_id, group_id)

    # Warning before max
    if count == limit:
        await update.message.reply_html(
            f"⚠️ {user.mention_html()} আর ১টা মেসেজ করলে limit শেষ হবে!"
        )

    # Exceeded
    if count > limit:
        cur.execute("SELECT mute_enabled, mute_time FROM groups WHERE group_id=?",
                    (group_id,))
        mute_enabled, mute_time = cur.fetchone()

        await update.message.reply_html(
            f"🚫 {user.mention_html()} আপনি আজকের limit শেষ করেছেন!"
        )

        if mute_enabled:
            until = now() + parse_time(mute_time)
            await context.bot.restrict_chat_member(
                group_id,
                user_id,
                permissions=ChatPermissions(can_send_messages=False),
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

    limit = get_limit(user_id, group_id)

    await update.message.reply_text(
        f"User: {user_id}\nTotal: {row[0]}/{limit}"
    )

async def sp_mem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    user_id = int(context.args[0])
    group_id = update.effective_chat.id
    cur.execute("UPDATE users SET is_special=1 WHERE user_id=? AND group_id=?",
                (user_id, group_id))
    conn.commit()
    await update.message.reply_text("Special member added.")

async def ext_lim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    user_id = int(context.args[0])
    limit = int(context.args[1])
    group_id = update.effective_chat.id
    cur.execute("""
    UPDATE users SET extended_limit=?
    WHERE user_id=? AND group_id=?
    """, (limit, user_id, group_id))
    conn.commit()
    await update.message.reply_text("Extended limit updated.")

async def mute_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    status = context.args[0].lower()
    group_id = update.effective_chat.id
    value = 1 if status == "on" else 0
    cur.execute("UPDATE groups SET mute_enabled=? WHERE group_id=?",
                (value, group_id))
    conn.commit()
    await update.message.reply_text(f"Mute {'enabled' if value else 'disabled'}.")

async def set_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    group_id = update.effective_chat.id
    cur.execute("UPDATE groups SET mute_time=? WHERE group_id=?",
                (context.args[0], group_id))
    conn.commit()
    await update.message.reply_text("Mute duration updated.")

async def rem_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    user_id = int(context.args[0])
    duration = parse_time(context.args[1])
    group_id = update.effective_chat.id
    until = now() + duration
    cur.execute("""
    UPDATE users SET rem_until=?
    WHERE user_id=? AND group_id=?
    """, (until.isoformat(), user_id, group_id))
    conn.commit()
    await update.message.reply_text("Temporary limit removed.")

async def renew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id

    if context.args[0].lower() == "all":
        if update.effective_user.id != OWNER_ID:
            return
        cur.execute("UPDATE users SET message_count=0")
        conn.commit()
        await update.message.reply_text("All users renewed.")
    else:
        user_id = int(context.args[0])
        cur.execute("""
        UPDATE users SET message_count=0
        WHERE user_id=? AND group_id=?
        """, (user_id, group_id))
        conn.commit()
        await update.message.reply_text("User renewed.")

async def grp_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    group_id = update.effective_chat.id
    new_limit = int(context.args[0])
    cur.execute("UPDATE groups SET message_limit=? WHERE group_id=?",
                (new_limit, group_id))
    conn.commit()
    await update.message.reply_text(f"Group limit set to {new_limit}.")

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    group_id = int(context.args[0])
    cur.execute("INSERT OR IGNORE INTO groups(group_id) VALUES(?)", (group_id,))
    conn.commit()
    await update.message.reply_text("Group authorized.")

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/stats\n"
        "/Sp_mem\n"
        "/Ext_lim\n"
        "/Mute on/off\n"
        "/Set_mute\n"
        "/rem_limit\n"
        "/renew\n"
        "/grp_setting\n"
        "/Add_grp\n"
        "/cmd"
    )

# ---------------- MAIN ----------------

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_messages))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("Sp_mem", sp_mem))
    application.add_handler(CommandHandler("Ext_lim", ext_lim))
    application.add_handler(CommandHandler("Mute", mute_toggle))
    application.add_handler(CommandHandler("Set_mute", set_mute))
    application.add_handler(CommandHandler("rem_limit", rem_limit))
    application.add_handler(CommandHandler("renew", renew))
    application.add_handler(CommandHandler("grp_setting", grp_setting))
    application.add_handler(CommandHandler("Add_grp", add_group))
    application.add_handler(CommandHandler("cmd", cmd_list))

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
        webhook_url=f"{RENDER_EXTERNAL_URL}/webhook",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
