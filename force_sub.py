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
from datetime import datetime, timedelta, timezone
from telegram import ChatJoinRequest

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
    force_pending_col.delete_many({"group_id": group_id})

    await update.message.reply_text(
        "Verification cache cleared.\n"
        "Users will be checked again."
    )


async def unmute_user(context: ContextTypes.DEFAULT_TYPE):
    job = context.job

    await context.bot.restrict_chat_member(
        chat_id=job.data["group_id"],
        user_id=job.data["user_id"],
        permissions=ChatPermissions(
            can_send_messages=True
        )
    )

async def force_temp_mute(context, group_id, user_id):
    from datetime import datetime, timedelta, timezone

    until = datetime.now(timezone.utc) + timedelta(seconds=30)

    try:
        # Apply restriction (Force Sub only)
        await context.bot.restrict_chat_member(
            chat_id=group_id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until
        )

        # 🔒 Save in DB (Force-only tracking)
        force_muted_col.update_one(
            {
                "user_id": user_id,
                "group_id": group_id
            },
            {
                "$set": {
                    "force_muted": True,
                    "muted_at": datetime.utcnow()
                }
            },
            upsert=True
        )

    except:
        return

    # ⏳ Always schedule unmute (guard system)
    context.job_queue.run_once(
        force_auto_unmute,
        30,
        data={"group_id": group_id, "user_id": user_id}
    )

async def force_auto_unmute(context):
    job = context.job
    group_id = job.data["group_id"]
    user_id = job.data["user_id"]

    try:
        # Restore FULL permissions safely
        await context.bot.restrict_chat_member(
            chat_id=group_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
    except:
        pass

    # 🧹 Clean DB record
    force_muted_col.delete_one({
        "user_id": user_id,
        "group_id": group_id
    })

async def force_unmute_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    group_id = update.effective_chat.id

    muted_users = list(force_muted_col.find({
        "group_id": group_id,
        "force_muted": True
    }))

    count = 0

    for user in muted_users:
        try:
            await context.bot.restrict_chat_member(
                chat_id=group_id,
                user_id=user["user_id"],
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_audios=True,
                    can_send_documents=True,
                    can_send_photos=True,
                    can_send_videos=True,
                    can_send_video_notes=True,
                    can_send_voice_notes=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True
                )
            )
            count += 1
        except:
            pass

    # 🔥 Important: Delete ALL force mute records for this group
    force_muted_col.delete_many({
        "group_id": group_id
    })

    await update.message.reply_text(
        f"✅ {count} Force-Sub muted users unmuted & database cleaned."
    )

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    join_request: ChatJoinRequest = update.chat_join_request

    user_id = join_request.from_user.id
    channel_id = join_request.chat.id

    # Only track if this channel is used in force sub
    channel_data = force_channels_col.find_one({
        "channel_id": channel_id,
        "type": "req"
    })

    if not channel_data:
        return

    # Save pending request
    force_pending_col.update_one(
        {
            "user_id": user_id,
            "group_id": channel_data["group_id"],
            "channel_id": channel_id
        },
        {
            "$set": {
                "requested": True,
                "requested_at": datetime.utcnow()
            }
        },
        upsert=True
    )

async def handle_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_member = update.chat_member

    user_id = chat_member.from_user.id
    channel_id = chat_member.chat.id

    # Only check request-type channels
    channel_data = force_channels_col.find_one({
        "channel_id": channel_id,
        "type": "req"
    })

    if not channel_data:
        return

    # If user left / rejected
    if chat_member.new_chat_member.status in ["left", "kicked"]:
        force_pending_col.delete_many({
            "user_id": user_id,
            "group_id": channel_data["group_id"],
            "channel_id": channel_id
        })

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

    channels = list(force_channels_col.find({
        "group_id": group_id,
        "active": True
    }))

    if not channels:
        return


    # ---------------- CHANNEL CHECK ----------------
    not_joined = []

    for ch in channels:
        try:
            member = await context.bot.get_chat_member(
                ch["channel_id"],
                user.id
            )

            status = member.status

            # ✅ Only these mean user truly joined
            if status in ["member", "administrator", "creator"]:
                continue

            # ❌ Everything else means NOT joined
            if ch["type"] == "req":

                pending = force_pending_col.find_one({
                    "user_id": user.id,
                    "group_id": group_id,
                    "channel_id": ch["channel_id"]
                })

                # If real pending exists → allow
                if pending:
                    continue

            # If direct channel OR no valid pending → must join
            not_joined.append(ch)

        except:
            # If API fails, treat as not joined (safe side)
            not_joined.append(ch)
    # ------------------------------------------------


    # ✅ If user joined all required channels
    if not not_joined:

        already_verified = force_verified_col.find_one({
            "user_id": user.id,
            "group_id": group_id
        })

        # Only send greeting first time
        if not already_verified:

            force_verified_col.update_one(
                {"user_id": user.id, "group_id": group_id},
                {"$set": {"verified": True, "verified_at": datetime.utcnow()}},
                upsert=True
            )

            force_pending_col.delete_many({
                "user_id": user.id,
                "group_id": group_id
            })

            msg = await context.bot.send_message(
                chat_id=group_id,
                text=(
                    f"🎉 <b> Hey {user.mention_html()}</b>\n\n"
                    "<b>আমাদের চ্যানেলগুলি জয়েন করার জন্য আপনাকে অসংখ্য ধন্যবাদ 🙏.\n"
                    "এভাবেই আমাদের পাশে থাকুন ও সুস্থ থাকুন ✨</b>"
                ),
                parse_mode="HTML"
            )

            context.job_queue.run_once(
                lambda ctx: ctx.bot.delete_message(
                    chat_id=group_id,
                    message_id=msg.message_id
                ),
                when=50
            )

        return


    # ❌ User still not joined → always enforce
    try:
        await update.message.delete()
    except:
        pass

    # 30 sec temporary mute (your existing system)
    await force_temp_mute(context, group_id, user.id)

    buttons = []

    for ch in not_joined:
        if ch["type"] == "req":
            invite = await context.bot.create_chat_invite_link(
                ch["channel_id"],
                creates_join_request=True
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

    context.job_queue.run_once(
        lambda ctx: ctx.bot.delete_message(
            chat_id=group_id,
            message_id=warn_msg.message_id
        ),
        when=50
    )
