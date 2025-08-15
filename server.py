import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import json
import os
import asyncio
from datetime import datetime, timezone
import uvicorn
from fastapi import FastAPI

# ===================== CONFIG =====================
TOKEN = os.getenv("DISCORD_TOKEN")  # Set in Render/Env Vars
GUILD_ID = 1394437999596404748  # Your server ID
ADMIN_ID = 1240624476949975218  # Your Discord user ID
DB_PATH = "licenses.db"
JSON_PATH = "licenses.json"

# ===================== DATABASE =====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS licenses (
                    key TEXT PRIMARY KEY,
                    expiry_date TEXT,
                    hwid TEXT
                )''')
    conn.commit()
    conn.close()

def import_json_to_db():
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, "r") as f:
            data = json.load(f)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        for key, info in data.items():
            c.execute("INSERT OR REPLACE INTO licenses (key, expiry_date, hwid) VALUES (?, ?, ?)",
                      (key, info.get("expiry_date"), info.get("hwid")))
        conn.commit()
        conn.close()

def export_db_to_json():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()
    data = {key: {"expiry_date": expiry, "hwid": hwid} for key, expiry, hwid in rows}
    with open(JSON_PATH, "w") as f:
        json.dump(data, f, indent=4)

# ===================== BOT SETUP =====================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    try:
        guild = discord.Object(id=GUILD_ID)
        await bot.tree.sync(guild=guild)  # Only sync to your server
        print(f"✅ Slash commands synced to guild {GUILD_ID}", flush=True)
    except Exception as e:
        print(f"❌ Command sync failed: {e}", flush=True)

    print(f"🤖 Bot online as {bot.user}", flush=True)

    # Show live keys
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    live_keys = [
        f"{key} (expires {expiry_date}, HWID: {hwid or 'None'})"
        for key, expiry_date, hwid in rows
        if datetime.strptime(expiry_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) >= now
    ]

    if live_keys:
        print("🔑 Live keys on startup:")
        for k in live_keys:
            print(f"   • {k}")
    else:
        print("🚫 No keys live on startup.")

# ===================== COMMANDS =====================
@bot.tree.command(name="listkeys", description="List all license keys", guild=discord.Object(id=GUILD_ID))
async def listkeys(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("🚫 No keys found.", ephemeral=True)
        return

    now = datetime.now(timezone.utc)
    msg = ""
    for key, expiry_date, hwid in rows:
        expiry_dt = datetime.strptime(expiry_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        status = "✅ Active" if expiry_dt >= now else "❌ Expired"
        msg += f"**{key}** - Expires: {expiry_date} - HWID: {hwid or 'None'} - {status}\n"

    await interaction.response.send_message(msg, ephemeral=True)

# ===== New Command to Re-Sync Commands =====
@bot.tree.command(name="synccommands", description="Force re-sync slash commands", guild=discord.Object(id=GUILD_ID))
async def synccommands(interaction: discord.Interaction):
    if interaction.user.id != ADMIN_ID:
        await interaction.response.send_message("🚫 You are not authorized to run this command.", ephemeral=True)
        return
    try:
        guild = discord.Object(id=GUILD_ID)
        await bot.tree.sync(guild=guild)
        await interaction.response.send_message("✅ Slash commands synced successfully!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to sync commands: {e}", ephemeral=True)

# ===================== FASTAPI SERVER =====================
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "Bot is running"}

# ===================== MAIN =====================
async def start_bot():
    await bot.start(TOKEN)

async def main():
    init_db()
    import_json_to_db()
    export_db_to_json()

    bot_task = asyncio.create_task(start_bot())
    uvicorn_task = asyncio.create_task(
        uvicorn.Server(
            uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
        ).serve()
    )

    await asyncio.gather(bot_task, uvicorn_task)

if __name__ == "__main__":
    asyncio.run(main())
