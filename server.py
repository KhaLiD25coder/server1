import os
import json
import logging
import psycopg2
import discord
import asyncio
import uvicorn
from discord.ext import commands
from fastapi import FastAPI
from psycopg2.extras import RealDictCursor
from datetime import datetime

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Load ENV
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
DB_URL = os.getenv("DATABASE_URL")

if not TOKEN:
    logging.error("❌ No DISCORD_BOT_TOKEN found in environment!")
if not DB_URL:
    logging.error("❌ No DATABASE_URL found in environment!")

# FastAPI app
app = FastAPI()

# Discord bot
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# DB init
def init_db():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            key TEXT PRIMARY KEY,
            expiry BIGINT,
            hwid TEXT
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    logging.info("✅ Database initialized and schema ensured.")

def list_keys_db():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute("SELECT key, expiry, hwid FROM licenses")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [(row["key"], row["expiry"], row["hwid"]) for row in rows]

def add_key_db(key, expiry):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("INSERT INTO licenses (key, expiry, hwid) VALUES (%s, %s, %s) ON CONFLICT (key) DO UPDATE SET expiry = EXCLUDED.expiry",
                (key, expiry, None))
    conn.commit()
    cur.close()
    conn.close()

def remove_key_db(key):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("DELETE FROM licenses WHERE key = %s", (key,))
    conn.commit()
    cur.close()
    conn.close()

def reset_hwid_db(key):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("UPDATE licenses SET hwid = NULL WHERE key = %s", (key,))
    conn.commit()
    cur.close()
    conn.close()

# --- SLASH COMMANDS ---

@tree.command(name="listkeys", description="List all license keys")
async def listkeys(interaction: discord.Interaction):
    logging.info("🟡 /listkeys triggered")
    rows = list_keys_db()
    if not rows:
        await interaction.response.send_message("No keys found.", ephemeral=True)
        return
    msg = "\n".join([f"🔑 {k} | Expiry: {e} | HWID: {h}" for k, e, h in rows])
    await interaction.response.send_message(msg, ephemeral=True)

@tree.command(name="addkey", description="Add a new license key")
async def addkey(interaction: discord.Interaction, key: str, expiry: int):
    logging.info("🟡 /addkey triggered")
    add_key_db(key, expiry)
    logging.info(f"💾 Added key {key} with expiry {expiry}")
    rows = list_keys_db()
    for k, e, h in rows:
        logging.info(f"   🔑 {k} | Expiry: {e} | HWID: {h}")
    await interaction.response.send_message(f"✅ Key {key} added!", ephemeral=True)

@tree.command(name="removekey", description="Remove a license key")
async def removekey(interaction: discord.Interaction, key: str):
    logging.info("🟡 /removekey triggered")
    remove_key_db(key)
    logging.info(f"🗑️ Removed key {key}")
    rows = list_keys_db()
    for k, e, h in rows:
        logging.info(f"   🔑 {k} | Expiry: {e} | HWID: {h}")
    await interaction.response.send_message(f"✅ Key {key} removed!", ephemeral=True)

@tree.command(name="resethwid", description="Reset HWID for a key")
async def resethwid(interaction: discord.Interaction, key: str):
    logging.info("🟡 /resethwid triggered")
    reset_hwid_db(key)
    logging.info(f"🔄 Reset HWID for key {key}")
    rows = list_keys_db()
    for k, e, h in rows:
        logging.info(f"   🔑 {k} | Expiry: {e} | HWID: {h}")
    await interaction.response.send_message(f"✅ HWID reset for {key}", ephemeral=True)

# FastAPI route
@app.get("/")
async def root():
    return {"status": "running"}

# MAIN
async def main():
    init_db()

    bot_task = asyncio.create_task(bot.start(TOKEN))
    api_task = asyncio.create_task(
        uvicorn.Server(
            uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)), log_level="info")
        ).serve()
    )

    await bot.wait_until_ready()
    logging.info(f"✅ Bot online as {bot.user}")

    # Log keys at startup
    try:
        rows = list_keys_db()
        logging.info("📜 Current Keys in Database (after startup):")
        if rows:
            for k, e, h in rows:
                logging.info(f"   🔑 {k} | Expiry: {e} | HWID: {h}")
        else:
            logging.info("   (no keys in DB yet)")
    except Exception as e:
        logging.error(f"⚠️ Failed to list keys at startup: {e}")

    # Sync commands
    try:
        await tree.sync()
        logging.info("🧹 Cleared old global commands")
        if GUILD_ID:
            guild_obj = discord.Object(id=int(GUILD_ID))
            synced = await tree.sync(guild=guild_obj)
            logging.info(f"✅ Synced {len(synced)} commands to guild {GUILD_ID}")
        else:
            synced = await tree.sync()
            logging.info(f"🌍 Synced {len(synced)} global commands")
    except Exception as e:
        logging.error(f"❌ Failed to sync commands: {e}")

    await asyncio.gather(bot_task, api_task)

if __name__ == "__main__":
    asyncio.run(main())

