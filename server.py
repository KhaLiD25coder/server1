import os
import asyncio
import logging
import psycopg2
import discord
from discord import app_commands
from fastapi import FastAPI
import uvicorn

# ---------------- Logging ---------------- #
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------- Env / Config ---------------- #
# Try multiple variable names so it never ends up None by accident
TOKEN = (
    os.getenv("DISCORD_TOKEN")
    or os.getenv("DISCORD_BOT_TOKEN")
    or os.getenv("TOKEN")
)
if TOKEN:
    logging.info("🔑 Loaded Discord token from env (one of: DISCORD_TOKEN / DISCORD_BOT_TOKEN / TOKEN)")
else:
    logging.error("❌ No Discord token found in env (DISCORD_TOKEN / DISCORD_BOT_TOKEN / TOKEN). Bot will not start.")

GUILD_ID_RAW = os.getenv("GUILD_ID", "").strip()
GUILD_ID = int(GUILD_ID_RAW) if GUILD_ID_RAW.isdigit() else None
if GUILD_ID:
    logging.info(f"🛡️ Using GUILD_ID={GUILD_ID}")
else:
    logging.warning("⚠️ No valid GUILD_ID set. Will attempt global sync.")

DB_URL = os.getenv("DATABASE_URL")
if DB_URL:
    logging.info("🗄️ DATABASE_URL loaded.")
else:
    logging.error("❌ DATABASE_URL is missing! DB operations will fail.")

# ---------------- FastAPI ---------------- #
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "License server running"}

# ---------------- Discord ---------------- #
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------------- Database helpers ---------------- #
def init_db():
    """Create table if missing, add columns if missing (self-healing)."""
    if not DB_URL:
        logging.error("❌ Skipping DB init: DATABASE_URL missing.")
        return
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    # Keep the legacy column name 'key' for compatibility with your old data
    cur.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            key TEXT PRIMARY KEY
        );
    """)
    conn.commit()
    # Ensure required columns exist
    cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS expiry BIGINT;")
    cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS hwid TEXT;")
    conn.commit()
    cur.close()
    conn.close()
    logging.info("✅ Database initialized and schema ensured.")

def add_key_db(key: str, expiry: int):
    if not DB_URL:
        raise RuntimeError("DATABASE_URL not set")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO licenses (key, expiry, hwid) VALUES (%s, %s, %s) "
        "ON CONFLICT (key) DO UPDATE SET expiry = EXCLUDED.expiry",
        (key, expiry, None),
    )
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"🟡 Added/updated key {key} (expiry={expiry})")

def remove_key_db(key: str):
    if not DB_URL:
        raise RuntimeError("DATABASE_URL not set")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("DELETE FROM licenses WHERE key=%s", (key,))
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"🔴 Removed key {key}")

def reset_hwid_db(key: str):
    if not DB_URL:
        raise RuntimeError("DATABASE_URL not set")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("UPDATE licenses SET hwid = NULL WHERE key=%s", (key,))
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"🟠 Reset HWID for {key}")

def list_keys_db():
    if not DB_URL:
        raise RuntimeError("DATABASE_URL not set")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT key, expiry, hwid FROM licenses ORDER BY key ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# ---------------- Slash Commands ---------------- #
@tree.command(name="addkey", description="Add a new license key")
async def addkey(interaction: discord.Interaction, key: str, expiry: int):
    logging.info("🟡 /addkey triggered")
    try:
        add_key_db(key, expiry)
        await interaction.response.send_message(f"✅ Key `{key}` added with expiry `{expiry}`", ephemeral=True)
    except Exception as e:
        logging.exception("addkey failed")
        if interaction.response.is_done():
            await interaction.followup.send("⚠️ Something went wrong while adding the key.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Something went wrong while adding the key.", ephemeral=True)

@tree.command(name="removekey", description="Remove a license key")
async def removekey(interaction: discord.Interaction, key: str):
    logging.info("🟡 /removekey triggered")
    try:
        remove_key_db(key)
        await interaction.response.send_message(f"🗑️ Key `{key}` removed", ephemeral=True)
    except Exception as e:
        logging.exception("removekey failed")
        if interaction.response.is_done():
            await interaction.followup.send("⚠️ Something went wrong while removing the key.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Something went wrong while removing the key.", ephemeral=True)

@tree.command(name="resethwid", description="Reset HWID for a license key")
async def resethwid(interaction: discord.Interaction, key: str):
    logging.info("🟡 /resethwid triggered")
    try:
        reset_hwid_db(key)
        await interaction.response.send_message(f"🔄 HWID reset for `{key}`", ephemeral=True)
    except Exception as e:
        logging.exception("resethwid failed")
        if interaction.response.is_done():
            await interaction.followup.send("⚠️ Something went wrong while resetting HWID.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Something went wrong while resetting HWID.", ephemeral=True)

@tree.command(name="listkeys", description="List all license keys")
async def listkeys(interaction: discord.Interaction):
    logging.info("🟡 /listkeys triggered")
    try:
        rows = list_keys_db()
        if not rows:
            await interaction.response.send_message("📭 No keys found.", ephemeral=True)
            return
        msg = "\n".join([f"🔑 {k} | Expiry: {e} | HWID: {h}" for k, e, h in rows])
        await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        logging.exception("listkeys failed")
        if interaction.response.is_done():
            await interaction.followup.send("⚠️ Something went wrong while listing keys.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Something went wrong while listing keys.", ephemeral=True)

# Global error handler for app commands (extra safety)
@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logging.exception(f"❌ Error in command {interaction.command.name if interaction and interaction.command else None}: {error}")
    try:
        if interaction and interaction.response and interaction.response.is_done():
            await interaction.followup.send("⚠️ Something went wrong.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Something went wrong.", ephemeral=True)
    except Exception as e2:
        logging.error(f"❌ Failed to send error message: {e2}")

# ---------------- Boot & Sync ---------------- #
@bot.event
async def on_ready():
    logging.info(f"✅ Bot online as {bot.user}")
    # Log current keys at startup (useful on Render)
    try:
        rows = list_keys_db()
        logging.info("📜 Current Keys in Database (after startup):")
        for k, e, h in rows:
            logging.info(f"   🔑 {k} | Expiry: {e} | HWID: {h}")
    except Exception as e:
        logging.error(f"⚠️ Failed to list keys at startup: {e}")

    # Try guild sync first (fast), fallback to global
    try:
        if GUILD_ID:
            synced = await tree.sync(guild=discord.Object(id=GUILD_ID))
            logging.info(f"✅ Synced {len(synced)} commands to guild {GUILD_ID}")
        else:
            raise ValueError("No valid GUILD_ID")
    except discord.Forbidden:
        logging.warning("⚠️ Guild sync forbidden (Missing Access). Falling back to global sync...")
        synced = await tree.sync()
        logging.info(f"🌍 Synced {len(synced)} commands globally instead")
    except Exception as e:
        logging.error(f"❌ Failed to sync commands: {e}")

async def run_api():
    """Run uvicorn server; never raises so service stays alive."""
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)), log_level="info")
    )
    await server.serve()

async def run_bot():
    """Run the Discord bot; catch errors so API keeps running even if bot fails."""
    if not TOKEN:
        logging.error("❌ Bot not started: no token in env.")
        return
    try:
        logging.info("logging in using static token")
        await bot.start(TOKEN)
    except Exception as e:
        logging.exception(f"❌ Bot crashed: {e}")

async def main():
    init_db()
    # Run both concurrently; failures in one won't crash the other
    await asyncio.gather(run_api(), run_bot())

if __name__ == "__main__":
    asyncio.run(main())


