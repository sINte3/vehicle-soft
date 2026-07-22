"""
bot_formatters.py -- BOT002 Telegram Message Formatters

Format bot messages for Telegram in Russian.
No python-telegram-bot dependency.
All functions return plain strings safe for Telegram HTML or plain text.

Rules:
- Short, readable, Russian by default.
- Missing optional fields must not crash.
- Never include api_token in any output.
"""

# Status display mapping
_STATUS_LABELS = {
    "draft":     "Черновик",
    "submitted": "Подано",
    "approved":  "Одобрено",
    "rejected":  "Отклонено",
}

_ROLE_LABELS = {
    "admin":    "Администратор",
    "operator": "Оператор",
    "viewer":   "Наблюдатель",
}


# ---------------------------------------------------------------------------
# Help / Menu
# ---------------------------------------------------------------------------

def format_help() -> str:
    """Return help text listing all available bot commands."""
    return (
        "📋 <b>Доступные команды:</b>\n\n"
        "/start — Начало работы\n"
        "/help — Справка\n"
        "/menu — Главное меню\n"
        "/link &lt;код&gt; — Привязать аккаунт (6-значный код)\n"
        "/status — Мои последние заявки\n"
        "/pending — Заявки на проверку (только для администраторов)\n"
        "/logout — Отвязать аккаунт и выйти\n\n"
        "💡 Для привязки аккаунта: получите 6-значный код в веб-панели и "
        "введите <code>/link 123456</code>"
    )


def format_menu(full_name: str = "") -> str:
    """Return main menu text."""
    greeting = f"Привет, <b>{_esc(full_name)}</b>!" if full_name else "Привет!"
    return (
        f"{greeting}\n\n"
        "🏠 <b>Главное меню</b>\n\n"
        "Выберите действие из кнопок ниже или введите команду:\n"
        "/status — Мои заявки\n"
        "/pending — На проверке (для администраторов)\n"
        "/help — Справка\n"
        "/logout — Выйти"
    )


def format_not_linked() -> str:
    """Prompt user to link their Telegram account."""
    return (
        "🔗 <b>Аккаунт не привязан</b>\n\n"
        "Чтобы начать работу:\n"
        "1. Войдите в веб-панель\n"
        "2. Перейдите в настройки вашего профиля\n"
        "3. Нажмите «Получить код Telegram»\n"
        "4. Введите: <code>/link 123456</code> (замените 123456 на ваш код)\n\n"
        "Код действует 10 минут."
    )


def format_start_linked(user: dict) -> str:
    """Return welcome message for an already-linked user."""
    full_name = _safe_str(user, "full_name") or _safe_str(user, "username") or "Пользователь"
    role = _ROLE_LABELS.get(_safe_str(user, "role", ""), _safe_str(user, "role", ""))
    return (
        f"✅ <b>Добро пожаловать, {_esc(full_name)}!</b>\n"
        f"Роль: {_esc(role)}\n\n"
        + format_menu(full_name)
    )


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def format_profile(user: dict) -> str:
    """Format a user profile for display."""
    full_name = _safe_str(user, "full_name") or _safe_str(user, "username") or "—"
    username = _safe_str(user, "username") or "—"
    role = _ROLE_LABELS.get(_safe_str(user, "role", ""), _safe_str(user, "role", "—"))
    telegram_id = user.get("telegram_id") or "—"
    orgs = user.get("organizations") or []
    org_names = ", ".join(_safe_str(o, "name") for o in orgs if isinstance(o, dict)) or "—"

    return (
        "👤 <b>Профиль</b>\n\n"
        f"Имя: {_esc(full_name)}\n"
        f"Логин: {_esc(username)}\n"
        f"Роль: {_esc(role)}\n"
        f"Telegram ID: {telegram_id}\n"
        f"Организации: {_esc(org_names)}"
    )


# ---------------------------------------------------------------------------
# Request lists
# ---------------------------------------------------------------------------

def format_request_list(requests: list, title: str = "Последние заявки") -> str:
    """Format a list of spare part requests.

    Args:
        requests: List of request dicts from the API.
        title:    Section title.

    Returns:
        Formatted string, safe for Telegram HTML.
    """
    if not requests:
        return format_empty_requests(title)

    lines = [f"📦 <b>{_esc(title)}</b>\n"]
    for r in requests:
        rid = r.get("id", "?")
        status_raw = _safe_str(r, "status", "?")
        status = _STATUS_LABELS.get(status_raw, status_raw)
        org = _safe_str(r, "organization_name") or "—"
        eq = _safe_str(r, "equipment_name") or "—"
        items_count = r.get("items_count", 0)
        req_date = (_safe_str(r, "request_date") or "")[:10]

        lines.append(
            f"  ▪ <b>№{rid}</b> [{_esc(status)}] {req_date}\n"
            f"    Орг: {_esc(org)} | Техника: {_esc(eq)} | Позиций: {items_count}"
        )
    return "\n".join(lines)


def format_pending_list(requests: list) -> str:
    """Format a list of pending (submitted) requests for admin review."""
    return format_request_list(requests, title="Заявки на проверке")


def format_empty_requests(title: str = "Заявки") -> str:
    """Return a friendly empty-state message."""
    return f"📭 <b>{_esc(title)}</b>\n\nЗаявок не найдено."


# ---------------------------------------------------------------------------
# Error messages
# ---------------------------------------------------------------------------

def format_error(message: str) -> str:
    """Format a generic error message."""
    return f"❌ <b>Ошибка</b>\n\n{_esc(message)}"


def format_unauthorized() -> str:
    """Return message for session expired / unauthorized."""
    return (
        "🔒 <b>Сессия истекла или недействительна</b>\n\n"
        "Пожалуйста, привяжите аккаунт снова:\n"
        "Получите новый код в веб-панели и введите <code>/link 123456</code>"
    )


def format_access_denied() -> str:
    """Return message for insufficient permissions."""
    return "⛔ <b>Доступ запрещён</b>\n\nЭта функция доступна только администраторам."


def format_link_success(user: dict) -> str:
    """Return success message after linking Telegram account."""
    full_name = _safe_str(user, "full_name") or _safe_str(user, "username") or "Пользователь"
    role = _ROLE_LABELS.get(_safe_str(user, "role", ""), _safe_str(user, "role", ""))
    return (
        f"✅ <b>Аккаунт успешно привязан!</b>\n\n"
        f"Имя: {_esc(full_name)}\n"
        f"Роль: {_esc(role)}\n\n"
        + format_menu(full_name)
    )


def format_link_invalid_code() -> str:
    """Return message for invalid or expired link code."""
    return (
        "❌ <b>Неверный или истёкший код</b>\n\n"
        "Проверьте код и попробуйте снова. Код действует 10 минут.\n"
        "Получите новый код в веб-панели."
    )


def format_link_already_linked() -> str:
    """Return message when Telegram is already linked to another account."""
    return (
        "⚠️ <b>Этот Telegram-аккаунт уже привязан к другому пользователю</b>\n\n"
        "Если вы считаете, что это ошибка, обратитесь к администратору."
    )


def format_logout_success() -> str:
    """Return message after successful logout."""
    return (
        "👋 <b>Вы вышли из системы</b>\n\n"
        "Ваш Telegram-аккаунт отвязан. Локальная сессия удалена.\n"
        "Для повторного входа используйте /link &lt;код&gt;."
    )


def format_code_format_error() -> str:
    """Return message for invalid code format in /link command."""
    return (
        "⚠️ <b>Неверный формат кода</b>\n\n"
        "Код должен состоять из 6 цифр.\n"
        "Пример: <code>/link 123456</code>"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_str(d: dict, key: str, default: str = "") -> str:
    """Safely get a string value from a dict."""
    try:
        val = d.get(key, default)
        if val is None:
            return default
        return str(val)
    except Exception:
        return default


def _esc(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    if not text:
        return ""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
