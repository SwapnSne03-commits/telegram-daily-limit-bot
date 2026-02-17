import os
import sqlite3
from datetime import datetime, timedelta
from telegram.ext import ChatMemberHandler
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
LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID"))

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
CREATE TABLE IF NOT EXISTS stats_admins (
    user_id INTEGER PRIMARY KEY
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

# ---------------- LOGGING ----------------

async def send_log(context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        await context.bot.send_message(chat_id=LOG_CHAT_ID, text=text)
    except:
        pass

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

def is_up_admin(user_id):
    if user_id == OWNER_ID:
        return True
    cur.execute("SELECT user_id FROM stats_admins WHERE user_id=?", (user_id,))
    return cur.fetchone() is not None

async def ext_up(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_up_admin(update.effective_user.id):
        return

    group_id = update.effective_chat.id
    message = update.message

    target_id = None
    target_name = None
    new_limit = None

    # ---- Case 1: Reply ----
    if message.reply_to_message:
        target = message.reply_to_message.from_user
        target_id = target.id
        target_name = target.full_name

        if not context.args:
            await message.reply_text("Usage: reply + /ext_up [limit]")
            return

        try:
            new_limit = int(context.args[0])
        except:
            await message.reply_text("Invalid limit value.")
            return

    # ---- Case 2: Mention entity ----
    elif message.entities:
        for entity in message.entities:
            if entity.type == "text_mention":
                target_id = entity.user.id
                target_name = entity.user.full_name
                break
            elif entity.type == "mention":
                mention_text = message.text[entity.offset: entity.offset + entity.length]
                username_only = mention_text.replace("@", "")

                cur.execute("SELECT user_id FROM users WHERE group_id=?", (group_id,))
                all_users = cur.fetchall()

                for (uid,) in all_users:
                    try:
                        chat_member = await context.bot.get_chat_member(group_id, uid)
                        if chat_member.user.username == username_only:
                            target_id = uid
                            target_name = chat_member.user.full_name
                            break
                    except:
                        continue
                if target_id:
                    break

        if not target_id:
            await message.reply_text("User not found.")
            return

        # Extract limit from args (last argument)
        if context.args:
            try:
                new_limit = int(context.args[-1])
            except:
                await message.reply_text("Invalid limit value.")
                return
        else:
            await message.reply_text("Usage: /ext_up @user [limit]")
            return

    # ---- Case 3: ID দিয়ে ----
    elif context.args and len(context.args) >= 2:
        try:
            target_id = int(context.args[0])
            new_limit = int(context.args[1])
            target_name = str(target_id)
        except:
            await message.reply_text("Usage: /ext_up [id] [limit]")
            return

    else:
        await message.reply_text("Usage: reply বা /ext_up [id/@mention] [limit]")
        return

    # ---- Apply Extended Limit ----
    cur.execute("""
    UPDATE users SET extended_limit=?
    WHERE user_id=? AND group_id=?
    """, (new_limit, target_id, group_id))

    conn.commit()

    await message.reply_text(
        f"✅ {target_name} এর নতুন limit set করা হয়েছে: {new_limit}"
        )

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
            f"⚠️ <b>প্রিয় {user.mention_html()},\nআপনি কেবলমাত্র আর ১টি মুভি/সিরিজ রিকোয়েস্ট করতে পারবেন!\n\nধন্যবাদ🙏</b>"
        )

    # Exceeded
    if count > limit:
        cur.execute("SELECT mute_enabled, mute_time FROM groups WHERE group_id=?",
                    (group_id,))
        mute_enabled, mute_time = cur.fetchone()

        await update.message.reply_html(
            f"🚫 প্রিয় {user.mention_html()}\nআপনি আজকের সর্বোচ্চ Movie Request limit এ পৌঁছে গেছেন। আবার আগামীকাল Request করবেন!\n\n ধন্যবাদ"
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
async def bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.my_chat_member:
        chat = update.my_chat_member.chat

        # Log
        await send_log(
            context,
            f"➕ Bot added to group\nName: {chat.title}\nID: {chat.id}"
        )

        # Group message
        await context.bot.send_message(
            chat_id=chat.id,
            text="⚠️ This group is not authorized.\nOwner must use /Add_grp to activate bot."
        )

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /Add_grp [group_id]")
        return

    try:
        group_id = int(context.args[0])
    except:
        await update.message.reply_text("Invalid group ID.")
        return

    cur.execute("INSERT OR IGNORE INTO groups(group_id) VALUES(?)", (group_id,))
    conn.commit()

    await update.message.reply_text("Group authorized successfully.")

    # ---- LOG ----
    await send_log(
        context,
        f"✅ New Group Authorized\nGroup ID: {group_id}\nAuthorized By: {update.effective_user.full_name}\nUser ID: {update.effective_user.id}"
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_up_admin(update.effective_user.id):
        return

    group_id = update.effective_chat.id
    message = update.message

    user_id = None
    username = None

    # ---- Case 1: Reply ----
    if message.reply_to_message:
        target = message.reply_to_message.from_user
        user_id = target.id
        username = target.full_name

    # ---- Case 2: Mention entity (@username or text mention) ----
    elif message.entities:
        for entity in message.entities:
            if entity.type == "text_mention":
                user_id = entity.user.id
                username = entity.user.full_name
                break
            elif entity.type == "mention":
                mention_text = message.text[entity.offset: entity.offset + entity.length]
                username_only = mention_text.replace("@", "")

                # Try to find from database (if user already interacted)
                cur.execute("""
                SELECT user_id FROM users
                WHERE group_id=?
                """, (group_id,))
                all_users = cur.fetchall()

                for (uid,) in all_users:
                    try:
                        chat_member = await context.bot.get_chat_member(group_id, uid)
                        if chat_member.user.username == username_only:
                            user_id = uid
                            username = chat_member.user.full_name
                            break
                    except:
                        continue
                if user_id:
                    break

    # ---- Case 3: ID from argument ----
    if not user_id and context.args:
        try:
            user_id = int(context.args[0])
            username = str(user_id)
        except:
            await message.reply_text("Invalid user reference.")
            return

    # ---- Case 4: Self ----
    if not user_id:
        target = update.effective_user
        user_id = target.id
        username = target.full_name

    # ---- Fetch Data ----
    cur.execute("""
    SELECT message_count, extended_limit, is_special
    FROM users
    WHERE user_id=? AND group_id=?
    """, (user_id, group_id))

    row = cur.fetchone()

    if not row:
        await message.reply_text("No data found for this user.")
        return

    message_count, extended_limit, is_special = row

    limit = get_limit(user_id, group_id)
    remaining = max(limit - message_count, 0)

    special_status = "Yes" if is_special else "No"
    ext_text = extended_limit if extended_limit else "No"

    await message.reply_text(
        f"📊 User Stats\n\n"
        f"Name: {username}\n"
        f"User ID: {user_id}\n\n"
        f"Used: {message_count}/{limit}\n"
        f"Remaining: {remaining}\n\n"
        f"Extended Limit: {ext_text}\n"
        f"Special Member: {special_status}"
    )

async def up_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /up_admin [user_id]")
        return

    user_id = int(context.args[0])

    cur.execute("INSERT OR IGNORE INTO stats_admins(user_id) VALUES(?)", (user_id,))
    conn.commit()

    await update.message.reply_text("User promoted to Stats Admin.")

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

    if not context.args:
        await update.message.reply_text("Usage: /Mute on/off")
        return

    status = context.args[0].lower()

    if status not in ["on", "off"]:
        await update.message.reply_text("Usage: /Mute on/off")
        return

    group_id = update.effective_chat.id
    value = 1 if status == "on" else 0

    cur.execute("UPDATE groups SET mute_enabled=? WHERE group_id=?",
                (value, group_id))
    conn.commit()

    await update.message.reply_text(
        f"Mute {'enabled' if value else 'disabled'}."
    )

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

    if not context.args:
        await update.message.reply_text("Usage: /renew [id/all]")
        return

    if context.args[0].lower() == "all":
        if update.effective_user.id != OWNER_ID:
            return
        cur.execute("UPDATE users SET message_count=0")
        conn.commit()
        await update.message.reply_text("All users renewed.")
    else:
        try:
            user_id = int(context.args[0])
        except:
            await update.message.reply_text("Invalid user ID.")
            return

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

async def post_init(application):
    await application.bot.send_message(
        chat_id=LOG_CHAT_ID,
        text="🚀 Bot restarted successfully."
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_log(
        context,
        f"👤 User started bot\nName: {update.effective_user.full_name}\nID: {update.effective_user.id}"
    )
    await update.message.reply_text("Bot active.")

# ---------------- MAIN ----------------

def main():
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_messages))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("ext_up", ext_up))
    application.add_handler(CommandHandler("Sp_mem", sp_mem))
    application.add_handler(CommandHandler("Ext_lim", ext_lim))
    application.add_handler(CommandHandler("Mute", mute_toggle))
    application.add_handler(CommandHandler("Set_mute", set_mute))
    application.add_handler(CommandHandler("rem_limit", rem_limit))
    application.add_handler(CommandHandler("renew", renew))
    application.add_handler(CommandHandler("grp_setting", grp_setting))
    application.add_handler(CommandHandler("Add_grp", add_group))
    application.add_handler(CommandHandler("cmd", cmd_list))
    application.add_handler(ChatMemberHandler(bot_added, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("up_admin", up_admin))

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
        webhook_url=f"{RENDER_EXTERNAL_URL}/webhook",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
