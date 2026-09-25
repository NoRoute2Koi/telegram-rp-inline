"""
Inline RP bot for Telegram.

Flow:
1. User writes @bot <action> in a chat.
2. Bot returns one InlineQueryResultArticle:
   - title: "предложить <action> собеседника"
   - message: "<first_name> хочет <action>"
   - reply_markup: InlineKeyboard with "Принять"/"Отказ"
   - callback_data encodes action and initiator user_id.
3. User clicks the article -> bot sends the message with buttons.
4. Any user (presumably the partner) presses a button.
5. Bot edits the message to show result in past tense or refusal.
"""

import tomllib
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
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
ACTIONS = config["actions"]  # dict: key -> [infinitive, past_form]
INFINITIVES = [v[0] for v in ACTIONS.values()]


# -----------------------------
# Handlers
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    await update.message.reply_text("test")


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline queries.

    - empty query -> error card "пожалуйста, укажите действие"
    - unknown action -> error card "неизвестное действие"
    - known action -> one article with send-message and buttons.
    """
    query = update.inline_query.query.strip().lower()

    if not query:
        results = [
            InlineQueryResultArticle(
                id="error_empty",
                title="Ошибка",
                input_message_content=InputTextMessageContent(
                    "пожалуйста, укажите действие"
                ),
            )
        ]
    elif query not in INFINITIVES:
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
        # Find infinitive and past form.
        infinitive = query
        past_form = None
        for key, (inf, past) in ACTIONS.items():
            if inf == query:
                past_form = past
                break

        # Build message that will be sent when user clicks the article.
        from_user = update.inline_query.from_user
        text = f"{from_user.first_name} хочет {infinitive}"

        # Callback data: action:infinitive:from_user_id
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
                # unique per user+query to avoid cache reuse
                id=f"{infinitive}:{from_user.id}",
                title=f"предложить {infinitive} собеседника",
                input_message_content=InputTextMessageContent(text),
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

    data = query.data  # format: "accept:<infinitive>:<user_id>" or "decline:<...>"
    try:
        prefix, payload = data.split(":", 1)
        infinitive, from_user_id_str = payload.split(":", 1)
        from_user_id = int(from_user_id_str)
    except ValueError:
        await query.edit_message_text("ошибка: неверные данные кнопки")
        return

    # Retrieve past form from config.
    past_form = None
    for key, (inf, past) in ACTIONS.items():
        if inf == infinitive:
            past_form = past
            break
    if past_form is None:
        await query.edit_message_text("ошибка: неизвестное действие")
        return

    if prefix == "accept":
        # Final message: "[initiator] past_form [presser]"
        presser_name = query.from_user.first_name
        text = f"{await _get_first_name(context, from_user_id)} {past_form} {presser_name}"
    elif prefix == "decline":
        # Final message: "предложение infinitive было отвергнуто"
        text = f"предложение {infinitive} было отвергнуто"
    else:
        await query.edit_message_text("ошибка: неизвестное действие кнопки")
        return

    # Edit the message to show final result and remove buttons.
    await query.edit_message_text(text)


async def _get_first_name(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    """Fetch first name of a user by ID (may raise if bot hasn't seen user)."""
    try:
        chat = await context.bot.get_chat(user_id)
        return chat.first_name or str(user_id)
    except Exception:
        return str(user_id)


# -----------------------------
# Main
# -----------------------------
def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(callback_query))

    app.run_polling()


if __name__ == "__main__":
    main()
