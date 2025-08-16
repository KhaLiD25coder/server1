import os
import time
import logging
import asyncio
import discord
import psycopg2
from fastapi import FastAPI, Query
import uvicorn
from typing import Optional

# -------------------------------------------------
# Logging
# -------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("LicenseBot")

# -------------------------------------------------
# FastAPI
# -------------------------------------------------
app = FastAPI()

# -------------------------------------------------
# Database (PostgreSQL)
# -------------------------------------------------
DB_URL = os.getenv("DATABASE_URL")

def init_db():
    conn = psycopg2.connect(DB_URL)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            key TEXT PRIMARY KEY,
            expiry_date BIGINT,
            hwid TEXT
        )
    """)
    conn.commit()
    conn.close()
    log.info("✅ Database initialized.")

init_db()

# Utility: log current keys
def log_all_keys():
    conn = psycopg2.connect(DB_URL)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    log.info("📜 Current Keys in Database:")
    if not rows:
        log.info("   (no keys found)")
        return
    for k, expiry, hwid in rows:
        exp_str = time.strftime('%Y-%m-%d', time.localtime(expiry)) if expiry else "None"
        log.info(f"   🔑 {k} | Expiry: {exp_str} | HWID: {hwid}")

# -------------------------------------------------
# Discord Bot
# -------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(bot)

# -------------------------------------------------
# API Endpoint for Autojoiner
# -------------------------------------------------
@app.get("/verify")
async def verify(key: str = Query(...), hwid: Optional[str] = None):
    conn = psycopg2.connect(DB_URL)
    c = conn.cursor()
    c.execute("SELECT expiry_date, hwid FROM licenses WHERE key=%s", (key,))
    row = c.fetchone()
    conn.close()

    if not row:
        return {"status": "invalid"}

    expiry, saved_hwid = row

    # Expiry check
    if expiry and expiry < int(time.time()):
        return {"status": "expired"}

    # HWID mismatch
    if saved_hwid and saved_hwid != hwid:
        return {"status": "hwid_mismatch"}

    # Bind HWID if first use
    if not saved_hwid and hwid:
        conn = psycopg2.connect(DB_URL)
        c = conn.cursor()
        c.execute("UPDATE licenses SET hwid=%s WHERE key=%s", (hwid, key))
        conn.commit()
        conn.close()

    return {"status": "valid"}

# -------------------------------------------------
# Slash Commands
# -------------------------------------------------
@tree.command(name="addkey", description="Add a new license key")
async def addkey(interaction: discord.Interaction, key: str, expiry: int):
    conn = psycopg2.connect(DB_URL)
    c = conn.cursor()
    c.execute("""
        INSERT INTO licenses (key, expiry_date, hwid)
        VALUES (%s, %s, %s)
        ON CONFLICT (key) DO UPDATE SET expiry_date=EXCLUDED.expiry_date
    """, (key, expiry, None))
    conn.commit()
    conn.close()

    log.info(f"🟡 Added key {key}")
    log_all_keys()
    await interaction.response.send_message(f"✅ Key `{key}` added with expiry `{expiry}`", ephemeral=True)


@tree.command(name="delkey", description="Delete a license key")
async def delkey(interaction: discord.Interaction, key: str):
    conn = psycopg2.connect(DB_URL)
    c = conn.cursor()
    c.execute("DELETE FROM licenses WHERE key=%s", (key,))
    conn.commit()
    conn.close()

    log.info(f"🟡 Deleted key {key}")
    log_all_keys()
    await interaction.response.send_message(f"🗑️ Key `{key}` deleted", ephemeral=True)


@tree.command(name="resethwid", description="Reset HWID for a license key")
async def resethwid(interaction: discord.Interaction, key: str):
    conn = psycopg2.connect(DB_URL)
    c = conn.cursor()
    c.execute("UPDATE licenses SET hwid=NULL WHERE key=%s", (key,))
    conn.commit()
    conn.close()

    log.info(f"🟡 Reset HWID for {key}")
    log_all_keys()
    await interaction.response.send_message(f"🔄 HWID reset for `{key}`", ephemeral=True)


@tree.command(name="listkeys", description="List all license keys")
async def listkeys(interaction: discord.Interaction):
    conn = psycopg2.connect(DB_URL)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("📭 No keys found", ephemeral=True)
        return

    response = "📜 **License Keys:**\n"
    for k, expiry, hwid in rows:
        exp_str = time.strftime('%Y-%m-%d', time.localtime(expiry)) if expiry else "None"
        response += f"🔑 `{k}` | Expiry: {exp_str} | HWID: {hwid}\n"

    await interaction.response.send_message(response, ephemeral=True)

# -------------------------------------------------
# Bot Events
# -------------------------------------------------
@bot.event
async def on_ready():
    await tree.sync()
    log.info(f"✅ Bot online as {bot.user}")
    log_all_keys()

# -------------------------------------------------
# Main
# -------------------------------------------------
async def main():
    api_task = asyncio.create_task(
        uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)), log_level="info")
    )
    bot_task = asyncio.create_task(bot.start(TOKEN))
    await asyncio.gather(api_task, bot_task)

if __name__ == "__main__":
    asyncio.run(main())
