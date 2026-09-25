"""
Inline RP bot for Telegram.

Flow:
1. User writes @bot <action> in a chat.
2. Bot returns one InlineQueryResultArticle:
   - title: "предложить <action> собеседника"
   - message: "<first_name> хочет <action>" (clickable names)
   - reply_markup: InlineKeyboard with "Принять"/"Отказ"
   - callback_data encodes action and initiator user_id.
3. User clicks the article -> bot sends the message with buttons.
4. Any user (presumably the partner) presses a button.
5. Bot edits the message to show result in past tense or refusal.
"""

import html
import re
import sqlite3
import tomllib
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
)


# -----------------------------
# Config loading
# -----------------------------
with open("config.toml", "rb") as f:
    config = tomllib.load(f)

BOT_TOKEN = config["BOT_TOKEN"]
BOT_USERNAME = config.get("BOT_USERNAME", "your_bot_username")
ACTIONS = config["actions"]  # dict: key -> [infinitive, past_form]
INFINITIVES = [v[0] for v in ACTIONS.values()]


# -----------------------------
# Database (sqlite3)
# -----------------------------
DB_PATH = Path("bot.db")


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            custom_name TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            extra TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def get_custom_name(user_id: int) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT custom_name FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def set_custom_name(user_id: int, name: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cur.fetchone():
        cur.execute(
            "UPDATE users SET custom_name = ? WHERE user_id = ?",
            (name, user_id),
        )
    else:
        cur.execute(
            "INSERT INTO users (user_id, custom_name) VALUES (?, ?)",
            (user_id, name),
        )
    conn.commit()
    conn.close()


async def get_display_name(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    """Return clickable HTML link for user (custom name or first_name)."""
    custom = get_custom_name(user_id)
    if custom:
        display = custom
    else:
        try:
            chat = await context.bot.get_chat(user_id)
            display = chat.first_name or str(user_id)
        except Exception:
            display = str(user_id)
    safe = html.escape(display)
    return f'<a href="tg://user?id={user_id}">{safe}</a>'


# -----------------------------
# Helpers
# -----------------------------
NAME_PATTERN = re.compile(r"^[A-Za-zА-Яа-яЁё\s]+$")


def validate_name(name: str) -> bool:
    name = name.strip()
    if not name:
        return False
    if len(name) > 64:
        return False
    return bool(NAME_PATTERN.fullmatch(name))


# -----------------------------
# Handlers
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    text = (
        "Этот бот работает только в инлайн-режиме.\n"
        f"Наберите в чате @{BOT_USERNAME} и действие, "
        f"например: @{BOT_USERNAME} обнять"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command — list available actions from config."""
    lines = ["Доступные действия (инфинитив):"]
    lines += [f"• {inf}" for inf in INFINITIVES]
    lines.append("")
    lines.append(f"Используйте бота инлайн: наберите в чате @{BOT_USERNAME} &lt;действие&gt;")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def setname_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /setname <имя> — set custom display name (ru/en only)."""
    args = context.args
    if not args:
        await update.message.reply_text(
            "Использование: /setname имя\n"
            "Имя может содержать только русские и английские буквы и пробелы.",
            parse_mode=ParseMode.HTML,
        )
        return

    name = " ".join(args).strip()
    if not validate_name(name):
        await update.message.reply_text(
            "Имя должно состоять только из русских и английских букв и пробелов (макс. 64 символа).",
            parse_mode=ParseMode.HTML,
        )
        return

    user_id = update.effective_user.id
    set_custom_name(user_id, name)
    await update.message.reply_text(
        f"Имя установлено: {html.escape(name)}",
        parse_mode=ParseMode.HTML,
    )


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline queries."""
    query_text = update.inline_query.query.strip().lower()

    if not query_text:
        results = [
            InlineQueryResultArticle(
                id="error_empty",
                title="Ошибка",
                input_message_content=InputTextMessageContent(
                    "пожалуйста, укажите действие"
                ),
            )
        ]
    elif query_text not in INFINITIVES:
        results = [
            InlineQueryResultArticle(
                id="error_unknown",
                title="Ошибка",
                input_message_content=InputTextMessageContent(
                    "неизвестное действие"
                ),
            )
        ]
    else:
        infinitive = query_text
        past_form = None
        for key, (inf, past) in ACTIONS.items():
            if inf == query_text:
                past_form = past
                break

        from_user = update.inline_query.from_user
        # Clickable initiator name
        initiator_name = await get_display_name(context, from_user.id)
        text = f"{initiator_name} хочет {infinitive}"

        callback_data = f"{infinitive}:{from_user.id}"

        keyboard = [
            [
                InlineKeyboardButton(
                    "Принять", callback_data=f"accept:{callback_data}"
                ),
                InlineKeyboardButton(
                    "Отказ", callback_data=f"decline:{callback_data}"
                ),
            ]
        ]

        results = [
            InlineQueryResultArticle(
                id=f"{infinitive}:{from_user.id}",
                title=f"предложить {infinitive} собеседника",
                input_message_content=InputTextMessageContent(
                    text, parse_mode=ParseMode.HTML
                ),
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        ]

    await context.bot.answer_inline_query(
        update.inline_query.id,
        results,
        cache_time=0,
        is_personal=True,
    )


async def callback_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle button presses on the sent message."""
    query = update.callback_query
    await query.answer()

    data = query.data
    try:
        prefix, payload = data.split(":", 1)
        infinitive, from_user_id_str = payload.split(":", 1)
        from_user_id = int(from_user_id_str)
    except ValueError:
        await query.edit_message_text("ошибка: неверные данные кнопки", parse_mode=ParseMode.HTML)
        return

    past_form = None
    for key, (inf, past) in ACTIONS.items():
        if inf == infinitive:
            past_form = past
            break
    if past_form is None:
        await query.edit_message_text("ошибка: неизвестное действие", parse_mode=ParseMode.HTML)
        return

    if prefix == "accept":
        initiator_name = await get_display_name(context, from_user_id)
        presser_name = await get_display_name(context, query.from_user.id)
        text = f"{initiator_name} {past_form} {presser_name}"
    elif prefix == "decline":
        text = f"предложение {infinitive} было отвергнуто"
    else:
        await query.edit_message_text("ошибка: неизвестное действие кнопки", parse_mode=ParseMode.HTML)
        return

    await query.edit_message_text(text, parse_mode=ParseMode.HTML)


# -----------------------------
# Main
# -----------------------------
def main() -> None:
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setname", setname_command))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(callback_query))

    app.run_polling()


if __name__ == "__main__":
    main()