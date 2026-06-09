"""
bot_config.py -- BOT002 Telegram Runner Configuration

Reads environment variables and provides a BotSettings dataclass.
No python-telegram-bot dependency.
No real secrets stored here.
Stdlib only.
"""

import os
from dataclasses import dataclass, field


@dataclass
class BotSettings:
    """Validated settings for the Telegram bot runner.

    All values come from environment variables.
    No defaults for security-sensitive fields (TG_BOT_TOKEN).
    """
    # Required at runtime -- read from env, never hardcoded
    tg_bot_token: str = field(default="")

    # API base URL -- staging default
    bot_api_base_url: str = field(default="http://127.0.0.1:5051")

    # Local state DB path
    bot_state_db: str = field(default=os.path.join("instance", "bot_state.db"))

    # Log directory
    bot_log_dir: str = field(default="logs")

    # Request timeout in seconds
    bot_request_timeout: int = field(default=10)


def load_settings() -> BotSettings:
    """Load settings from environment variables.

    Returns a BotSettings dataclass populated from the environment.
    Does not raise if TG_BOT_TOKEN is missing -- call validate_runtime()
    before actually starting the bot.
    """
    timeout_raw = os.environ.get("BOT_REQUEST_TIMEOUT_SECONDS", "10")
    try:
        timeout = int(timeout_raw)
        if timeout < 1 or timeout > 120:
            timeout = 10
    except (ValueError, TypeError):
        timeout = 10

    return BotSettings(
        tg_bot_token=os.environ.get("TG_BOT_TOKEN", ""),
        bot_api_base_url=os.environ.get("BOT_API_BASE_URL", "http://127.0.0.1:5051"),
        bot_state_db=os.environ.get("BOT_STATE_DB", os.path.join("instance", "bot_state.db")),
        bot_log_dir=os.environ.get("BOT_LOG_DIR", "logs"),
        bot_request_timeout=timeout,
    )


def validate_runtime(settings: BotSettings) -> None:
    """Validate that all required settings are present for runtime.

    Raises ValueError with a descriptive message if any required value
    is missing or clearly invalid.

    Call this just before starting the bot runner -- not on import.
    """
    if not settings.tg_bot_token:
        raise ValueError(
            "TG_BOT_TOKEN environment variable is required to run the bot. "
            "Set it before starting: set TG_BOT_TOKEN=<your-token>"
        )
    if not settings.tg_bot_token.count(":") == 1:
        raise ValueError(
            "TG_BOT_TOKEN does not look like a valid Telegram bot token "
            "(expected format: 123456789:ABCdef...). Check the value."
        )
    if not settings.bot_api_base_url.startswith(("http://", "https://")):
        raise ValueError(
            "BOT_API_BASE_URL must start with http:// or https://. "
            "Got: " + repr(settings.bot_api_base_url)
        )
    if not settings.bot_state_db:
        raise ValueError(
            "BOT_STATE_DB must not be empty. "
            "Default: instance\\bot_state.db"
        )
