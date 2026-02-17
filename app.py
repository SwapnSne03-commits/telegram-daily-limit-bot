import os
from datetime import datetime, timedelta

from telegram import Update, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    CallbackQueryHandler,
    ChatMemberHandler
)

# Force Sub System
from force_sub import (
    sub_force,
    choose_type,
    save_channel,
    remove_channel,
    force_remove,
    clear_req,
    check_force,
    CHOOSING_TYPE,
    WAITING_CHANNEL_ID
)

# Database Collections
from database import (
    groups_col,
    users_col,
    admins_col,
    force_config_col,
    force_channels_col,
    force_verified_col
)
# ---------------- ENV ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
PORT = int(os.environ.get("PORT", 10000))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID"))

# ---------------- DATABASE ----------------
# ---------------- DATABASE (MongoDB) ----------------


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

    user = users_col.find_one({
        "user_id": user_id,
        "group_id": group_id
    })

    if user and user.get("last_reset") != today:
        users_col.update_one(
            {"user_id": user_id, "group_id": group_id},
            {"$set": {
                "message_count": 0,
                "last_reset": today
            }}
        )

def is_up_admin(user_id):
    if user_id == OWNER_ID:
        return True

    admin = admins_col.find_one({"user_id": user_id})
    return admin is not None

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

        if not target_id:
            await message.reply_text("Reply or mention a valid user.")
            return

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
        await message.reply_text("Usage: reply or /ext_up [id/@mention] [limit]")
        return

    # ---- Ensure user exists in DB ----
    users_col.update_one(
        {"user_id": target_id, "group_id": group_id},
        {"$setOnInsert": {
            "user_id": target_id,
            "group_id": group_id,
            "message_count": 0,
            "extended_limit": None,
            "is_special": False,
            "rem_until": None,
            "last_reset": now().date().isoformat()
        }},
        upsert=True
    )

    # ---- Apply Extended Limit ----
    users_col.update_one(
        {"user_id": target_id, "group_id": group_id},
        {"$set": {"extended_limit": new_limit}}
    )

    await message.reply_text(
        f"✅ {target_name} এর নতুন limit set করা হয়েছে: {new_limit}"
    )  

def get_limit(user_id, group_id):
    # Get group base limit
    group = groups_col.find_one({"group_id": group_id})
    base_limit = group["message_limit"] if group and "message_limit" in group else 3

    # Get user extended limit
    user = users_col.find_one({
        "user_id": user_id,
        "group_id": group_id
    })

    if user and user.get("extended_limit"):
        return user["extended_limit"]

    return base_limit

# ---------------- MESSAGE TRACKER ----------------

async def track_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ["group", "supergroup"]:
        return

    group_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id

    # ---- Check if group authorized ----
    group = groups_col.find_one({"group_id": group_id})
    if not group:
        return

    today = now().date().isoformat()

    # ---- Ensure user exists ----
    users_col.update_one(
        {"user_id": user_id, "group_id": group_id},
        {"$setOnInsert": {
            "user_id": user_id,
            "group_id": group_id,
            "message_count": 0,
            "extended_limit": None,
            "is_special": False,
            "rem_until": None,
            "last_reset": today
        }},
        upsert=True
    )

    # ---- Daily reset check ----
    reset_if_new_day(user_id, group_id)

    user_data = users_col.find_one({
        "user_id": user_id,
        "group_id": group_id
    })

    count = user_data.get("message_count", 0)
    is_special = user_data.get("is_special", False)
    rem_until = user_data.get("rem_until")

    # ---- Temporary unlimited check ----
    if rem_until:
        if now() < datetime.fromisoformat(rem_until):
            return

    # ---- Special member bypass ----
    if is_special:
        return

    # ---- Increase message count ----
    count += 1

    users_col.update_one(
        {"user_id": user_id, "group_id": group_id},
        {"$set": {"message_count": count}}
    )

    limit = get_limit(user_id, group_id)

    # ---- Warning before max ----
    if count == limit:
        await update.message.reply_html(
            f"⚠️ <b>প্রিয় {user.mention_html()},\nআপনি কেবলমাত্র আর ১টি মুভি/সিরিজ রিকোয়েস্ট করতে পারবেন!\n\nধন্যবাদ🙏</b>"
        )

    # ---- Exceeded ----
    if count > limit:
        mute_enabled = group.get("mute_enabled", 1)
        mute_time = group.get("mute_time", "5m")

        await update.message.reply_html(
            f"🚫 প্রিয় {user.mention_html()}\nআপনি আজকের সর্বোচ্চ Movie Request limit এ পৌঁছে গেছেন। আবার আগামীকাল Request করবেন!\n\nধন্যবাদ"
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

    # ---- Insert or ensure group exists ----
    groups_col.update_one(
        {"group_id": group_id},
        {"$setOnInsert": {
            "group_id": group_id,
            "message_limit": 3,
            "mute_enabled": 1,
            "mute_time": "5m"
        }},
        upsert=True
    )

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

    # ---- Case 2: text_mention ----
    elif message.entities:
        for entity in message.entities:
            if entity.type == "text_mention":
                user_id = entity.user.id
                username = entity.user.full_name
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
    user_data = users_col.find_one({
        "user_id": user_id,
        "group_id": group_id
    })

    if not user_data:
        await message.reply_text("No data found for this user.")
        return

    message_count = user_data.get("message_count", 0)
    extended_limit = user_data.get("extended_limit")
    is_special = user_data.get("is_special", False)

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

    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_text("Invalid user ID.")
        return

    admins_col.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id}},
        upsert=True
    )

    await update.message.reply_text("User promoted to Stats Admin.")

async def sp_mem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /Sp_mem [user_id]")
        return

    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_text("Invalid user ID.")
        return

    group_id = update.effective_chat.id

    # Ensure user document exists
    users_col.update_one(
        {"user_id": user_id, "group_id": group_id},
        {"$setOnInsert": {
            "user_id": user_id,
            "group_id": group_id,
            "message_count": 0,
            "extended_limit": None,
            "is_special": False,
            "rem_until": None,
            "last_reset": now().date().isoformat()
        }},
        upsert=True
    )

    # Set special member
    users_col.update_one(
        {"user_id": user_id, "group_id": group_id},
        {"$set": {"is_special": True}}
    )

    await update.message.reply_text("Special member added.")

async def ext_lim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /Ext_lim [user_id] [limit]")
        return

    try:
        user_id = int(context.args[0])
        limit = int(context.args[1])
    except:
        await update.message.reply_text("Invalid input.")
        return

    group_id = update.effective_chat.id

    # Ensure user document exists
    users_col.update_one(
        {"user_id": user_id, "group_id": group_id},
        {"$setOnInsert": {
            "user_id": user_id,
            "group_id": group_id,
            "message_count": 0,
            "extended_limit": None,
            "is_special": False,
            "rem_until": None,
            "last_reset": now().date().isoformat()
        }},
        upsert=True
    )

    # Update extended limit
    users_col.update_one(
        {"user_id": user_id, "group_id": group_id},
        {"$set": {"extended_limit": limit}}
    )

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

    # Ensure group exists
    groups_col.update_one(
        {"group_id": group_id},
        {"$setOnInsert": {
            "group_id": group_id,
            "message_limit": 3,
            "mute_enabled": 1,
            "mute_time": "5m"
        }},
        upsert=True
    )

    # Update mute status
    groups_col.update_one(
        {"group_id": group_id},
        {"$set": {"mute_enabled": value}}
    )

    await update.message.reply_text(
        f"Mute {'enabled' if value else 'disabled'}."
    )

async def set_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /Set_mute 5m/5h/5d")
        return

    group_id = update.effective_chat.id
    mute_value = context.args[0]

    # Ensure group exists
    groups_col.update_one(
        {"group_id": group_id},
        {"$setOnInsert": {
            "group_id": group_id,
            "message_limit": 3,
            "mute_enabled": 1,
            "mute_time": "5m"
        }},
        upsert=True
    )

    # Update mute time
    groups_col.update_one(
        {"group_id": group_id},
        {"$set": {"mute_time": mute_value}}
    )

    await update.message.reply_text("Mute duration updated.")

async def rem_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /rem_limit [user_id] [5m/5h/5d]")
        return

    try:
        user_id = int(context.args[0])
        duration = parse_time(context.args[1])
    except:
        await update.message.reply_text("Invalid input.")
        return

    group_id = update.effective_chat.id
    until = now() + duration

    # Ensure user document exists
    users_col.update_one(
        {"user_id": user_id, "group_id": group_id},
        {"$setOnInsert": {
            "user_id": user_id,
            "group_id": group_id,
            "message_count": 0,
            "extended_limit": None,
            "is_special": False,
            "rem_until": None,
            "last_reset": now().date().isoformat()
        }},
        upsert=True
    )

    # Set temporary removal
    users_col.update_one(
        {"user_id": user_id, "group_id": group_id},
        {"$set": {"rem_until": until.isoformat()}}
    )

    await update.message.reply_text("Temporary limit removed.")

async def renew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text("Usage: /renew [id/all]")
        return

    # ---- Renew All ----
    if context.args[0].lower() == "all":
        if update.effective_user.id != OWNER_ID:
            return

        users_col.update_many(
            {"group_id": group_id},
            {"$set": {"message_count": 0}}
        )

        await update.message.reply_text("All users renewed.")
        return

    # ---- Renew Single User ----
    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_text("Invalid user ID.")
        return

    users_col.update_one(
        {"user_id": user_id, "group_id": group_id},
        {"$set": {"message_count": 0}}
    )

    await update.message.reply_text("User renewed.")

async def grp_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /grp_setting [limit]")
        return

    try:
        new_limit = int(context.args[0])
    except:
        await update.message.reply_text("Invalid limit value.")
        return

    group_id = update.effective_chat.id

    # Ensure group exists
    groups_col.update_one(
        {"group_id": group_id},
        {"$setOnInsert": {
            "group_id": group_id,
            "message_limit": 3,
            "mute_enabled": 1,
            "mute_time": "5m"
        }},
        upsert=True
    )

    # Update limit
    groups_col.update_one(
        {"group_id": group_id},
        {"$set": {"message_limit": new_limit}}
    )

    await update.message.reply_text(f"Group limit set to {new_limit}.")

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/stats\n"
        "/up_admin\n"
        "/ext_up\n"
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

    # -------- Force Sub Conversation --------
    conv = ConversationHandler(
        entry_points=[CommandHandler("Sub_force", sub_force)],
        states={
            CHOOSING_TYPE: [CallbackQueryHandler(choose_type)],
            WAITING_CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_channel)],
        },
        fallbacks=[]
    )

    application.add_handler(conv)

    application.add_handler(CommandHandler("remove_chnl", remove_channel))
    application.add_handler(CommandHandler("force_remove", force_remove))
    application.add_handler(CommandHandler("clear_req", clear_req))

    # -------- IMPORTANT FIX --------
    # -------- Force Sub First (priority 0) --------
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, check_force),
        group=0
    )

    # -------- Limit System After (priority 1) --------
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, track_messages),
        group=1
    )

    # -------- Commands --------
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
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("up_admin", up_admin))

    application.add_handler(
        ChatMemberHandler(bot_added, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
        webhook_url=f"{RENDER_EXTERNAL_URL}/webhook",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
