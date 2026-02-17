from datetime import datetime, timedelta
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatPermissions,
    Update
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)

# ===== Import Database & Config =====
from database import (
    users_col,
    force_config_col,
    force_channels_col,
    force_verified_col,
    force_pending_col
)

import os

OWNER_ID = int(os.getenv("OWNER_ID"))

# ================= Conversation States =================
CHOOSING_TYPE, WAITING_CHANNEL_ID = range(2)


# ================= OWNER PANEL =================

async def sub_force(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if update.effective_chat.type == "private":
        await update.message.reply_text("Use this command inside a group.")
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Request Sub Channel", callback_data="req"),
            InlineKeyboardButton("Direct Sub Channel", callback_data="direct")
        ]
    ])

    await update.message.reply_text(
        "Force Subscribe Setup\n\n"
        "• Request Sub → Join request required (you approve)\n"
        "• Direct Sub → Instant join link\n\n"
        "Choose type:",
        reply_markup=keyboard
    )

    return CHOOSING_TYPE


async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["sub_type"] = query.data

    await query.message.reply_text(
        "Send Channel ID.\n\n"
        "Tip: Bot must be admin in that channel."
    )

    return WAITING_CHANNEL_ID


async def save_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return ConversationHandler.END

    try:
        channel_id = int(update.message.text)
    except:
        await update.message.reply_text("Invalid Channel ID.")
        return ConversationHandler.END

    group_id = update.effective_chat.id
    sub_type = context.user_data.get("sub_type")

    force_channels_col.insert_one({
        "group_id": group_id,
        "channel_id": channel_id,
        "type": sub_type,
        "active": True
    })

    force_config_col.update_one(
        {"group_id": group_id},
        {"$set": {"enabled": True}},
        upsert=True
    )

    await update.message.reply_text(
        "Force channel added.\n\n"
        "Tip: Use /clear_req if adding new channels."
    )

    return ConversationHandler.END


# ================= REMOVE & CONTROL =================

async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /remove_chnl [channel_id]")
        return

    try:
        channel_id = int(context.args[0])
    except:
        await update.message.reply_text("Invalid Channel ID.")
        return

    group_id = update.effective_chat.id

    force_channels_col.delete_one({
        "group_id": group_id,
        "channel_id": channel_id
    })

    await update.message.reply_text("Channel removed from this group.")


async def force_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    group_id = update.effective_chat.id

    force_config_col.update_one(
        {"group_id": group_id},
        {"$set": {"enabled": False}},
        upsert=True
    )

    await update.message.reply_text("Force Subscribe disabled for this group.")


async def clear_req(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    group_id = update.effective_chat.id
    force_verified_col.delete_many({"group_id": group_id})

    await update.message.reply_text(
        "Verification cache cleared.\n"
        "Users will be checked again."
    )


# ================= MAIN CHECK =================

async def check_force(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ["group", "supergroup"]:
        return

    group_id = update.effective_chat.id
    user = update.effective_user

    if user.id == OWNER_ID:
        return

    # Force enabled?
    config = force_config_col.find_one({"group_id": group_id})
    if not config or not config.get("enabled"):
        return

    # Special member bypass
    special = users_col.find_one({
        "user_id": user.id,
        "group_id": group_id,
        "is_special": True
    })
    if special:
        return

    # Already verified?
    if force_verified_col.find_one({
        "user_id": user.id,
        "group_id": group_id
    }):
        return

    channels = list(force_channels_col.find({
        "group_id": group_id,
        "active": True
    }))

    if not channels:
        return
    # ----- Pending Request Detect -----
    pending_exists = False

    for ch in channels:
        pending = force_pending_col.find_one({
            "user_id": user.id,
            "group_id": group_id,
            "channel_id": ch["channel_id"],
            "requested": True
        })

        if pending:
            pending_exists = True
            break

    if pending_exists:
        return
    not_joined = []

    for ch in channels:
        try:
            member = await context.bot.get_chat_member(ch["channel_id"], user.id)
            if member.status in ["left", "kicked"]:
                not_joined.append(ch)
        except:
            not_joined.append(ch)

    if not not_joined:
        # Mark verified
        force_verified_col.update_one(
            {"user_id": user.id, "group_id": group_id},
            {"$set": {"verified": True}},
            upsert=True
        )

        # Send greeting message
        msg = await context.bot.send_message(
            chat_id=group_id,
            text=(
                f"🎉 Hey {user.mention_html()}\n\n"
                "<b>আমাদের চ্যানেলগুলি জয়েন করার জন্য আপনাকে অসংখ্য ধন্যবাদ 🙏.\n"
                "এবার আপনি Request করতে পারেন..</b>"
            ),
            parse_mode="HTML"
        )

        # Auto delete after 50 sec
        context.job_queue.run_once(
            lambda ctx: ctx.bot.delete_message(chat_id=group_id, message_id=msg.message_id),
            when=50
        )
        return
    # Delete message
    try:
        await update.message.delete()
    except:
        pass

    # Mute 30 seconds
    until = datetime.utcnow() + timedelta(seconds=30)

    await context.bot.restrict_chat_member(
        group_id,
        user.id,
        permissions=ChatPermissions(can_send_messages=False),
        until_date=until
    )

    # Create buttons
    buttons = []

    for ch in not_joined:
        if ch["type"] == "req":
            invite = await context.bot.create_chat_invite_link(
                ch["channel_id"],
                creates_join_request=True
            )
            # ----- Mark Pending Request -----
            force_pending_col.update_one(
                {
                    "user_id": user.id,
                    "group_id": group_id,
                    "channel_id": ch["channel_id"]
                },
                {
                    "$set": {
                        "requested": True,
                        "requested_at": datetime.utcnow()
                    }
                },
                upsert=True
            )
        else:
            invite = await context.bot.create_chat_invite_link(
                ch["channel_id"]
            )

        buttons.append([
            InlineKeyboardButton("ᴊᴏɪɴ ᴏᴜʀ ᴄʜᴀɴɴᴇʟ", url=invite.invite_link)
        ])

    keyboard = InlineKeyboardMarkup(buttons)

    warn_msg = await context.bot.send_message(
        chat_id=group_id,
        text=(
            f"⚠️ {user.mention_html()}\n\n"
            "<b>Request করার আগে আপনাকে নিচে দেওয়া চ্যানেলগুলি অবশ্যই Join করতে হবে।\n\n"
            "জয়েন করার পর আবার Request করুন।\n"
            "আমরা আপনার Request এর জন্য অপেক্ষায় আছি..!!</b>"
        ),
        reply_markup=keyboard,
        parse_mode="HTML"
    )

    # Auto delete after 50 sec
    context.job_queue.run_once(
        lambda ctx: ctx.bot.delete_message(chat_id=group_id, message_id=warn_msg.message_id),
        when=50
    )
