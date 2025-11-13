import os
import asyncio
from datetime import time
import aiosqlite

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time as t


BOT_TOKEN = "8545944887:AAFj0Qlj0mLxolWh2mDHYED5TPR6DASJ4uo"
DB_PATH = "data.db"


# -------------------------
# BASE DE DONNÉES
# -------------------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                chat_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                PRIMARY KEY (chat_id, username)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                auto_check_enabled INTEGER NOT NULL DEFAULT 0
            )
        """)

        await db.commit()


async def add_account(chat_id, username):
    username = username.lower().lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO accounts(chat_id, username) VALUES(?, ?)",
            (chat_id, username)
        )
        await db.commit()


async def remove_account(chat_id, username):
    username = username.lower().lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM accounts WHERE chat_id=? AND username=?",
            (chat_id, username)
        )
        await db.commit()


async def list_accounts(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT username FROM accounts WHERE chat_id=? ORDER BY username",
            (chat_id,)
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def set_auto_check(chat_id, enabled):
    v = 1 if enabled else 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO chat_settings(chat_id, auto_check_enabled)
            VALUES(?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET auto_check_enabled=excluded.auto_check_enabled
        """, (chat_id, v))
        await db.commit()


async def get_auto_check(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT auto_check_enabled FROM chat_settings WHERE chat_id=?",
            (chat_id,)
        )
        row = await cur.fetchone()
        return bool(row[0]) if row else False


async def get_auto_chats():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT chat_id FROM chat_settings WHERE auto_check_enabled=1"
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


# -------------------------
# SHADOWBAN CHECK
# -------------------------
async def check_shadowban(username):
    username = username.lower().lstrip("@")

    # 🔥 IMPORTANT : Bonne URL du site
    url = f"https://hisubway.online/shadowban/?username={username}"

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    driver.get(url)
    t.sleep(2)  # attendre chargement JS
    html = driver.page_source.lower()
    driver.quit()

    lines = [f"Résultat pour @{username} :", url, ""]

    # Détection
    if "exists." not in html:
        lines.append("❌ Le site indique que ce compte n'existe pas.")
        return "\n".join(lines)

    def detect(label, no_phrase, yes_phrase):
        np = no_phrase.lower()
        yp = yes_phrase.lower()
        if np in html:
            return f"✅ {label} : OK"
        if yp in html:
            return f"❌ {label} : POSITIF"
        return f"❓ {label} : indéterminé"

    lines.append(detect("Search Suggestion Ban", "No Search Suggestion Ban", "Search Suggestion Ban"))
    lines.append(detect("Search Ban", "No Search Ban", "Search Ban"))
    lines.append(detect("Ghost Ban", "No Ghost Ban", "Ghost Ban"))
    lines.append(detect("Reply Deboosting", "No Reply Deboosting", "Reply Deboosting"))

    return "\n".join(lines)


# -------------------------
# COMMANDES
# -------------------------
async def start(update, context):
    await update.message.reply_text(
        "/add <user>\n/remove <user>\n/list\n/check <user>\n/autocheck"
    )


async def cmd_add(update, context):
    if not context.args:
        return await update.message.reply_text("Usage : /add username")
    user = context.args[0]
    await add_account(update.effective_chat.id, user)
    await update.message.reply_text(f"Ajouté : @{user}")


async def cmd_remove(update, context):
    if not context.args:
        return await update.message.reply_text("Usage : /remove username")
    user = context.args[0]
    await remove_account(update.effective_chat.id, user)
    await update.message.reply_text(f"Retiré : @{user}")


async def cmd_list(update, context):
    accs = await list_accounts(update.effective_chat.id)
    if not accs:
        return await update.message.reply_text("Aucun compte surveillé.")
    await update.message.reply_text("\n".join(f"• @{a}" for a in accs))


async def cmd_check(update, context):
    if not context.args:
        return await update.message.reply_text("Usage : /check username")
    user = context.args[0]
    await update.message.reply_text(f"⏳ Vérification de @{user}...")
    result = await check_shadowban(user)
    await update.message.reply_text(result)


async def cmd_autocheck(update, context):
    chat = update.effective_chat.id
    current = await get_auto_check(chat)
    new = not current
    await set_auto_check(chat, new)
    msg = "Activé" if new else "Désactivé"
    await update.message.reply_text(f"Vérification automatique : {msg}.")


# -------------------------
# AUTO CHECK (9h / 15h / 21h)
# -------------------------
async def scheduled_check(context):
    bot = context.application.bot
    chats = await get_auto_chats()

    for chat in chats:
        accs = await list_accounts(chat)
        if not accs:
            continue

        await bot.send_message(chat, "🔁 Vérification automatique…")

        for user in accs:
            res = await check_shadowban(user)
            await bot.send_message(chat, res)
            await asyncio.sleep(2)


# -------------------------
# MAIN
# -------------------------
async def main():
    await init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("autocheck", cmd_autocheck))

    app.job_queue.run_daily(scheduled_check, time=time(hour=9))
    app.job_queue.run_daily(scheduled_check, time=time(hour=15))
    app.job_queue.run_daily(scheduled_check, time=time(hour=21))

    print("Bot lancé.")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())