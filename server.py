import os
import asyncio
import logging
from datetime import date, timedelta, UTC, datetime
from contextlib import asynccontextmanager

import asyncpg
import httpx
import uvicorn
import discord
from discord import app_commands
from fastapi import FastAPI, Query

# ================= CONFIG =================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")          # optional; API runs even if missing
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x]
GUILD_ID = int(os.environ.get("GUILD_ID", "0"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")        # Supabase/Postgres connection string
RENDER_URL = os.environ.get("RENDER_URL", "")            # e.g. https://server1-xxxx.onrender.com

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("license-server")

# Ensure sslmode=require for Supabase/managed Postgres
if DATABASE_URL and "sslmode=" not in DATABASE_URL:
    sep = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL = f"{DATABASE_URL}{sep}sslmode=require"

# ================= DB POOL =================
pool: asyncpg.Pool | None = None

async def init_db():
    global pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, command_timeout=60)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
              key TEXT PRIMARY KEY,
              expiry_date DATE NOT NULL,
              hwid TEXT
            );
        """)
    log.info("✅ Database initialized.")

# ================= FASTAPI =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"message": "License server is running 🚀"}

@app.get("/verify")
async def verify_license(
    key: str = Query(..., min_length=1),
    hwid: str | None = None
):
    assert pool is not None
    row = await pool.fetchrow("SELECT expiry_date, hwid FROM licenses WHERE key=$1", key)
    if not row:
        return {"status": "invalid"}

    expiry_date: date = row["expiry_date"]
    saved_hwid: str | None = row["hwid"]

    if expiry_date < date.today():
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

def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id in ADMIN_IDS

@bot.tree.command(name="addkey", description="Add a new license key")
async def add_key(interaction: discord.Interaction, key: str, days: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    expiry = date.today() + timedelta(days=days)
    assert pool is not None
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO licenses (key, expiry_date, hwid)
            VALUES ($1, $2, NULL)
            ON CONFLICT (key) DO UPDATE SET expiry_date=EXCLUDED.expiry_date, hwid=NULL
        """, key, expiry)
    await interaction.response.send_message(f"✅ Key '{key}' added for {days} days (expires {expiry}).", ephemeral=True)

@bot.tree.command(name="removekey", description="Remove a license key")
async def remove_key(interaction: discord.Interaction, key: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    assert pool is not None
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM licenses WHERE key=$1", key)
    await interaction.response.send_message(f"🗑 Key '{key}' removed.", ephemeral=True)

@bot.tree.command(name="resethwid", description="Reset HWID for a license key")
async def reset_hwid(interaction: discord.Interaction, key: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    assert pool is not None
    async with pool.acquire() as conn:
        await conn.execute("UPDATE licenses SET hwid=NULL WHERE key=$1", key)
    await interaction.response.send_message(f"🔄 HWID for key '{key}' reset.", ephemeral=True)

@bot.event
async def on_ready():
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            await bot.tree.sync(guild=guild)
            print(f"✅ Commands synced to guild {GUILD_ID}.")
        else:
            await bot.tree.sync()
            print("✅ Commands synced globally.")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")
    print(f"Bot online as {bot.user}")

# ================= SELF-PING =================
async def self_ping():
    if not RENDER_URL:
        return
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(RENDER_URL, timeout=10)
                print(f"🔄 Pinged {RENDER_URL}")
            except Exception as e:
                print(f"⚠️ Self-ping failed: {e}")
            await asyncio.sleep(300)  # 5 min

# ================= MAIN =================
async def main():
    # Start API first so Render always detects the port
    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), loop="asyncio")
    api_task = asyncio.create_task(uvicorn.Server(config).serve())

    # Start self-ping (optional)
    if RENDER_URL:
        asyncio.create_task(self_ping())

    # Start bot (won't kill API if it fails)
    bot_task = None
    if DISCORD_TOKEN:
        try:
            bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
        except Exception as e:
            print(f"⚠️ Bot failed to start: {e}")

    if bot_task:
        await asyncio.gather(api_task, bot_task)
    else:
        await api_task  # keep API running even without bot

if __name__ == "__main__":
    asyncio.run(main())
