import os
import asyncio
import logging
import psycopg2
import discord
from discord import app_commands
from discord.ext import commands
from fastapi import FastAPI
from uvicorn import Config, Server

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# ---------- Config ----------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0))
DB_URL = os.getenv("DATABASE_URL")

# ---------- Database ----------
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
    logging.info("✅ Database initialized.")

def add_key_db(key, expiry):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("INSERT INTO licenses (key, expiry, hwid) VALUES (%s, %s, %s) ON CONFLICT (key) DO NOTHING", (key, expiry, None))
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"🟡 Added key {key}")

def list_keys_db():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT key, expiry, hwid FROM licenses")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def remove_key_db(key):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("DELETE FROM licenses WHERE key=%s", (key,))
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"🔴 Removed key {key}")

def reset_hwid_db(key):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("UPDATE licenses SET hwid=NULL WHERE key=%s", (key,))
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"🟠 Reset HWID for {key}")

# ---------- Discord Bot ----------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logging.info(f"✅ Bot online as {bot.user}")
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        logging.info(f"🌍 Synced {len(synced)} commands to guild {GUILD_ID}")
    except Exception as e:
        logging.error(f"Failed to sync commands: {e}")

@bot.tree.command(name="listkeys", description="List all license keys", guild=discord.Object(id=GUILD_ID))
async def listkeys(interaction: discord.Interaction):
    rows = list_keys_db()
    if not rows:
        await interaction.response.send_message("No keys found.", ephemeral=True)
    else:
        msg = "\n".join([f"🔑 {k} | Expiry: {e} | HWID: {h}" for k, e, h in rows])
        await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="addkey", description="Add a new license key", guild=discord.Object(id=GUILD_ID))
async def addkey(interaction: discord.Interaction, key: str, expiry: int):
    add_key_db(key, expiry)
    await interaction.response.send_message(f"✅ Key `{key}` added.", ephemeral=True)

@bot.tree.command(name="removekey", description="Remove a license key", guild=discord.Object(id=GUILD_ID))
async def removekey(interaction: discord.Interaction, key: str):
    remove_key_db(key)
    await interaction.response.send_message(f"❌ Key `{key}` removed.", ephemeral=True)

@bot.tree.command(name="resethwid", description="Reset HWID for a license key", guild=discord.Object(id=GUILD_ID))
async def resethwid(interaction: discord.Interaction, key: str):
    reset_hwid_db(key)
    await interaction.response.send_message(f"🟠 HWID reset for `{key}`.", ephemeral=True)

# ---------- FastAPI ----------
app = FastAPI()

@app.get("/")
def root():
    return {"status": "ok", "message": "License server running"}

# ---------- Main ----------
async def main():
    init_db()

    # log keys at startup
    rows = list_keys_db()
    logging.info("📜 Current Keys in Database (after startup):")
    for k, e, h in rows:
        logging.info(f"   🔑 {k} | Expiry: {e} | HWID: {h}")

    # run both bot + API server
    bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    config = Config(app=app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)), log_level="info")
    server = Server(config)
    api_task = asyncio.create_task(server.serve())
    await asyncio.gather(bot_task, api_task)

if __name__ == "__main__":
    asyncio.run(main())
