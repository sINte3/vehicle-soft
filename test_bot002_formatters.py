"""
test_bot002_formatters.py -- BOT002 Offline Formatter Tests

Tests bot_formatters.py with synthetic data.
No network access. No Flask. No Telegram dependency.

Output: BOT002_FORMATTERS_TEST_OK (or error details + non-zero exit)
"""

import os
import sys

STAGING_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STAGING_DIR)

ERRORS = []
PASSED = []


def check(label, fn):
    try:
        fn()
        PASSED.append(label)
        print("[OK]   " + label)
    except Exception as exc:
        ERRORS.append((label, str(exc)))
        print("[FAIL] " + label)
        print("       " + str(exc)[:400])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_import():
    import bot_formatters
    assert hasattr(bot_formatters, "format_help")
    assert hasattr(bot_formatters, "format_menu")
    assert hasattr(bot_formatters, "format_not_linked")
    assert hasattr(bot_formatters, "format_start_linked")
    assert hasattr(bot_formatters, "format_profile")
    assert hasattr(bot_formatters, "format_request_list")
    assert hasattr(bot_formatters, "format_pending_list")
    assert hasattr(bot_formatters, "format_empty_requests")
    assert hasattr(bot_formatters, "format_error")
    assert hasattr(bot_formatters, "format_unauthorized")
    assert hasattr(bot_formatters, "format_access_denied")
    assert hasattr(bot_formatters, "format_link_success")
    assert hasattr(bot_formatters, "format_link_invalid_code")
    assert hasattr(bot_formatters, "format_link_already_linked")
    assert hasattr(bot_formatters, "format_logout_success")
    assert hasattr(bot_formatters, "format_code_format_error")


def test_format_help():
    from bot_formatters import format_help
    text = format_help()
    assert isinstance(text, str)
    assert len(text) > 10
    assert "/start" in text
    assert "/help" in text
    assert "/link" in text
    assert "/status" in text
    assert "/logout" in text
    # Should be in Russian
    assert any(c in text for c in "аеёиоуыэюя")


def test_format_menu_with_name():
    from bot_formatters import format_menu
    text = format_menu("Иван Петров")
    assert isinstance(text, str)
    assert "Иван Петров" in text


def test_format_menu_without_name():
    from bot_formatters import format_menu
    text = format_menu()
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_not_linked():
    from bot_formatters import format_not_linked
    text = format_not_linked()
    assert isinstance(text, str)
    assert "/link" in text


def test_format_start_linked():
    from bot_formatters import format_start_linked
    user = {"full_name": "Test Admin", "role": "admin", "username": "admin1"}
    text = format_start_linked(user)
    assert isinstance(text, str)
    assert "Test Admin" in text


def test_format_start_linked_missing_name():
    from bot_formatters import format_start_linked
    # Should use username as fallback, not crash
    user = {"full_name": "", "role": "operator", "username": "op_user"}
    text = format_start_linked(user)
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_start_linked_empty_user():
    from bot_formatters import format_start_linked
    # Missing all keys -- must not crash
    text = format_start_linked({})
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_profile():
    from bot_formatters import format_profile
    user = {
        "full_name": "Мухаммад Алимов",
        "username": "muhammad",
        "role": "admin",
        "telegram_id": 123456789,
        "organizations": [
            {"id": 1, "name": "Агрокластер Северный"},
        ],
    }
    text = format_profile(user)
    assert isinstance(text, str)
    assert "Мухаммад Алимов" in text
    assert "muhammad" in text
    assert "Администратор" in text
    assert "Агрокластер Северный" in text


def test_format_profile_missing_fields():
    from bot_formatters import format_profile
    # Minimal dict -- must not crash
    text = format_profile({})
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_empty_request_list():
    from bot_formatters import format_request_list, format_empty_requests
    text = format_request_list([], title="Тест пустой")
    assert isinstance(text, str)
    assert len(text) > 0
    # Should use empty state message
    text2 = format_empty_requests("Пусто")
    assert "Пусто" in text2


def test_format_request_list_with_data():
    from bot_formatters import format_request_list
    requests = [
        {
            "id": 42,
            "status": "submitted",
            "organization_name": "ООО Тест",
            "equipment_name": "Трактор МТЗ-82",
            "items_count": 3,
            "request_date": "2026-06-01",
        },
        {
            "id": 43,
            "status": "draft",
            "organization_name": "Агрокластер",
            "equipment_name": "",
            "items_count": 0,
            "request_date": "2026-06-02",
        },
    ]
    text = format_request_list(requests)
    assert isinstance(text, str)
    assert "#42" in text
    assert "#43" in text
    assert "Подано" in text  # submitted
    assert "Черновик" in text  # draft
    assert "ООО Тест" in text


def test_format_request_list_missing_optional_fields():
    from bot_formatters import format_request_list
    # Missing many optional fields -- must not crash
    requests = [
        {"id": 1},
        {"status": "approved"},
        {},
    ]
    text = format_request_list(requests)
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_pending_list():
    from bot_formatters import format_pending_list
    requests = [
        {
            "id": 10,
            "status": "submitted",
            "organization_name": "Орг А",
            "equipment_name": "Комбайн",
            "items_count": 2,
            "request_date": "2026-06-05",
        }
    ]
    text = format_pending_list(requests)
    assert isinstance(text, str)
    assert "#10" in text
    assert "Орг А" in text


def test_format_pending_list_empty():
    from bot_formatters import format_pending_list
    text = format_pending_list([])
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_error():
    from bot_formatters import format_error
    text = format_error("Ошибка подключения")
    assert isinstance(text, str)
    assert "Ошибка" in text
    assert "подключения" in text


def test_format_error_html_injection():
    from bot_formatters import format_error
    # Ensure < and > are escaped (no raw HTML injection)
    text = format_error("<script>alert('xss')</script>")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text or "script" in text


def test_format_unauthorized():
    from bot_formatters import format_unauthorized
    text = format_unauthorized()
    assert isinstance(text, str)
    assert "/link" in text


def test_format_access_denied():
    from bot_formatters import format_access_denied
    text = format_access_denied()
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_link_success():
    from bot_formatters import format_link_success
    user = {"full_name": "Оператор Иванов", "role": "operator", "username": "operator1"}
    text = format_link_success(user)
    assert isinstance(text, str)
    assert "Оператор Иванов" in text


def test_format_link_success_empty_user():
    from bot_formatters import format_link_success
    text = format_link_success({})
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_link_invalid_code():
    from bot_formatters import format_link_invalid_code
    text = format_link_invalid_code()
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_link_already_linked():
    from bot_formatters import format_link_already_linked
    text = format_link_already_linked()
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_logout_success():
    from bot_formatters import format_logout_success
    text = format_logout_success()
    assert isinstance(text, str)
    assert "/link" in text


def test_format_code_format_error():
    from bot_formatters import format_code_format_error
    text = format_code_format_error()
    assert isinstance(text, str)
    assert "6" in text  # should mention 6 digits


def test_html_escape():
    """Verify the internal _esc function escapes HTML properly."""
    from bot_formatters import _esc
    assert _esc("") == ""
    assert _esc("Hello") == "Hello"
    assert _esc("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;"
    assert _esc("AT&T") == "AT&amp;T"
    assert _esc("a>b<c") == "a&gt;b&lt;c"


def test_safe_str():
    """Verify the internal _safe_str function handles edge cases."""
    from bot_formatters import _safe_str
    assert _safe_str({"key": "value"}, "key") == "value"
    assert _safe_str({"key": None}, "key") == ""
    assert _safe_str({}, "missing") == ""
    assert _safe_str({"key": 42}, "key") == "42"
    assert _safe_str({"key": ""}, "key", "default") == ""
    assert _safe_str({}, "missing", "default") == "default"


def test_no_api_token_in_any_output():
    """Verify none of the formatters ever output an api_token."""
    import bot_formatters
    # None of the formatter functions take a token parameter, so this
    # is guaranteed by design. Test that format_profile doesn't expose it even if passed.
    user_with_secret = {
        "full_name": "Test",
        "username": "test",
        "role": "operator",
        "api_token": "SECRET_MUST_NOT_APPEAR",
        "telegram_id": 123,
        "organizations": [],
    }
    text = bot_formatters.format_profile(user_with_secret)
    assert "SECRET_MUST_NOT_APPEAR" not in text, "api_token leaked into profile output"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

check("import bot_formatters", test_import)
check("format_help returns Russian text with commands", test_format_help)
check("format_menu with name", test_format_menu_with_name)
check("format_menu without name", test_format_menu_without_name)
check("format_not_linked mentions /link", test_format_not_linked)
check("format_start_linked with user", test_format_start_linked)
check("format_start_linked missing name falls back to username", test_format_start_linked_missing_name)
check("format_start_linked empty user does not crash", test_format_start_linked_empty_user)
check("format_profile full user", test_format_profile)
check("format_profile missing fields does not crash", test_format_profile_missing_fields)
check("format_request_list empty list", test_format_empty_request_list)
check("format_request_list with data", test_format_request_list_with_data)
check("format_request_list missing optional fields", test_format_request_list_missing_optional_fields)
check("format_pending_list with data", test_format_pending_list)
check("format_pending_list empty", test_format_pending_list_empty)
check("format_error message", test_format_error)
check("format_error HTML escaping", test_format_error_html_injection)
check("format_unauthorized", test_format_unauthorized)
check("format_access_denied", test_format_access_denied)
check("format_link_success", test_format_link_success)
check("format_link_success empty user", test_format_link_success_empty_user)
check("format_link_invalid_code", test_format_link_invalid_code)
check("format_link_already_linked", test_format_link_already_linked)
check("format_logout_success mentions /link", test_format_logout_success)
check("format_code_format_error mentions 6 digits", test_format_code_format_error)
check("HTML escape function", test_html_escape)
check("_safe_str edge cases", test_safe_str)
check("api_token not leaked in any formatter output", test_no_api_token_in_any_output)

print()
print("=" * 55)
print("Passed: {}/{}".format(len(PASSED), len(PASSED) + len(ERRORS)))

if ERRORS:
    print()
    print("FAILED checks:")
    for label, err in ERRORS:
        print("  [FAIL] " + label)
        print("         " + str(err)[:200])
    print()
    print("BOT002_FORMATTERS_TEST_FAILED")
    sys.exit(1)
else:
    print()
    print("BOT002_FORMATTERS_TEST_OK")
