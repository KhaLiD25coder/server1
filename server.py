import os
import json
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
import uvicorn
import discord
from discord import app_commands
from fastapi import FastAPI
from contextlib import asynccontextmanager
import httpx

# ================= CONFIG =================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
ADMIN_IDS = [int(i) for i in os.environ.get("ADMIN_IDS", "").split(",") if i]
GUILD_ID = int(os.environ.get("GUILD_ID", "0"))
DB_PATH = "licenses.db"
LICENSES_JSON_PATH = "licenses.json"

# ================= DATABASE INIT =================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        '''CREATE TABLE IF NOT EXISTS licenses
           (key TEXT PRIMARY KEY, expiry_date TEXT, hwid TEXT)'''
    )
    conn.commit()
    conn.close()
    logging.info("Database initialized.")

def import_licenses_from_json():
    if not os.path.exists(LICENSES_JSON_PATH):
        print(f"[DEBUG] No {LICENSES_JSON_PATH} file found. Skipping import.")
        return

    try:
        with open(LICENSES_JSON_PATH, "r") as f:
            data = json.load(f)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        imported_count = 0

        if isinstance(data, dict):  # Timestamp format
            for key, timestamp in data.items():
                try:
                    expiry_date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
                except Exception as e:
                    print(f"[ERROR] Invalid timestamp for key {key}: {e}")
                    continue

                c.execute("SELECT key FROM licenses WHERE key=?", (key,))
                if not c.fetchone():
                    c.execute("INSERT INTO licenses (key, expiry_date, hwid) VALUES (?, ?, NULL)",
                              (key, expiry_date))
                    imported_count += 1

        elif isinstance(data, list):  # Structured object format
            for lic in data:
                key = lic.get("key")
                expiry_date = lic.get("expiry_date")
                hwid = lic.get("hwid", None)

                if not key or not expiry_date:
                    continue

                c.execute("SELECT key FROM licenses WHERE key=?", (key,))
                if not c.fetchone():
                    c.execute("INSERT INTO licenses (key, expiry_date, hwid) VALUES (?, ?, ?)",
                              (key, expiry_date, hwid))
                    imported_count += 1

        conn.commit()
        conn.close()
        print(f"[DEBUG] Imported {imported_count} keys from {LICENSES_JSON_PATH}")
    except Exception as e:
        print(f"[ERROR] Failed to import licenses from JSON: {e}")

def export_licenses_to_json():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT key, expiry_date, hwid FROM licenses")
        rows = c.fetchall()
        conn.close()

        data = []
        for key, expiry_date, hwid in rows:
            data.append({
                "key": key,
                "expiry_date": expiry_date,
                "hwid": hwid
            })

        with open(LICENSES_JSON_PATH, "w") as f:
            json.dump(data, f, indent=4)

        print(f"[DEBUG] Exported {len(data)} keys to {LICENSES_JSON_PATH}")
    except Exception as e:
        print(f"[ERROR] Failed to export licenses to JSON: {e}")

# ================= FASTAPI =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    import_licenses_from_json()
    yield

app = FastAPI(lifespan=lifespan)

@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {"message": "License server is running 🚀"}

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
    if datetime.strptime(expiry_date, "%Y-%m-%d") < datetime.now(timezone.utc):
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

    expiry = datetime.now(timezone.utc) + timedelta(days=days)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO licenses (key, expiry_date, hwid) VALUES (?, ?, NULL)",
              (key, expiry.strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ Key '{key}' added for {days} days.", ephemeral=True)

@bot.tree.command(name="removekey", description="Remove a license key")
async def remove_key(interaction: discord.Interaction, key: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM licenses WHERE key=?", (key,))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"🗑 Key '{key}' removed.", ephemeral=True)

@bot.tree.command(name="resethwid", description="Reset HWID for a license key")
async def reset_hwid(interaction: discord.Interaction, key: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE licenses SET hwid=NULL WHERE key=?", (key,))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"🔄 HWID for key '{key}' reset.", ephemeral=True)

@bot.tree.command(name="listkeys", description="List all currently live license keys")
async def list_keys(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    live_keys = []
    for key, expiry_date, hwid in rows:
        if datetime.strptime(expiry_date, "%Y-%m-%d") >= now:
            live_keys.append(f"**{key}** → expires {expiry_date} | HWID: {hwid or 'None'}")

    if not live_keys:
        await interaction.response.send_message("🚫 No keys live.", ephemeral=True)
    else:
        message = "\n".join(live_keys)
        await interaction.response.send_message(f"🔑 **Live Keys:**\n{message}", ephemeral=True)

@bot.event
async def on_ready():
    try:
        guild = discord.Object(id=GUILD_ID)
        await bot.tree.sync(guild=guild)
        print(f"✅ Commands synced to guild {GUILD_ID}.")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")
    print(f"Bot online as {bot.user}")

    # Log live keys to Render console
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    live_keys = []
    for key, expiry_date, hwid in rows:
        if datetime.strptime(expiry_date, "%Y-%m-%d") >= now:
            live_keys.append(f"{key} (expires {expiry_date}, HWID: {hwid or 'None'})")

    if live_keys:
        print("🔑 Live keys on startup:")
        for k in live_keys:
            print(f"   • {k}")
    else:
        print("🚫 No keys live on startup.")

# ================= BACKGROUND TASKS =================
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

async def periodic_export():
    while True:
        export_licenses_to_json()
        await asyncio.sleep(300)  # Every 5 minutes

# ================= RUN BOTH =================
async def main():
    if os.environ.get("RENDER_URL"):
        asyncio.create_task(self_ping())

    asyncio.create_task(periodic_export())

    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), loop="asyncio")
    server = uvicorn.Server(config)

    bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    uvicorn_task = asyncio.create_task(server.serve())

    await asyncio.gather(uvicorn_task, bot_task)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise ValueError("❌ DISCORD_TOKEN not set in environment variables")
    asyncio.run(main())

