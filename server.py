import os
import json
import sqlite3
import asyncio
import logging
from typing import Optional
from fastapi import FastAPI
import uvicorn
import discord
from discord.ext import commands
import datetime

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

@app.head("/")
async def head_root():
    return {}

# 🔑 License verification endpoint
@app.post("/verify")
async def verify(key: str, hwid: Optional[str] = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()

    if not row:
        return {"valid": False, "reason": "Key not found"}

    expiry = int(row[1]) if row[1] else None
    now_ts = int(datetime.datetime.utcnow().timestamp())

    if not expiry or expiry < now_ts:
        return {"valid": False, "reason": "Key expired"}

    if row[2] and hwid and row[2] != hwid:
        return {"valid": False, "reason": "HWID mismatch"}

    # If HWID not set yet, bind it
    if not row[2] and hwid:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE licenses SET hwid=? WHERE key=?", (hwid, key))
        conn.commit()
        conn.close()
        export_db_to_json()

    return {"valid": True, "expiry": expiry, "hwid": hwid or row[2]}

# ================== DISCORD BOT ==================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    log.info(f"✅ Bot online as {bot.user}")
    try:
        guild = discord.Object(id=GUILD_ID)

        # 🟢 Instant guild-only sync
        bot.tree.clear_commands(guild=guild)
        synced_guild = await bot.tree.sync(guild=guild)
        log.info(f"✅ Synced {len(synced_guild)} commands instantly to guild {GUILD_ID}")
    except Exception as e:
        log.error(f"❌ Failed to sync commands: {e}")

# ========== SLASH COMMANDS ==========
@bot.tree.command(name="listkeys", description="List all saved license keys")
async def listkeys(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    now_ts = int(datetime.datetime.utcnow().timestamp())
    active, expired = [], []

    for row in rows:
        expiry = int(row[1]) if row[1] else None
        exp_str = datetime.datetime.utcfromtimestamp(expiry).strftime("%Y-%m-%d") if expiry else "None"
        line = f"🔑 {row[0]} | Expiry: {exp_str} | HWID: {row[2]}"
        if expiry and expiry > now_ts:
            active.append(line)
        else:
            expired.append(line)

    msg = "**✅ Active Keys:**\n" + ("\n".join(active) if active else "None")
    msg += "\n\n**❌ Expired Keys:**\n" + ("\n".join(expired) if expired else "None")

    await interaction.followup.send(msg[:1900], ephemeral=True)

@bot.tree.command(name="addkey", description="Add a new license key")
async def addkey(interaction: discord.Interaction, key: str, days: int, hwid: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)

    expiry_date = int((datetime.datetime.utcnow() + datetime.timedelta(days=days)).timestamp())
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO licenses (key, expiry_date, hwid) VALUES (?, ?, ?)", (key, expiry_date, hwid))
    conn.commit()
    conn.close()
    export_db_to_json()

    exp_str = datetime.datetime.utcfromtimestamp(expiry_date).strftime("%Y-%m-%d")
    await interaction.followup.send(f"✅ Key `{key}` added! Expiry: {exp_str}", ephemeral=True)

@bot.tree.command(name="delkey", description="Delete a license key")
async def delkey(interaction: discord.Interaction, key: str):
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key FROM licenses WHERE key=?", (key,))
    exists = c.fetchone()
    if exists:
        c.execute("DELETE FROM licenses WHERE key=?", (key,))
        conn.commit()
        conn.close()
        export_db_to_json()
        await interaction.followup.send(f"🗑️ Key `{key}` deleted!", ephemeral=True)
    else:
        conn.close()
        await interaction.followup.send(f"⚠️ Key `{key}` not found.", ephemeral=True)

@bot.tree.command(name="resethwid", description="Reset the HWID for a license key")
async def resethwid(interaction: discord.Interaction, key: str):
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE licenses SET hwid=NULL WHERE key=?", (key,))
    conn.commit()
    conn.close()
    export_db_to_json()

    await interaction.followup.send(f"♻️ HWID reset for `{key}`!", ephemeral=True)

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

