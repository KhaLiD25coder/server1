import os
import json
import logging
import asyncio
import psycopg2
import discord
from discord import app_commands
from fastapi import FastAPI
import uvicorn

# ---------------- Logging ---------------- #
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------- Config ---------------- #
TOKEN = os.getenv("DISCORD_BOT_TOKEN")   # make sure this matches your Render env var
GUILD_ID = os.getenv("GUILD_ID")
DB_URL = os.getenv("DATABASE_URL")       # supabase / postgres url

# ---------------- Discord ---------------- #
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------------- FastAPI ---------------- #
app = FastAPI()

# ---------------- Database ---------------- #
def init_db():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            id SERIAL PRIMARY KEY,
            license_key TEXT UNIQUE NOT NULL,
            expiry BIGINT NOT NULL,
            hwid TEXT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    logging.info("✅ Database initialized and schema ensured.")

def add_key_db(key: str, expiry: int, hwid: str = None):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO licenses (license_key, expiry, hwid) VALUES (%s, %s, %s) ON CONFLICT (license_key) DO NOTHING",
        (key, expiry, hwid),
    )
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"💾 Added key {key}")

def remove_key_db(key: str):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("DELETE FROM licenses WHERE license_key = %s", (key,))
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"🗑️ Removed key {key}")

def reset_hwid_db(key: str):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("UPDATE licenses SET hwid = NULL WHERE license_key = %s", (key,))
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"🔄 Reset HWID for key {key}")

def list_keys_db():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT license_key, expiry, hwid FROM licenses")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# ---------------- Discord Commands ---------------- #
@tree.command(name="addkey", description="Add a license key")
async def addkey(interaction: discord.Interaction, key: str, expiry: int):
    logging.info("🟡 /addkey triggered")
    add_key_db(key, expiry)
    await interaction.response.send_message(f"✅ Key `{key}` added with expiry `{expiry}`", ephemeral=True)

@tree.command(name="removekey", description="Remove a license key")
async def removekey(interaction: discord.Interaction, key: str):
    logging.info("🟡 /removekey triggered")
    remove_key_db(key)
    await interaction.response.send_message(f"🗑️ Key `{key}` removed", ephemeral=True)

@tree.command(name="resethwid", description="Reset HWID for a license key")
async def resethwid(interaction: discord.Interaction, key: str):
    logging.info("🟡 /resethwid triggered")
    reset_hwid_db(key)
    await interaction.response.send_message(f"🔄 HWID for `{key}` has been reset", ephemeral=True)

@tree.command(name="listkeys", description="List all license keys")
async def listkeys(interaction: discord.Interaction):
    logging.info("🟡 /listkeys triggered")
    rows = list_keys_db()
    if not rows:
        await interaction.response.send_message("📭 No keys found", ephemeral=True)
        return
    msg = "\n".join([f"🔑 {r[0]} | Expiry: {r[1]} | HWID: {r[2]}" for r in rows])
    await interaction.response.send_message(msg, ephemeral=True)

# ---------------- FastAPI Route ---------------- #
@app.get("/")
async def home():
    return {"status": "ok", "message": "Bot + API running!"}

# ---------------- Main ---------------- #
async def main():
    init_db()

    # --- Sync commands once bot is ready
    @bot.event
    async def on_ready():
        logging.info(f"✅ Bot online as {bot.user}")
        try:
            if GUILD_ID:
                await tree.sync(guild=discord.Object(id=int(GUILD_ID)))
                logging.info(f"✅ Synced commands to guild {GUILD_ID}")
            else:
                raise ValueError("No GUILD_ID set")
        except discord.Forbidden:
            logging.warning("⚠️ Guild sync forbidden. Falling back to global sync...")
            synced = await tree.sync()
            logging.info(f"🌍 Synced {len(synced)} commands globally instead")
        except Exception as e:
            logging.error(f"❌ Failed to sync commands: {e}")

    # Run bot + API in parallel
    bot_task = asyncio.create_task(bot.start(TOKEN))
    api_task = asyncio.create_task(
        uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)), log_level="info")).serve()
    )

    await asyncio.gather(bot_task, api_task)

if __name__ == "__main__":
    asyncio.run(main())

