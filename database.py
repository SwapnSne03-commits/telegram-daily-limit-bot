import os
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI")

client = MongoClient(MONGO_URI)

db = client["telegram_limit_bot"]

groups_col = db["groups"]
users_col = db["users"]
admins_col = db["stats_admins"]

force_config_col = db["force_config"]
force_channels_col = db["force_channels"]
force_verified_col = db["force_verified"]
force_pending_col = db["force_pending"]
force_muted_col = db["force_muted"]
