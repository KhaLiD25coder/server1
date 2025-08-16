import os
import json
import sqlite3
import asyncio
import logging
import datetime
from typing import Optional
from fastapi import FastAPI
import uvicorn
import discord
from discord.ext import commands

# ================== CONFIG ==================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1394437999596404748"))

DB_PATH = "licenses.db"
JSON_PATH = "licenses.json"

if not DISCORD_BOT_TOKEN:
    raise ValueError("❌ DISCORD_BOT_TOKEN not set in environment variables")

# ================== LOGGING ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("LicenseBot")

# ================== DATE HELPER ==================
def parse_expiry_to_ts(v):
    """Accepts int timestamp, ISO date 'YYYY-MM-DD', or None -> int|None"""
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    s = str(v).strip()
    if s.isdigit():  # plain int string
        return int(s)
    try:
        dt = datetime.datetime.strptime(s, "%Y-%m-%d")
        return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
    except ValueError:
        return None

# ================== DATABASE HELPERS ==================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS licenses (
            key TEXT PRIMARY KEY,
            expiry_date TEXT,
            hwid TEXT
        )"""
    )
    conn.commit()
    conn.close()

def import_json_to_db():
    if not os.path.exists(JSON_PATH):
        return
    with open(JSON_PATH, "r") as f:
        data = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for key, info in data.items():
        expiry = info.get("expiry_date")
        hwid = info.get("hwid")
        c.execute("INSERT OR REPLACE INTO licenses (key, expiry_date, hwid) VALUES (?, ?, ?)", (key, expiry, hwid))
    conn.commit()
    conn.close()

def export_db_to_json():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT key, expiry_date, hwid FROM licenses")
        rows = c.fetchall()
        conn.close()

        data = {row[0]: {"expiry_date": row[1], "hwid": row[2]} for row in rows}
        with open(JSON_PATH, "w") as f:
            json.dump(data, f, indent=2)

        log.info("💾 licenses.json updated successfully.")
    except Exception as e:
        log.error(f"❌ Failed to export DB to JSON: {e}")

# ================== FASTAPI APP ==================
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "Bot + API running"}

# ================== DISCORD BOT ==================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    log.info(f"✅ Bot online as {bot.user}")
    try:
        guild = discord.Object(id=GUILD_ID)
        synced_guild = await bot.tree.sync(guild=guild)
        log.info(f"✅ Synced {len(synced_guild)} commands to guild {GUILD_ID}")
        for cmd in synced_guild:
            log.info(f"   • /{cmd.name} — {cmd.description}")

        synced_global = await bot.tree.sync()
        log.info(f"🌍 Synced {len(synced_global)} global commands")
    except Exception as e:
        log.error(f"❌ Failed to sync commands: {e}")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    log.info("📜 Current Keys in Database (after startup):")
    if not rows:
        log.info("   (No keys found)")
    else:
        for row in rows:
            exp_ts = parse_expiry_to_ts(row[1])
            status = "❌ Expired" if exp_ts and exp_ts < now_ts else "✅ Active"
            exp_str = (
                datetime.datetime.fromtimestamp(exp_ts, datetime.timezone.utc).strftime("%Y-%m-%d")
                if exp_ts else "None"
            )
            log.info(f"   🔑 {row[0]} | Expiry: {exp_str} | HWID: {row[2]} | {status}")

# ========== SLASH COMMANDS ==========
@bot.tree.command(name="listkeys", description="List all saved license keys")
async def listkeys(interaction: discord.Interaction):
    log.info("🟡 /listkeys triggered")
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    active, expired = [], []

    for row in rows:
        exp_ts = parse_expiry_to_ts(row[1])
        exp_str = (
            datetime.datetime.fromtimestamp(exp_ts, datetime.timezone.utc).strftime("%Y-%m-%d")
            if exp_ts else "None"
        )
        entry = f"🔑 {row[0]} | Expiry: {exp_str} | HWID: {row[2]}"
        if exp_ts and exp_ts < now_ts:
            expired.append(entry)
        else:
            active.append(entry)

    msg = ""
    if active:
        msg += "✅ **Active Keys:**\n" + "\n".join(active) + "\n\n"
    if expired:
        msg += "❌ **Expired Keys:**\n" + "\n".join(expired)

    if not msg:
        msg = "No keys found."

    await interaction.followup.send(msg[:1900], ephemeral=True)

# ================== MAIN ==================
async def main():
    init_db()
    import_json_to_db()

    loop = asyncio.get_event_loop()

    api_task = loop.create_task(
        uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=10000, log_level="info")).serve()
    )
    bot_task = loop.create_task(bot.start(DISCORD_BOT_TOKEN))

    await asyncio.gather(api_task, bot_task)

if __name__ == "__main__":
    asyncio.run(main())

