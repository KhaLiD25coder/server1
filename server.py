import os
import sqlite3
import json
import asyncio
import discord
from discord.ext import commands
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

DB_FILE = "licenses.db"
LICENSES_FILE = "licenses.json"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

app = FastAPI()


# ---------------- JSON <-> DB Sync ----------------
def import_json_to_db():
    """Load licenses.json into DB on startup (only if not already present)."""
    if not os.path.exists(LICENSES_FILE):
        print("[DEBUG] licenses.json not found, skipping import")
        return

    with open(LICENSES_FILE, "r") as f:
        data = json.load(f)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            license_key TEXT PRIMARY KEY,
            expiry_date INTEGER,
            hwid TEXT
        )
    """)

    for key, info in data.items():
        c.execute("INSERT OR REPLACE INTO licenses (license_key, expiry_date, hwid) VALUES (?, ?, ?)",
                  (key, info.get("expiry_date"), info.get("hwid")))

    conn.commit()
    conn.close()
    print(f"[DEBUG] Imported {len(data)} keys from JSON.")


def export_db_to_json():
    """Save DB state to licenses.json."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT license_key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    data = {}
    for key, expiry, hwid in rows:
        data[key] = {
            "expiry_date": expiry,
            "hwid": hwid
        }

    with open(LICENSES_FILE, "w") as f:
        json.dump(data, f, indent=4)
    print(f"[DEBUG] Exported {len(rows)} keys to JSON.")


def sync_after_change():
    export_db_to_json()


# ---------------- FastAPI Routes ----------------
@app.get("/")
async def root():
    return {"message": "License server is running!"}


@app.get("/verify")
async def verify(key: str, hwid: str = None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT expiry_date, hwid FROM licenses WHERE license_key = ?", (key,))
    row = c.fetchone()
    conn.close()

    if not row:
        return JSONResponse({"valid": False, "reason": "Invalid key"})

    expiry, bound_hwid = row
    if expiry and expiry < int(asyncio.get_event_loop().time()):
        return JSONResponse({"valid": False, "reason": "Key expired"})

    if bound_hwid and hwid != bound_hwid:
        return JSONResponse({"valid": False, "reason": "HWID mismatch"})

    return JSONResponse({"valid": True, "expiry_date": expiry})


# ---------------- Discord Bot Commands ----------------
@bot.event
async def on_ready():
    try:
        guild = discord.Object(id=int(os.getenv("DISCORD_GUILD_ID")))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"✅ Slash commands synced to guild {guild.id}")
    except Exception as e:
        print(f"❌ Command sync failed: {e}")

    print(f"🤖 Bot online as {bot.user}")


@bot.tree.command(name="addkey", description="Add a new license key")
async def addkey(interaction: discord.Interaction, key: str, expiry: int, hwid: str = None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO licenses (license_key, expiry_date, hwid) VALUES (?, ?, ?)",
              (key, expiry, hwid))
    conn.commit()
    conn.close()
    sync_after_change()
    await interaction.response.send_message(f"✅ Key `{key}` added.")


@bot.tree.command(name="delkey", description="Delete a license key")
async def delkey(interaction: discord.Interaction, key: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM licenses WHERE license_key = ?", (key,))
    conn.commit()
    conn.close()
    sync_after_change()
    await interaction.response.send_message(f"🗑️ Key `{key}` deleted.")


@bot.tree.command(name="listkeys", description="List all license keys")
async def listkeys(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT license_key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("No keys found.")
        return

    msg = "\n".join([f"🔑 {r[0]} | Expiry: {r[1]} | HWID: {r[2]}" for r in rows])
    await interaction.response.send_message(f"**Stored Keys:**\n{msg}")


# ---------------- Main Runner ----------------
async def main():
    import_json_to_db()

    uvicorn_config = uvicorn.Config(app=app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
    uvicorn_server = uvicorn.Server(config=uvicorn_config)

    bot_task = asyncio.create_task(bot.start(os.getenv("DISCORD_BOT_TOKEN")))
    uvicorn_task = asyncio.create_task(uvicorn_server.serve())

    await asyncio.gather(bot_task, uvicorn_task)


if __name__ == "__main__":
    asyncio.run(main())

