import os
import sqlite3
import asyncio
import json
import discord
from discord import app_commands
from discord.ext import commands
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

# ---------------- CONFIG ---------------- #
TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # must be set in Render env vars
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1394437999596404748"))
DB_PATH = "licenses.db"
JSON_PATH = "licenses.json"

# ---------------- DATABASE ---------------- #
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            key TEXT PRIMARY KEY,
            expiry_date INTEGER,
            hwid TEXT
        )
    """)
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
        expiry = info.get("expiry_date") if isinstance(info, dict) else info
        hwid = info.get("hwid") if isinstance(info, dict) else None
        c.execute("INSERT OR REPLACE INTO licenses (key, expiry_date, hwid) VALUES (?, ?, ?)", (key, expiry, hwid))
    conn.commit()
    conn.close()

def export_db_to_json():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    data = {row[0]: {"expiry_date": row[1], "hwid": row[2]} for row in rows}
    with open(JSON_PATH, "w") as f:
        json.dump(data, f, indent=4)
    print("[DEBUG] Exported DB to JSON.")

# ---------------- DISCORD BOT ---------------- #
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

@tree.command(name="addkey", description="Add a new license key")
@app_commands.describe(key="The license key", expiry="Expiry date (UNIX timestamp)")
async def addkey(interaction: discord.Interaction, key: str, expiry: int):
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO licenses (key, expiry_date, hwid) VALUES (?, ?, ?)", (key, expiry, None))
    conn.commit()
    conn.close()
    export_db_to_json()

    await interaction.followup.send(f"✅ License `{key}` added with expiry `{expiry}`.", ephemeral=True)

@tree.command(name="listkeys", description="List all license keys")
async def listkeys(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await interaction.followup.send("⚠️ No keys found.", ephemeral=True)
        return

    msg = "\n".join([f"🔑 {r[0]} | Exp: {r[1]} | HWID: {r[2]}" for r in rows])
    await interaction.followup.send(f"**License Keys:**\n{msg}", ephemeral=True)

@tree.command(name="removekey", description="Remove a license key")
@app_commands.describe(key="The license key to remove")
async def removekey(interaction: discord.Interaction, key: str):
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM licenses WHERE key=?", (key,))
    conn.commit()
    conn.close()
    export_db_to_json()

    await interaction.followup.send(f"🗑️ License `{key}` removed.", ephemeral=True)

@tree.command(name="updatekey", description="Update expiry or HWID of an existing license key")
@app_commands.describe(key="The license key", expiry="New expiry date (UNIX timestamp, optional)", hwid="New HWID (optional)")
async def updatekey(interaction: discord.Interaction, key: str, expiry: int = None, hwid: str = None):
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT expiry_date, hwid FROM licenses WHERE key=?", (key,))
    row = c.fetchone()

    if not row:
        await interaction.followup.send(f"❌ License `{key}` not found.", ephemeral=True)
        conn.close()
        return

    current_expiry, current_hwid = row
    new_expiry = expiry if expiry is not None else current_expiry
    new_hwid = hwid if hwid is not None else current_hwid

    c.execute("UPDATE licenses SET expiry_date=?, hwid=? WHERE key=?", (new_expiry, new_hwid, key))
    conn.commit()
    conn.close()
    export_db_to_json()

    await interaction.followup.send(f"♻️ License `{key}` updated → Expiry: `{new_expiry}`, HWID: `{new_hwid}`.", ephemeral=True)

@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"✅ Slash commands synced to guild {GUILD_ID}")
    print(f"🤖 Bot online as {bot.user}")

# ---------------- FASTAPI ---------------- #
app = FastAPI()

@app.get("/")
async def home():
    return JSONResponse({"status": "running"})

# ---------------- MAIN ---------------- #
async def main():
    if not TOKEN:
        raise ValueError("❌ DISCORD_BOT_TOKEN not set in environment variables")

    init_db()
    import_json_to_db()

    # Use uvicorn.Server instead of uvicorn.run
    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)), loop="asyncio")
    server = uvicorn.Server(config)

    bot_task = asyncio.create_task(bot.start(TOKEN))
    uvicorn_task = asyncio.create_task(server.serve())

    await asyncio.gather(bot_task, uvicorn_task)

if __name__ == "__main__":
    asyncio.run(main())
