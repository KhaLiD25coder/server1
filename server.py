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

    log.info("📜 Current Keys in Database (after startup):")
    if not rows:
        log.info("   (No keys found)")
    else:
        for row in rows:
            log.info(f"   🔑 {row[0]} | Expiry: {row[1]} | HWID: {row[2]}")

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

    msg = "\n".join([f"🔑 {row[0]} | Expiry: {row[1]} | HWID: {row[2]}" for row in rows])
    await interaction.followup.send(msg[:1900], ephemeral=True)
    log.info("🟡 Sent list of keys")

@bot.tree.command(name="addkey", description="Add a new license key")
async def addkey(interaction: discord.Interaction, key: Optional[str] = "TEST-KEY", expiry_date: Optional[int] = 1760000000, hwid: Optional[str] = None):
    log.info("🟡 /addkey triggered")
    await interaction.response.defer(ephemeral=True)

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO licenses (key, expiry_date, hwid) VALUES (?, ?, ?)", (key, expiry_date, hwid))
        conn.commit()
        conn.close()
        export_db_to_json()

        await interaction.followup.send(f"✅ Key `{key}` added!", ephemeral=True)
        log.info(f"🟡 Added key {key}")
    except Exception as e:
        log.error(f"❌ Error in /addkey: {e}")
        await interaction.followup.send("⚠️ Failed to add key", ephemeral=True)

@bot.tree.command(name="delkey", description="Delete a license key")
async def delkey(interaction: discord.Interaction, key: Optional[str] = "TEST-KEY"):
    log.info("🟡 /delkey triggered")
    await interaction.response.defer(ephemeral=True)

    try:
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
            log.info(f"🟡 Deleted key {key}")
        else:
            conn.close()
            await interaction.followup.send(f"⚠️ Key `{key}` not found.", ephemeral=True)
            log.info("🟡 Key not found in DB")
    except Exception as e:
        log.error(f"❌ Error in /delkey: {e}")
        await interaction.followup.send("⚠️ Failed to delete key", ephemeral=True)

@bot.tree.command(name="resethwid", description="Reset the HWID for a license key")
async def resethwid(interaction: discord.Interaction, key: Optional[str] = "TEST-KEY"):
    log.info("🟡 /resethwid triggered")
    await interaction.response.defer(ephemeral=True)

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE licenses SET hwid=NULL WHERE key=?", (key,))
        conn.commit()
        conn.close()
        export_db_to_json()

        await interaction.followup.send(f"♻️ HWID reset for `{key}`!", ephemeral=True)
        log.info(f"🟡 Reset HWID for key {key}")
    except Exception as e:
        log.error(f"❌ Error in /resethwid: {e}")
        await interaction.followup.send("⚠️ Failed to reset HWID", ephemeral=True)

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
