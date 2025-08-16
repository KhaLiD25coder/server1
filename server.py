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

# ================== DATABASE HELPERS ==================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS licenses (
            key TEXT PRIMARY KEY,
            expiry_date INTEGER,
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

@app.post("/verify")
async def verify(data: dict):
    key = data.get("key")
    hwid = data.get("hwid")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT expiry_date, hwid FROM licenses WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()

    now_ts = int(datetime.datetime.utcnow().timestamp())
    if not row:
        return {"status": "error", "message": "Invalid key"}
    expiry, saved_hwid = row
    expiry = int(expiry) if expiry else None

    if expiry and expiry < now_ts:
        return {"status": "error", "message": "Key expired"}
    if saved_hwid and saved_hwid != hwid:
        return {"status": "error", "message": "HWID mismatch"}

    # update hwid if not already set
    if not saved_hwid:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE licenses SET hwid=? WHERE key=?", (hwid, key))
        conn.commit()
        conn.close()
        export_db_to_json()

    return {"status": "success", "message": "Key verified"}

# ================== DISCORD BOT ==================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    log.info(f"✅ Bot online as {bot.user}")
    try:
        synced_global = await bot.tree.sync()
        log.info(f"🌍 Synced {len(synced_global)} global commands")
    except Exception as e:
        log.error(f"❌ Failed to sync commands: {e}")

    # Print keys on startup
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    log.info("📜 Current Keys in Database (after startup):")
    if not rows:
        log.info("   (No keys found)")
    else:
        now_ts = int(datetime.datetime.utcnow().timestamp())
        for row in rows:
            expiry = int(row[1]) if row[1] else None
            if expiry and expiry < now_ts:
                status = "❌ Expired"
                exp_str = datetime.datetime.utcfromtimestamp(expiry).strftime("%Y-%m-%d")
            else:
                status = "✅ Active"
                exp_str = datetime.datetime.utcfromtimestamp(expiry).strftime("%Y-%m-%d") if expiry else "None"
            log.info(f"   {status} | 🔑 {row[0]} | Expiry: {exp_str} | HWID: {row[2]}")

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

    if not rows:
        await interaction.followup.send("No keys found.", ephemeral=True)
        return

    now_ts = int(datetime.datetime.utcnow().timestamp())
    active = []
    expired = []

    for row in rows:
        expiry = int(row[1]) if row[1] else None
        exp_str = datetime.datetime.utcfromtimestamp(expiry).strftime("%Y-%m-%d") if expiry else "None"
        if expiry and expiry < now_ts:
            expired.append(f"❌ {row[0]} | Expiry: {exp_str} | HWID: {row[2]}")
        else:
            active.append(f"✅ {row[0]} | Expiry: {exp_str} | HWID: {row[2]}")

    msg = "**✅ Active Keys:**\n" + "\n".join(active) if active else "No active keys."
    msg += "\n\n**❌ Expired Keys:**\n" + "\n".join(expired) if expired else "\n\nNo expired keys."

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
