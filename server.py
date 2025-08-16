import os
import json
import sqlite3
import asyncio
from typing import Optional
from fastapi import FastAPI
import uvicorn
import discord
from discord.ext import commands
from discord import app_commands

# ================== CONFIG ==================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1394437999596404748"))  # Replace with your server ID

DB_PATH = "licenses.db"
JSON_PATH = "licenses.json"

if not DISCORD_BOT_TOKEN:
    raise ValueError("❌ DISCORD_BOT_TOKEN not set in environment variables")

# ================== DATABASE + JSON HELPERS ==================
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

        print("💾 licenses.json updated successfully.")
    except Exception as e:
        print(f"❌ Failed to export DB to JSON: {e}")

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
    print(f"✅ Bot online as {bot.user}")
    try:
        guild = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild)
        print(f"✅ Synced {len(synced)} commands to guild {GUILD_ID}:")
        for cmd in synced:
            print(f"   • /{cmd.name} — {cmd.description}")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")

    # Always log current keys after ready
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    print("📜 Current Keys in Database (after startup):")
    if not rows:
        print("   (No keys found)")
    else:
        for row in rows:
            print(f"   🔑 {row[0]} | Expiry: {row[1]} | HWID: {row[2]}")

# ========== SLASH COMMANDS ==========
@bot.tree.command(name="listkeys", description="List all saved license keys", guild=discord.Object(id=GUILD_ID))
async def listkeys(interaction: discord.Interaction):
    print("🟡 /listkeys command received")
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("🟡 No keys found in DB")
        await interaction.followup.send("No keys found.", ephemeral=True)
        return

    msg = "\n".join([f"🔑 {row[0]} | Expiry: {row[1]} | HWID: {row[2]}" for row in rows])
    print("🟡 Sending keys list to user")
    await interaction.followup.send(msg[:1900], ephemeral=True)

@bot.tree.command(name="addkey", description="Add a new license key", guild=discord.Object(id=GUILD_ID))
async def addkey(
    interaction: discord.Interaction,
    key: Optional[str] = "TEST-KEY",
    expiry_date: Optional[int] = 1760000000,
    hwid: Optional[str] = None
):
    print("🟡 /addkey command received")
    await interaction.response.defer(ephemeral=True)

    key = key or "TEST-KEY"
    expiry_date = expiry_date or 1760000000
    print(f"🟡 Using key={key}, expiry={expiry_date}, hwid={hwid}")

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO licenses (key, expiry_date, hwid) VALUES (?, ?, ?)", (key, expiry_date, hwid))
        conn.commit()
        conn.close()
        print("🟡 Inserted into DB")

        export_db_to_json()
        print("🟡 Exported to JSON")

        await interaction.followup.send(f"✅ Key `{key}` added!", ephemeral=True)
        print("🟡 Responded to user")
    except Exception as e:
        print(f"❌ /addkey crashed: {e}")
        await interaction.followup.send("⚠️ Internal error in /addkey", ephemeral=True)

@bot.tree.command(name="delkey", description="Delete a license key", guild=discord.Object(id=GUILD_ID))
async def delkey(interaction: discord.Interaction, key: Optional[str] = "TEST-KEY"):
    print("🟡 /delkey command received")
    await interaction.response.defer(ephemeral=True)

    key = key or "TEST-KEY"

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT key FROM licenses WHERE key=?", (key,))
        exists = c.fetchone()
        if exists:
            c.execute("DELETE FROM licenses WHERE key=?", (key,))
            conn.commit()
            conn.close()
            print("🟡 Deleted from DB")
            export_db_to_json()
            print("🟡 Exported to JSON")
            await interaction.followup.send(f"🗑️ Key `{key}` deleted!", ephemeral=True)
            print("🟡 Responded to user")
        else:
            conn.close()
            print("🟡 Key not found in DB")
            await interaction.followup.send(f"⚠️ Key `{key}` not found.", ephemeral=True)
    except Exception as e:
        print(f"❌ /delkey crashed: {e}")
        await interaction.followup.send("⚠️ Internal error in /delkey", ephemeral=True)

@bot.tree.command(name="resethwid", description="Reset the HWID for a license key", guild=discord.Object(id=GUILD_ID))
async def resethwid(interaction: discord.Interaction, key: Optional[str] = "TEST-KEY"):
    print("🟡 /resethwid command received")
    await interaction.response.defer(ephemeral=True)

    key = key or "TEST-KEY"

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT key FROM licenses WHERE key=?", (key,))
        exists = c.fetchone()
        if exists:
            c.execute("UPDATE licenses SET hwid=NULL WHERE key=?", (key,))
            conn.commit()
            conn.close()
            print("🟡 HWID reset in DB")
            export_db_to_json()
            print("🟡 Exported to JSON")
            await interaction.followup.send(f"♻️ HWID reset for key `{key}`!", ephemeral=True)
            print("🟡 Responded to user")
        else:
            conn.close()
            print("🟡 Key not found in DB")
            await interaction.followup.send(f"⚠️ Key `{key}` not found.", ephemeral=True)
    except Exception as e:
        print(f"❌ /resethwid crashed: {e}")
        await interaction.followup.send("⚠️ Internal error in /resethwid", ephemeral=True)

# ================== MAIN ==================
async def main():
    init_db()
    import_json_to_db()

    loop = asyncio.get_event_loop()

    # Run API server and bot together
    api_task = loop.create_task(
        uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=10000, log_level="info")).serve()
    )
    bot_task = loop.create_task(bot.start(DISCORD_BOT_TOKEN))

    await asyncio.gather(api_task, bot_task)

if __name__ == "__main__":
    asyncio.run(main())
