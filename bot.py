import os
import asyncio
from datetime import time

import aiosqlite
import aiohttp
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

DB_PATH = "data.db"

# Le token vient d'une variable d'environnement
BOT_TOKEN = os.environ["BOT_TOKEN"]


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


async def add_account(chat_id: int, username: str):
    username = username.lower().lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO accounts(chat_id, username) VALUES(?, ?)",
            (chat_id, username),
        )
        await db.commit()


async def remove_account(chat_id: int, username: str):
    username = username.lower().lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM accounts WHERE chat_id = ? AND username = ?",
            (chat_id, username),
        )
        await db.commit()


async def list_accounts(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT username FROM accounts WHERE chat_id = ? ORDER BY username",
            (chat_id,),
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def set_auto_check(chat_id: int, enabled: bool):
    value = 1 if enabled else 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO chat_settings(chat_id, auto_check_enabled)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET auto_check_enabled = excluded.auto_check_enabled
            """,
            (chat_id, value),
        )
        await db.commit()


async def get_auto_check(chat_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT auto_check_enabled FROM chat_settings WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cur.fetchone()
        return bool(row[0]) if row else False


async def get_auto_chats():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT chat_id FROM chat_settings WHERE auto_check_enabled = 1"
        )
        rows = await cur.fetchall()
        return [r[0] for r in rows]


# -------------------------
# CHECK SHADOWBAN (HTTP, SSL désactivé)
# -------------------------
async def check_shadowban(username: str) -> str:
    username = username.lower().lstrip("@")
    url = f"https://hisubway.online/shadowban/?username={username}"

    try:
        # IMPORTANT : ssl=False pour éviter l'erreur CERTIFICATE_VERIFY_FAILED
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    return (
                        f"Résultat pour @{username} :\n"
                        f"URL: {url}\n\n"
                        f"❌ Impossible d'accéder au site (HTTP {resp.status})."
                    )
                html = (await resp.text()).lower()
    except asyncio.TimeoutError:
        return (
            f"Résultat pour @{username} :\n"
            f"URL: {url}\n\n"
            "❌ Délai dépassé en contactant le site."
        )
    except Exception as e:
        return (
            f"Résultat pour @{username} :\n"
            f"URL: {url}\n\n"
            f"❌ Erreur réseau: {e}"
        )

    lines = [f"Résultat pour @{username} :", f"URL: {url}", ""]

    if "exists." not in html:
        lines.append("❌ Le site indique que ce compte n'existe pas ou n'a pas pu être testé.")
        return "\n".join(lines)

    def detect(label: str, no_phrase: str, yes_phrase: str) -> str:
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
# COMMANDES TELEGRAM
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot Shadowban.\n\n"
        "Commandes :\n"
        "/add <username> – ajouter un compte\n"
        "/remove <username> – retirer un compte\n"
        "/list – lister les comptes\n"
        "/check <username> – vérifier un compte\n"
        "/autocheck – activer/désactiver la vérification auto (9h, 15h, 21h)"
    )


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage : /add <username>")
    user = context.args[0]
    await add_account(update.effective_chat.id, user)
    await update.message.reply_text(f"✅ @{user} ajouté à la surveillance.")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage : /remove <username>")
    user = context.args[0]
    await remove_account(update.effective_chat.id, user)
    await update.message.reply_text(f"❌ @{user} retiré de la surveillance.")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accounts = await list_accounts(update.effective_chat.id)
    if not accounts:
        return await update.message.reply_text("Aucun compte surveillé.")
    txt = "Comptes surveillés :\n" + "\n".join(f"• @{u}" for u in accounts)
    await update.message.reply_text(txt)


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage : /check <username>")
    user = context.args[0]
    await update.message.reply_text(f"⏳ Vérification de @{user}...")
    result = await check_shadowban(user)
    await update.message.reply_text(result)


async def cmd_autocheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current = await get_auto_check(chat_id)
    new_value = not current
    await set_auto_check(chat_id, new_value)
    if new_value:
        await update.message.reply_text("✅ Vérification automatique activée (9h, 15h, 21h).")
    else:
        await update.message.reply_text("❌ Vérification automatique désactivée.")


# -------------------------
# TÂCHE PLANIFIÉE 3x/JOUR
# -------------------------
async def scheduled_check(context: ContextTypes.DEFAULT_TYPE):
    bot = context.application.bot
    chats = await get_auto_chats()

    for chat_id in chats:
        accounts = await list_accounts(chat_id)
        if not accounts:
            continue

        await bot.send_message(chat_id, "🔁 Vérification automatique des comptes surveillés...")

        for user in accounts:
            try:
                result = await check_shadowban(user)
                await bot.send_message(chat_id, result)
                await asyncio.sleep(2)
            except Exception as e:
                await bot.send_message(chat_id, f"Erreur pour @{user} : {e}")


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