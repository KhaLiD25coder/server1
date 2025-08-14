import os
import asyncio
import logging
from datetime import datetime, timedelta

import uvicorn
import discord
from discord import app_commands
from fastapi import FastAPI
from contextlib import asynccontextmanager
import httpx
import psycopg2
from psycopg2.extras import RealDictCursor

# ================= CONFIG =================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
ADMIN_IDS = [int(i) for i in os.environ.get("ADMIN_IDS", "").split(",") if i]
GUILD_ID = int(os.environ.get("GUILD_ID", "0"))
DATABASE_URL = os.environ.get("DATABASE_URL")

# ================= DATABASE INIT =================
def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            key TEXT PRIMARY KEY,
            expiry_date TEXT,
            hwid TEXT
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    logging.info("Database initialized.")

# ================= FASTAPI =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"message": "License server is running 🚀"}

@app.get("/verify")
async def verify_license(key: str, hwid: str = None):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT expiry_date, hwid FROM licenses WHERE key=%s", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return {"status": "invalid"}

    expiry_date = row['expiry_date']
    saved_hwid = row['hwid']

    if datetime.strptime(expiry_date, "%Y-%m-%d") < datetime.utcnow():
        return {"status": "expired"}

    if saved_hwid and hwid and saved_hwid != hwid:
        return {"status": "hwid_mismatch"}

    return {"status": "valid"}

# ================= DISCORD BOT =================
class LicenseBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

bot = LicenseBot()

def is_admin(interaction: discord.Interaction):
    return interaction.user.id in ADMIN_IDS

@bot.tree.command(name="addkey", description="Add a new license key")
async def add_key(interaction: discord.Interaction, key: str, days: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return

    expiry = datetime.utcnow() + timedelta(days=days)
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO licenses (key, expiry_date, hwid)
        VALUES (%s, %s, NULL)
        ON CONFLICT (key) DO UPDATE SET expiry_date = EXCLUDED.expiry_date, hwid = NULL
    """, (key, expiry.strftime("%Y-%m-%d")))
    conn.commit()
    cur.close()
    conn.close()
    await interaction.response.send_message(f"✅ Key '{key}' added for {days} days.", ephemeral=True)

@bot.tree.command(name="removekey", description="Remove a license key")
async def remove_key(interaction: discord.Interaction, key: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("DELETE FROM licenses WHERE key=%s", (key,))
    conn.commit()
    cur.close()
    conn.close()
    await interaction.response.send_message(f"🗑 Key '{key}' removed.", ephemeral=True)

@bot.tree.command(name="resethwid", description="Reset HWID for a license key")
async def reset_hwid(interaction: discord.Interaction, key: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("UPDATE licenses SET hwid=NULL WHERE key=%s", (key,))
    conn.commit()
    cur.close()
    conn.close()
    await interaction.response.send_message(f"🔄 HWID for key '{key}' reset.", ephemeral=True)

@bot.event
async def on_ready():
    try:
        guild = discord.Object(id=GUILD_ID)
        await bot.tree.sync(guild=guild)
        print(f"✅ Commands synced to guild {GUILD_ID}.")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")
    print(f"Bot online as {bot.user}")

# ================= SELF-PING TASK =================
async def self_ping():
    url = os.environ.get("RENDER_URL")
    if not url:
        return
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(url)
                print(f"🔄 Pinged {url}")
            except Exception as e:
                print(f"⚠️ Self-ping failed: {e}")
            await asyncio.sleep(300)

# ================= RUN BOTH =================
async def main():
    if os.environ.get("RENDER_URL"):
        asyncio.create_task(self_ping())

    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), loop="asyncio")
    server = uvicorn.Server(config)

    bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    uvicorn_task = asyncio.create_task(server.serve())

    await asyncio.gather(uvicorn_task, bot_task)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise ValueError("❌ DISCORD_TOKEN not set in environment variables")
    asyncio.run(main())
