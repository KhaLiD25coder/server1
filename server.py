import os
import json
import sqlite3
import asyncio
import discord
from discord.ext import commands
from fastapi import FastAPI
import uvicorn

DB_PATH = "licenses.db"
JSON_PATH = "licenses.json"
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "1394437999596404748"))  # default to your guild

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
app = FastAPI()


# ---------------- DATABASE ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Drop any old table to avoid wrong schema
    c.execute("DROP TABLE IF EXISTS licenses")

    # Fresh schema
    c.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            license_key TEXT PRIMARY KEY,
            expiry_date INTEGER,
            hwid TEXT
        )
    """)
    conn.commit()
    conn.close()
    print("[DEBUG] Database initialized at", DB_PATH)


def import_json_to_db():
    if not os.path.exists(JSON_PATH):
        print("[DEBUG] No licenses.json found, skipping import.")
        return

    with open(JSON_PATH, "r") as f:
        data = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    for key, info in data.items():
        if isinstance(info, dict):
            expiry = info.get("expiry_date")
            hwid = info.get("hwid")
        else:
            expiry = info
            hwid = None

        c.execute(
            "INSERT OR REPLACE INTO licenses (license_key, expiry_date, hwid) VALUES (?, ?, ?)",
            (key, expiry, hwid),
        )

    conn.commit()
    conn.close()
    print(f"[DEBUG] Imported {len(data)} keys from JSON.")


# ---------------- DISCORD BOT ----------------
@bot.event
async def on_ready():
    print(f"🤖 Bot online as {bot.user}")


@bot.tree.command(name="listkeys", description="List all license keys")
async def listkeys(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT license_key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("No license keys found.", ephemeral=True)
        return

    msg = "\n".join(
        [f"🔑 {r[0]} | Exp: {r[1]} | HWID: {r[2] or 'None'}" for r in rows]
    )
    await interaction.response.send_message(msg, ephemeral=True)


# ---------------- FASTAPI ----------------
@app.get("/")
async def root():
    return {"status": "ok", "message": "License server running"}


# ---------------- MAIN ----------------
async def main():
    init_db()
    import_json_to_db()

    # Run bot + API together
    config = uvicorn.Config(app, host="0.0.0.0", port=10000, log_level="info")
    server = uvicorn.Server(config)

    await asyncio.gather(
        bot.start(TOKEN),
        server.serve()
    )


if __name__ == "__main__":
    asyncio.run(main())
