import os
import json
import sqlite3
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import uvicorn
from fastapi import FastAPI

# ===================== CONFIG =====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
GUILD_ID = int(os.getenv("GUILD_ID", "1394437999596404748"))
DB_PATH = "licenses.db"
JSON_PATH = "licenses.json"
ADMIN_ID = int(os.getenv("ADMIN_ID", "1240624476949975218"))

# ===================== FASTAPI =====================
app = FastAPI()

@app.get("/")
async def home():
    return {"status": "ok", "bot": "running"}

# ===================== DATABASE =====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Check if table exists
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='licenses'")
    exists = c.fetchone()

    if exists:
        # Validate schema
        c.execute("PRAGMA table_info(licenses)")
        cols = [row[1] for row in c.fetchall()]
        required = {"license_key", "expiry_date", "hwid"}

        if not required.issubset(set(cols)):
            print("[DEBUG] Old schema detected. Recreating licenses table...")
            c.execute("DROP TABLE licenses")
            conn.commit()
            exists = False

    if not exists:
        c.execute("""
            CREATE TABLE licenses (
                license_key TEXT PRIMARY KEY,
                expiry_date INTEGER,
                hwid TEXT
            )
        """)
        conn.commit()

    conn.close()

def import_json_to_db():
    if not os.path.exists(JSON_PATH):
        print("[DEBUG] No licenses.json found.")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    with open(JSON_PATH, "r") as f:
        data = json.load(f)

    for key, info in data.items():
        if isinstance(info, dict):
            expiry = info.get("expiry_date")
            hwid = info.get("hwid")
        else:
            expiry = info
            hwid = None

        c.execute("INSERT OR REPLACE INTO licenses (license_key, expiry_date, hwid) VALUES (?, ?, ?)",
                  (key, expiry, hwid))

    conn.commit()
    conn.close()
    print(f"[DEBUG] Imported {len(data)} keys from JSON.")

# ===================== DISCORD BOT =====================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

@bot.event
async def on_ready():
    print(f"🤖 Bot online as {bot.user}")
    try:
        guild = discord.Object(id=GUILD_ID)
        synced = await tree.sync(guild=guild)
        print(f"✅ Slash commands synced to guild {GUILD_ID}")
    except Exception as e:
        print(f"❌ Command sync failed: {e}")

# ---------------- Slash Commands ----------------
@tree.command(name="listkeys", description="List all license keys", guild=discord.Object(id=GUILD_ID))
async def list_keys(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)  # avoid timeout
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT license_key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await interaction.followup.send("No license keys found.", ephemeral=True)
        return

    message = "\n".join([f"🔑 {r[0]} | Exp: {r[1]} | HWID: {r[2]}" for r in rows])
    await interaction.followup.send(message, ephemeral=True)

@tree.command(name="addkey", description="Add a new license key", guild=discord.Object(id=GUILD_ID))
async def add_key(interaction: discord.Interaction, license_key: str, expiry_date: int, hwid: str = None):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != ADMIN_ID:
        await interaction.followup.send("❌ You are not authorized.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO licenses (license_key, expiry_date, hwid) VALUES (?, ?, ?)",
              (license_key, expiry_date, hwid))
    conn.commit()
    conn.close()

    await interaction.followup.send(f"✅ Key `{license_key}` added.", ephemeral=True)

@tree.command(name="removekey", description="Remove a license key", guild=discord.Object(id=GUILD_ID))
async def remove_key(interaction: discord.Interaction, license_key: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != ADMIN_ID:
        await interaction.followup.send("❌ You are not authorized.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM licenses WHERE license_key = ?", (license_key,))
    conn.commit()
    conn.close()

    await interaction.followup.send(f"🗑️ Key `{license_key}` removed.", ephemeral=True)

# ===================== MAIN =====================
async def start_bot():
    await bot.start(BOT_TOKEN)

async def start_api():
    config = uvicorn.Config(app, host="0.0.0.0", port=10000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    init_db()
    import_json_to_db()
    await asyncio.gather(start_bot(), start_api())

if __name__ == "__main__":
    asyncio.run(main())
