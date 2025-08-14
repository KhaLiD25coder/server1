import os
import asyncio
import uvicorn
from fastapi import FastAPI
import logging
import sqlite3
import discord
from discord import app_commands
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

# ==================== CONFIG ====================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")  # Read token from Render env variable
ADMIN_IDS = [1240624476949975218]
GUILD_ID = 1240624476949975218  # Your Discord server ID
DB_PATH = "licenses.db"

# ==================== FASTAPI ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS licenses
                 (key TEXT PRIMARY KEY, expiry_date TEXT, hwid TEXT)''')
    conn.commit()
    conn.close()
    logger.info("Database initialized.")

    yield

    # Shutdown (nothing to clean here for now)


app = FastAPI(lifespan=lifespan)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("license-server")


@app.get("/verify")
async def verify_license(key: str, hwid: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT expiry_date, hwid FROM licenses WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    if not row:
        return {"status": "invalid"}
    expiry_date, saved_hwid = row
    if datetime.strptime(expiry_date, "%Y-%m-%d") < datetime.utcnow():
        return {"status": "expired"}
    if saved_hwid and hwid and saved_hwid != hwid:
        return {"status": "hwid_mismatch"}
    return {"status": "valid"}


# ==================== DISCORD BOT ====================
class LicenseBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

bot = LicenseBot()

def is_admin(interaction: discord.Interaction):
    return interaction.user.id in ADMIN_IDS

@bot.tree.command(name="addkey", description="Add a new license key")
async def add_key(interaction: discord.Interaction, key: str, days: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return
    expiry = datetime.utcnow() + timedelta(days=days)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO licenses (key, expiry_date, hwid) VALUES (?, ?, NULL)",
              (key, expiry.strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ License key '{key}' added for {days} days.", ephemeral=True)

@bot.tree.command(name="removekey", description="Remove a license key")
async def remove_key(interaction: discord.Interaction, key: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM licenses WHERE key=?", (key,))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"🗑 License key '{key}' removed.", ephemeral=True)

@bot.tree.command(name="resethwid", description="Reset the HWID for a license key")
async def reset_hwid(interaction: discord.Interaction, key: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE licenses SET hwid=NULL WHERE key=?", (key,))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"🔄 HWID for license key '{key}' reset.", ephemeral=True)


@bot.event
async def on_ready():
    try:
        guild = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild)
        print(f"✅ Synced {len(synced)} slash command(s) to guild {GUILD_ID}.")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")
    print(f"Bot is online as {bot.user}")


# ==================== RUN BOTH ====================
async def main():
    if not DISCORD_TOKEN:
        raise ValueError("❌ DISCORD_TOKEN environment variable not set!")

    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.environ.get('PORT', 8000)), loop="asyncio")
    server = uvicorn.Server(config)

    bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    uvicorn_task = asyncio.create_task(server.serve())

    await asyncio.gather(uvicorn_task, bot_task)


if __name__ == "__main__":
    asyncio.run(main())
