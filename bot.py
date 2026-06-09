"""
bot.py -- BOT002 Telegram Bot Runner

Actual Telegram bot runner using long polling.
Requires python-telegram-bot (see requirements_bot002.txt).

Environment variables required:
    TG_BOT_TOKEN        -- Telegram bot token (mandatory at runtime)
    BOT_API_BASE_URL    -- Flask API base URL (default: http://127.0.0.1:5051)
    BOT_STATE_DB        -- Local SQLite state DB path (default: instance\\bot_state.db)
    BOT_LOG_DIR         -- Log directory (default: logs)
    BOT_REQUEST_TIMEOUT_SECONDS -- HTTP timeout (default: 10)

Commands:
    /start   -- Welcome; show menu if linked, prompt to /link if not.
    /help    -- Show available commands.
    /menu    -- Show inline menu.
    /link    -- Link Telegram account with one-time code.
    /status  -- Show last 5 spare part requests.
    /pending -- Show pending (submitted) requests; admin only.
    /logout  -- Unlink account and delete local session.

No webhook. No HTTPS. No Mini App. Long polling only.
"""

import asyncio
import logging
# BOT002B log hardening: suppress HTTP logs that may contain Telegram bot token
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)

import os
import sys

# ---------------------------------------------------------------------------
# Ensure the bot directory is on sys.path
# ---------------------------------------------------------------------------
_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _BOT_DIR not in sys.path:
    sys.path.insert(0, _BOT_DIR)

import bot_config
import bot_state
import bot_formatters
from bot_http_client import api_get, api_post, BotApiError

# ---------------------------------------------------------------------------
# python-telegram-bot imports
# ---------------------------------------------------------------------------
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ParseMode
    from telegram.ext import (
        Application,
        CommandHandler,
        CallbackQueryHandler,
        ContextTypes,
    )
    _TG_AVAILABLE = True
except ImportError:
    _TG_AVAILABLE = False
    # Allow py_compile / import test to succeed without the runtime dep.
    # bot.py is only actually executed via main().
    class _Placeholder:
        """Placeholder so the module can be parsed without python-telegram-bot."""
    Update = _Placeholder
    InlineKeyboardButton = _Placeholder
    InlineKeyboardMarkup = _Placeholder
    ParseMode = type("ParseMode", (), {"HTML": "HTML"})()
    Application = _Placeholder
    CommandHandler = _Placeholder
    CallbackQueryHandler = _Placeholder
    ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": None})()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("bot002")

# ---------------------------------------------------------------------------
# Load settings (at module level, but don't validate -- allow offline import)
# ---------------------------------------------------------------------------
_settings = bot_config.load_settings()
_DB_PATH = _settings.bot_state_db
_API_BASE = _settings.bot_api_base_url
_TIMEOUT = _settings.bot_request_timeout

# ---------------------------------------------------------------------------
# Inline keyboard builder
# ---------------------------------------------------------------------------

def _main_menu_keyboard():
    """Build the main menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📦 Мои заявки", callback_data="status"),
            InlineKeyboardButton("🕐 На проверке", callback_data="pending"),
        ],
        [
            InlineKeyboardButton("❓ Помощь", callback_data="help"),
            InlineKeyboardButton("🚪 Выйти", callback_data="logout"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _get_local_session(telegram_id: int):
    """Return session dict or None."""
    try:
        return bot_state.get_session(_DB_PATH, telegram_id)
    except Exception as exc:
        logger.warning("Failed to read local session for %d: %s", telegram_id, exc)
        return None


def _delete_local_session(telegram_id: int) -> None:
    """Delete local session, ignore errors."""
    try:
        bot_state.delete_session(_DB_PATH, telegram_id)
    except Exception as exc:
        logger.warning("Failed to delete local session for %d: %s", telegram_id, exc)


def _verify_session_api(session: dict) -> dict | None:
    """Call /api/bot/me to verify the stored token is still valid.

    Returns user dict on success, None on 401/connection error.
    Does NOT raise.
    """
    token = session.get("api_token", "")
    if not token:
        return None
    try:
        resp = api_get(_API_BASE, "/api/bot/me", token=token, timeout=_TIMEOUT)
        return resp.get("user")
    except BotApiError as exc:
        if exc.status_code == 401:
            return None
        logger.warning("API error in _verify_session_api: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Unexpected error in _verify_session_api: %s", exc)
        return None


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    tg_id = update.effective_user.id

    session = _get_local_session(tg_id)
    if session:
        user = _verify_session_api(session)
        if user:
            text = bot_formatters.format_start_linked(user)
            await update.message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=_main_menu_keyboard(),
            )
            return
        else:
            # Token expired or revoked
            _delete_local_session(tg_id)
            logger.info("Deleted invalid session for telegram_id=%d", tg_id)

    # Not linked
    await update.message.reply_text(
        bot_formatters.format_not_linked(),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(
        bot_formatters.format_help(),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /menu
# ---------------------------------------------------------------------------

async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /menu command."""
    tg_id = update.effective_user.id
    session = _get_local_session(tg_id)

    if not session:
        await update.message.reply_text(
            bot_formatters.format_not_linked(),
            parse_mode=ParseMode.HTML,
        )
        return

    full_name = session.get("full_name") or session.get("username") or ""
    text = bot_formatters.format_menu(full_name)
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# /link <code>
# ---------------------------------------------------------------------------

async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /link <code> command."""
    tg_id = update.effective_user.id
    args = context.args or []

    if not args:
        await update.message.reply_text(
            bot_formatters.format_code_format_error(),
            parse_mode=ParseMode.HTML,
        )
        return

    code = args[0].strip()

    # Validate 6-digit format
    if not code.isdigit() or len(code) != 6:
        await update.message.reply_text(
            bot_formatters.format_code_format_error(),
            parse_mode=ParseMode.HTML,
        )
        return

    # POST to link/verify
    try:
        resp = api_post(
            _API_BASE,
            "/api/bot/link/verify",
            payload={"telegram_id": tg_id, "code": code},
            timeout=_TIMEOUT,
        )
    except BotApiError as exc:
        if exc.status_code == 401:
            await update.message.reply_text(
                bot_formatters.format_link_invalid_code(),
                parse_mode=ParseMode.HTML,
            )
        elif exc.status_code == 403:
            await update.message.reply_text(
                bot_formatters.format_error(
                    "Пользователь неактивен. Привязка Telegram недоступна."
                ),
                parse_mode=ParseMode.HTML,
            )
        elif exc.status_code == 409:
            await update.message.reply_text(
                bot_formatters.format_link_already_linked(),
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                bot_formatters.format_error(str(exc)),
                parse_mode=ParseMode.HTML,
            )
        return
    except Exception as exc:
        logger.error("Unexpected error in /link: %s", exc)
        await update.message.reply_text(
            bot_formatters.format_error("Ошибка соединения с сервером. Попробуйте позже."),
            parse_mode=ParseMode.HTML,
        )
        return

    # Success
    api_token = resp.get("api_token", "")
    user = resp.get("user", {})
    if not api_token or not user:
        await update.message.reply_text(
            bot_formatters.format_error("Ошибка: сервер не вернул токен. Попробуйте снова."),
            parse_mode=ParseMode.HTML,
        )
        return

    # Save to local state (token stored but not printed)
    try:
        bot_state.save_session(_DB_PATH, tg_id, api_token, user)
        logger.info("Session saved for telegram_id=%d user_id=%s", tg_id, user.get("id"))
    except Exception as exc:
        logger.error("Failed to save session for telegram_id=%d: %s", tg_id, exc)
        await update.message.reply_text(
            bot_formatters.format_error("Не удалось сохранить сессию. Попробуйте снова."),
            parse_mode=ParseMode.HTML,
        )
        return

    text = bot_formatters.format_link_success(user)
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command -- show last 5 requests."""
    tg_id = update.effective_user.id
    session = _get_local_session(tg_id)

    if not session:
        await update.message.reply_text(
            bot_formatters.format_not_linked(),
            parse_mode=ParseMode.HTML,
        )
        return

    token = session.get("api_token", "")
    try:
        resp = api_get(
            _API_BASE,
            "/api/bot/requests",
            token=token,
            params={"limit": 5},
            timeout=_TIMEOUT,
        )
    except BotApiError as exc:
        if exc.status_code == 401:
            _delete_local_session(tg_id)
            await update.message.reply_text(
                bot_formatters.format_unauthorized(),
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                bot_formatters.format_error(str(exc)),
                parse_mode=ParseMode.HTML,
            )
        return
    except Exception as exc:
        logger.error("Unexpected error in /status: %s", exc)
        await update.message.reply_text(
            bot_formatters.format_error("Ошибка соединения. Попробуйте позже."),
            parse_mode=ParseMode.HTML,
        )
        return

    requests_list = resp.get("requests") or []
    text = bot_formatters.format_request_list(requests_list, title="Последние заявки (5)")
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /pending
# ---------------------------------------------------------------------------

async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /pending command -- show submitted requests; admin only."""
    tg_id = update.effective_user.id
    session = _get_local_session(tg_id)

    if not session:
        await update.message.reply_text(
            bot_formatters.format_not_linked(),
            parse_mode=ParseMode.HTML,
        )
        return

    token = session.get("api_token", "")

    # Verify role via /api/bot/me
    try:
        me_resp = api_get(_API_BASE, "/api/bot/me", token=token, timeout=_TIMEOUT)
        user = me_resp.get("user", {})
    except BotApiError as exc:
        if exc.status_code == 401:
            _delete_local_session(tg_id)
            await update.message.reply_text(
                bot_formatters.format_unauthorized(),
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                bot_formatters.format_error(str(exc)),
                parse_mode=ParseMode.HTML,
            )
        return
    except Exception as exc:
        logger.error("Unexpected error in /pending (me check): %s", exc)
        await update.message.reply_text(
            bot_formatters.format_error("Ошибка соединения. Попробуйте позже."),
            parse_mode=ParseMode.HTML,
        )
        return

    if user.get("role") != "admin":
        await update.message.reply_text(
            bot_formatters.format_access_denied(),
            parse_mode=ParseMode.HTML,
        )
        return

    # Fetch submitted requests
    try:
        resp = api_get(
            _API_BASE,
            "/api/bot/requests",
            token=token,
            params={"status": "submitted", "limit": 10},
            timeout=_TIMEOUT,
        )
    except BotApiError as exc:
        await update.message.reply_text(
            bot_formatters.format_error(str(exc)),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as exc:
        logger.error("Unexpected error in /pending (requests): %s", exc)
        await update.message.reply_text(
            bot_formatters.format_error("Ошибка соединения. Попробуйте позже."),
            parse_mode=ParseMode.HTML,
        )
        return

    requests_list = resp.get("requests") or []
    text = bot_formatters.format_pending_list(requests_list)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /logout
# ---------------------------------------------------------------------------

async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /logout -- revoke API session and delete local state."""
    tg_id = update.effective_user.id
    session = _get_local_session(tg_id)

    if not session:
        await update.message.reply_text(
            "ℹ️ Вы не были авторизованы. Ничего не изменилось.",
            parse_mode=ParseMode.HTML,
        )
        return

    token = session.get("api_token", "")
    # Call /api/bot/logout (best effort -- delete local session regardless of result)
    if token:
        try:
            api_post(
                _API_BASE,
                "/api/bot/logout",
                token=token,
                timeout=_TIMEOUT,
            )
            logger.info("Server logout successful for telegram_id=%d", tg_id)
        except BotApiError as exc:
            # 401 = already expired/revoked; still delete locally
            if exc.status_code != 401:
                logger.warning("Server logout returned error for telegram_id=%d: %s", tg_id, exc)
        except Exception as exc:
            logger.warning("Unexpected error in /logout API call: %s", exc)

    _delete_local_session(tg_id)
    logger.info("Local session deleted for telegram_id=%d", tg_id)

    await update.message.reply_text(
        bot_formatters.format_logout_success(),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Callback query handler (inline keyboard buttons)
# ---------------------------------------------------------------------------

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()  # acknowledge immediately

    data = query.data

    # Reuse command handlers by delegating through message context
    # For callbacks we must use query.message.reply_text instead of update.message
    # We create a thin wrapper that routes to the right logic.
    tg_id = update.effective_user.id

    if data == "help":
        await query.message.reply_text(
            bot_formatters.format_help(),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "status":
        session = _get_local_session(tg_id)
        if not session:
            await query.message.reply_text(
                bot_formatters.format_not_linked(),
                parse_mode=ParseMode.HTML,
            )
            return
        token = session.get("api_token", "")
        try:
            resp = api_get(
                _API_BASE, "/api/bot/requests",
                token=token,
                params={"limit": 5},
                timeout=_TIMEOUT,
            )
            requests_list = resp.get("requests") or []
            text = bot_formatters.format_request_list(requests_list, "Последние заявки (5)")
        except BotApiError as exc:
            if exc.status_code == 401:
                _delete_local_session(tg_id)
                text = bot_formatters.format_unauthorized()
            else:
                text = bot_formatters.format_error(str(exc))
        except Exception as exc:
            logger.error("Callback status error: %s", exc)
            text = bot_formatters.format_error("Ошибка соединения.")
        await query.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    if data == "pending":
        session = _get_local_session(tg_id)
        if not session:
            await query.message.reply_text(
                bot_formatters.format_not_linked(),
                parse_mode=ParseMode.HTML,
            )
            return
        token = session.get("api_token", "")
        try:
            me_resp = api_get(_API_BASE, "/api/bot/me", token=token, timeout=_TIMEOUT)
            user = me_resp.get("user", {})
            if user.get("role") != "admin":
                await query.message.reply_text(
                    bot_formatters.format_access_denied(),
                    parse_mode=ParseMode.HTML,
                )
                return
            resp = api_get(
                _API_BASE, "/api/bot/requests",
                token=token,
                params={"status": "submitted", "limit": 10},
                timeout=_TIMEOUT,
            )
            requests_list = resp.get("requests") or []
            text = bot_formatters.format_pending_list(requests_list)
        except BotApiError as exc:
            if exc.status_code == 401:
                _delete_local_session(tg_id)
                text = bot_formatters.format_unauthorized()
            else:
                text = bot_formatters.format_error(str(exc))
        except Exception as exc:
            logger.error("Callback pending error: %s", exc)
            text = bot_formatters.format_error("Ошибка соединения.")
        await query.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    if data == "logout":
        session = _get_local_session(tg_id)
        if not session:
            await query.message.reply_text(
                "ℹ️ Вы не были авторизованы.",
                parse_mode=ParseMode.HTML,
            )
            return
        token = session.get("api_token", "")
        if token:
            try:
                api_post(_API_BASE, "/api/bot/logout", token=token, timeout=_TIMEOUT)
            except Exception:
                pass
        _delete_local_session(tg_id)
        await query.message.reply_text(
            bot_formatters.format_logout_success(),
            parse_mode=ParseMode.HTML,
        )
        return

    # Unknown callback
    await query.message.reply_text(
        "❓ Неизвестная команда.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the Telegram bot with long polling.

    This function:
    1. Validates settings (raises if TG_BOT_TOKEN not set).
    2. Initialises the local state DB.
    3. Starts long polling loop.
    4. Stops gracefully on Ctrl+C or SIGTERM.
    """
    if not _TG_AVAILABLE:
        print("ERROR: python-telegram-bot is not installed.")
        print("Install it first:")
        print('  "C:\\Program Files\\Python314\\python.exe" -m pip install -r requirements_bot002.txt')
        sys.exit(1)

    settings = bot_config.load_settings()
    try:
        bot_config.validate_runtime(settings)
    except ValueError as exc:
        print(f"CONFIGURATION ERROR: {exc}")
        sys.exit(1)

    # Init local state DB
    try:
        bot_state.init_state_db(settings.bot_state_db)
        logger.info("State DB ready at: %s", settings.bot_state_db)
    except Exception as exc:
        print(f"ERROR: Cannot initialise state DB at {settings.bot_state_db}: {exc}")
        sys.exit(1)

    logger.info("Starting BOT002 Telegram bot (long polling)...")
    logger.info("API base URL: %s", settings.bot_api_base_url)
    logger.info("State DB: %s", settings.bot_state_db)

    app = Application.builder().token(settings.tg_bot_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Bot started. Press Ctrl+C to stop.")
    # Python 3.14 compatibility: ensure an asyncio event loop exists before python-telegram-bot run_polling().
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app.run_polling(stop_signals=None)


if __name__ == "__main__":
    main()
