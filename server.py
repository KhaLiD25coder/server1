import os
import json
import sqlite3
import asyncio
import logging
import datetime
from typing import Optional, Tuple

from fastapi import FastAPI
import uvicorn
import discord
from discord.ext import commands

# ================== CONFIG ==================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1394437999596404748"))

DB_PATH = "licenses.db"
JSON_PATH = "licenses.json"

if not DISCORD_BOT_TOKEN:
    raise ValueError("❌ DISCORD_BOT_TOKEN not set in environment variables")

# ================== LOGGING ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("LicenseBot")

# ================== HELPERS ==================
def parse_expiry_any(val) -> Tuple[Optional[int], str]:
    """
    Accepts either:
      - int/str unix timestamp
      - 'YYYY-MM-DD' date string
      - None / empty
    Returns (expiry_ts or None, display_string)
    """
    if val is None or val == "":
        return None, "None"

    # Already int?
    if isinstance(val, int):
        try:
            ds = datetime.datetime.fromtimestamp(val, datetime.timezone.utc).strftime("%Y-%m-%d")
            return val, ds
        except Exception:
            return None, str(val)

    # String cases
    s = str(val).strip()
    # Try integer string first
    try:
        ts = int(s)
        ds = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")
        return ts, ds
    except Exception:
        pass

    # Try YYYY-MM-DD
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.datetime.strptime(s, fmt).replace(tzinfo=datetime.timezone.utc)
            ts = int(dt.timestamp())
            ds = dt.strftime("%Y-%m-%d")
            return ts, ds
        except Exception:
            continue

    # Fallback: unknown format, just show raw
    return None, s

# ================== DATABASE HELPERS ==================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Keep schema permissive (TEXT lets old rows with 'YYYY-MM-DD' live alongside ints)
    c.execute(
        """CREATE TABLE IF NOT EXISTS licenses (
            key TEXT PRIMARY KEY,
            expiry_date TEXT,
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

        log.info("💾 licenses.json updated successfully.")
    except Exception as e:
        log.error(f"❌ Failed to export DB to JSON: {e}")

# ================== FASTAPI APP ==================
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "Bot + API running"}

# UptimeRobot HEAD/GET health
@app.head("/")
async def head_root():
    return {}

@app.get("/health")
async def health():
    return {"ok": True}

# Simple license verify endpoint (kept same behavior)
@app.get("/verify")
async def verify(key: str, hwid: Optional[str] = None):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT expiry_date, hwid FROM licenses WHERE key=?", (key,))
        row = c.fetchone()
        conn.close()

        if not row:
            return {"ok": False, "reason": "not_found"}

        expiry_any, _ = parse_expiry_any(row[0])
        saved_hwid = row[1]

        now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

        if expiry_any is not None and expiry_any < now_ts:
            return {"ok": False, "reason": "expired"}

        if saved_hwid and hwid and saved_hwid != hwid:
            return {"ok": False, "reason": "hwid_mismatch"}

        # If no hwid set, bind it on first verify (optional — unchanged logic)
        if not saved_hwid and hwid:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE licenses SET hwid=? WHERE key=?", (hwid, key))
            conn.commit()
            conn.close()

        return {"ok": True}
    except Exception as e:
        log.error(f"❌ /verify error: {e}")
        return {"ok": False, "reason": "server_error"}

# ================== DISCORD BOT ==================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    log.info(f"✅ Bot online as {bot.user}")
    try:
        synced_global = await bot.tree.sync()  # global sync only (Unknown Integration fix)
        log.info(f"🌍 Synced {len(synced_global)} global commands")
    except Exception as e:
        log.error(f"❌ Failed to sync commands: {e}")

    # Startup key dump (robust to string or int expiry)
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT key, expiry_date, hwid FROM licenses")
        rows = c.fetchall()
        conn.close()

        now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        log.info("📜 Current Keys in Database (after startup):")

        active = []
        expired = []
        if rows:
            for row in rows:
                k = row[0]
                expiry_any, exp_disp = parse_expiry_any(row[1])
                hw = row[2]
                if expiry_any is not None and expiry_any < now_ts:
                    expired.append((k, exp_disp, hw))
                else:
                    active.append((k, exp_disp, hw))

        if not rows:
            log.info("   (No keys found)")
        else:
            if active:
                log.info("✅ Active keys:")
                for k, exp_disp, hw in active:
                    log.info(f"   🔑 {k} | Expiry: {exp_disp} | HWID: {hw}")
            if expired:
                log.info("❌ Expired keys:")
                for k, exp_disp, hw in expired:
                    log.info(f"   🔑 {k} | Expiry: {exp_disp} | HWID: {hw}")
    except Exception as e:
        log.error(f"❌ Error while logging keys on startup: {e}")

# ========== SLASH COMMANDS ==========
@bot.tree.command(name="listkeys", description="List all saved license keys")
async def listkeys(interaction: discord.Interaction):
    log.info("🟡 /listkeys triggered")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, expiry_date, hwid FROM licenses")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("No keys found.", ephemeral=True)
        return

    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    active_lines = []
    expired_lines = []

    for row in rows:
        k = row[0]
        expiry_any, exp_disp = parse_expiry_any(row[1])
        hw = row[2]
        if expiry_any is not None and expiry_any < now_ts:
            expired_lines.append(f"🔑 {k} | Expiry: {exp_disp} | HWID: {hw}")
        else:
            active_lines.append(f"🔑 {k} | Expiry: {exp_disp} | HWID: {hw}")

    out = []
    if active_lines:
        out.append("✅ **Active keys**")
        out.extend(active_lines)
    if expired_lines:
        out.append("\n❌ **Expired keys**")
        out.extend(expired_lines)

    msg = "\n".join(out)
    # Respond immediately (no defer) to avoid Unknown Interaction
    await interaction.response.send_message(msg[:1900], ephemeral=True)
    log.info("🟡 Sent list of keys")

@bot.tree.command(name="addkey", description="Add a new license key")
async def addkey(interaction: discord.Interaction, key: str, expiry_days: int = 365, hwid: Optional[str] = None):
    log.info("🟡 /addkey triggered")

    try:
        # compute expiry from 'days from now'
        expiry_ts = int((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=expiry_days)).timestamp())
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO licenses (key, expiry_date, hwid) VALUES (?, ?, ?)", (key, str(expiry_ts), hwid))
        conn.commit()
        conn.close()
        export_db_to_json()

        exp_disp = datetime.datetime.fromtimestamp(expiry_ts, datetime.timezone.utc).strftime("%Y-%m-%d")
        await interaction.response.send_message(f"✅ Key `{key}` added! (expires {exp_disp})", ephemeral=True)
        log.info(f"🟡 Added key {key} (expires {exp_disp})")
    except Exception as e:
        log.error(f"❌ Error in /addkey: {e}")
        await interaction.response.send_message("⚠️ Failed to add key", ephemeral=True)

@bot.tree.command(name="delkey", description="Delete a license key")
async def delkey(interaction: discord.Interaction, key: str):
    log.info("🟡 /delkey triggered")

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT key FROM licenses WHERE key=?", (key,))
        exists = c.fetchone()
        if exists:
            c.execute("DELETE FROM licenses WHERE key=?", (key,))
            conn.commit()
            conn.close()
            export_db_to_json()
            await interaction.response.send_message(f"🗑️ Key `{key}` deleted!", ephemeral=True)
            log.info(f"🟡 Deleted key {key}")
        else:
            conn.close()
            await interaction.response.send_message(f"⚠️ Key `{key}` not found.", ephemeral=True)
            log.info("🟡 Key not found in DB")
    except Exception as e:
        log.error(f"❌ Error in /delkey: {e}")
        await interaction.response.send_message("⚠️ Failed to delete key", ephemeral=True)

@bot.tree.command(name="resethwid", description="Reset the HWID for a license key")
async def resethwid(interaction: discord.Interaction, key: str):
    log.info("🟡 /resethwid triggered")

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE licenses SET hwid=NULL WHERE key=?", (key,))
        conn.commit()
        conn.close()
        export_db_to_json()

        await interaction.response.send_message(f"♻️ HWID reset for `{key}`!", ephemeral=True)
        log.info(f"🟡 Reset HWID for key {key}")
    except Exception as e:
        log.error(f"❌ Error in /resethwid: {e}")
        await interaction.response.send_message("⚠️ Failed to reset HWID", ephemeral=True)

# ================== MAIN ==================
async def main():
    init_db()
    import_json_to_db()

    loop = asyncio.get_event_loop()
    api = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)), log_level="info"))

    bot_task = loop.create_task(bot.start(DISCORD_BOT_TOKEN))
    api_task = loop.create_task(api.serve())

    await asyncio.gather(bot_task, api_task)

if __name__ == "__main__":
    asyncio.run(main())

