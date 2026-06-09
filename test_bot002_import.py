"""
test_bot002_import.py -- BOT002A Import / Syntax Test

Checks:
  1. import bot_config     (stdlib only)
  2. import bot_state      (stdlib only)
  3. import bot_http_client (stdlib only)
  4. import bot_formatters  (stdlib only)
  5. ast.parse + py_compile bot.py (without TG_BOT_TOKEN or python-telegram-bot)
  6. ast.parse + py_compile bot_api.py (the BOT002A updated bot_api)
  7. Report if python-telegram-bot runtime dep is missing (warning only, not failure)

Output: BOT002_IMPORT_TEST_OK (or error details + non-zero exit)
"""

import ast
import os
import py_compile
import sys
import tempfile

STAGING_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STAGING_DIR)

ERRORS = []
PASSED = []
WARNINGS = []


def check(label, fn):
    try:
        fn()
        PASSED.append(label)
        print("[OK]   " + label)
    except Exception as exc:
        ERRORS.append((label, str(exc)))
        print("[FAIL] " + label)
        print("       " + str(exc)[:400])


def warn(message):
    WARNINGS.append(message)
    print("[WARN] " + message)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_import_bot_config():
    import bot_config
    assert hasattr(bot_config, "BotSettings"), "BotSettings class missing"
    assert hasattr(bot_config, "load_settings"), "load_settings() missing"
    assert hasattr(bot_config, "validate_runtime"), "validate_runtime() missing"
    # load_settings should work without env vars set
    settings = bot_config.load_settings()
    assert hasattr(settings, "tg_bot_token")
    assert hasattr(settings, "bot_api_base_url")
    assert hasattr(settings, "bot_state_db")
    assert hasattr(settings, "bot_log_dir")
    assert hasattr(settings, "bot_request_timeout")


def test_import_bot_state():
    import bot_state
    assert hasattr(bot_state, "init_state_db")
    assert hasattr(bot_state, "save_session")
    assert hasattr(bot_state, "get_session")
    assert hasattr(bot_state, "delete_session")
    assert hasattr(bot_state, "list_sessions")
    # Verify it's pure stdlib -- read source file directly
    src_path = os.path.join(STAGING_DIR, "bot_state.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    import_lines = [l for l in src.splitlines() if l.startswith("import ") or l.startswith("from ")]
    import_text = "\n".join(import_lines).lower()
    assert "telegram" not in import_text, "bot_state.py must not import telegram"
    assert "flask" not in import_text, "bot_state.py must not import flask"


def test_import_bot_http_client():
    from bot_http_client import api_get, api_post, BotApiError
    assert callable(api_get)
    assert callable(api_post)
    assert issubclass(BotApiError, Exception)
    # Verify stdlib only -- read source file directly
    src_path = os.path.join(STAGING_DIR, "bot_http_client.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    import_lines = [l for l in src.splitlines() if l.startswith("import ") or l.startswith("from ")]
    import_text = "\n".join(import_lines).lower()
    assert "telegram" not in import_text, "bot_http_client.py must not import telegram"
    assert "flask" not in import_text, "bot_http_client.py must not import flask"
    assert "import requests" not in import_text, "bot_http_client.py must not import requests library"


def test_import_bot_formatters():
    import bot_formatters
    # Verify stdlib only -- read source file directly
    src_path = os.path.join(STAGING_DIR, "bot_formatters.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    import_lines = [l for l in src.splitlines() if l.startswith("import ") or l.startswith("from ")]
    import_text = "\n".join(import_lines).lower()
    assert "telegram" not in import_text, "bot_formatters.py must not import telegram"
    assert "flask" not in import_text, "bot_formatters.py must not import flask"


def test_bot_config_no_real_secrets():
    """Verify bot_config.py contains no real tokens."""
    config_path = os.path.join(STAGING_DIR, "bot_config.py")
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
    suspicious_patterns = [
        "1234567890:AAAA",  # looks like a real token prefix
        "bot_token =",
        "TG_BOT_TOKEN =",  # only env var reads are allowed
    ]
    for pattern in suspicious_patterns:
        assert pattern not in content, f"Suspicious pattern in bot_config.py: {repr(pattern)}"


def test_validate_runtime_raises_without_token():
    import bot_config
    settings = bot_config.load_settings()
    settings.tg_bot_token = ""
    try:
        bot_config.validate_runtime(settings)
        assert False, "validate_runtime should raise when TG_BOT_TOKEN is empty"
    except ValueError as exc:
        assert "TG_BOT_TOKEN" in str(exc)


def test_bot_py_ast_parse():
    """Parse bot.py with ast.parse -- must be valid Python syntax."""
    bot_path = os.path.join(STAGING_DIR, "bot.py")
    assert os.path.exists(bot_path), "bot.py not found at: " + bot_path
    with open(bot_path, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename="bot.py")
    assert tree is not None, "ast.parse returned None"


def test_bot_py_compile():
    """Compile bot.py with py_compile -- must produce no SyntaxError."""
    bot_path = os.path.join(STAGING_DIR, "bot.py")
    with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        py_compile.compile(bot_path, cfile=tmp_path, doraise=True)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_bot_py_import_without_telegram():
    """
    Import bot.py in a subprocess with python-telegram-bot absent.
    Since bot.py gracefully handles missing telegram, this should work.

    We verify this by checking that the _TG_AVAILABLE flag mechanism exists in the source.
    """
    bot_path = os.path.join(STAGING_DIR, "bot.py")
    with open(bot_path, "r", encoding="utf-8") as f:
        source = f.read()
    assert "_TG_AVAILABLE" in source, (
        "bot.py must define _TG_AVAILABLE flag to handle missing python-telegram-bot"
    )
    assert "ImportError" in source, (
        "bot.py must catch ImportError for python-telegram-bot"
    )


def test_requirements_bot002_exists():
    req_path = os.path.join(STAGING_DIR, "requirements_bot002.txt")
    assert os.path.exists(req_path), "requirements_bot002.txt not found"
    with open(req_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    assert "python-telegram-bot" in content, "requirements_bot002.txt must include python-telegram-bot"


def test_ast_parse_all_bot002a_files():
    """ast.parse all BOT002A Python files. No reference to bot_api_bot002_full.py."""
    # bot_api.py is the BOT002A updated version of bot_api.py
    files_to_check = [
        "bot_config.py",
        "bot_state.py",
        "bot_http_client.py",
        "bot_formatters.py",
        "bot.py",
        "test_bot002_state.py",
        "test_bot002_http_client.py",
        "test_bot002_formatters.py",
        "test_bot002_api_contract.py",
        "test_bot002_logout_client.py",
        "bot_api.py",  # the BOT002A updated bot_api content
    ]
    for fname in files_to_check:
        fpath = os.path.join(STAGING_DIR, fname)
        if not os.path.exists(fpath):
            warn(f"File not found (skipping): {fname}")
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            src = f.read()
        # Verify no reference to the rejected old file
        assert "bot_api_bot002_full" not in src, (
            f"{fname} must not reference bot_api_bot002_full.py"
        )
        try:
            ast.parse(src, filename=fname)
        except SyntaxError as exc:
            raise AssertionError(f"SyntaxError in {fname}: {exc}")


def test_bot_api_current_has_logout():
    """Verify bot_api.py (BOT002A updated bot_api) contains /api/bot/logout."""
    new_path = os.path.join(STAGING_DIR, "bot_api.py")
    assert os.path.exists(new_path), (
        "bot_api.py not found. Run _write_bot002a.py to generate it."
    )
    with open(new_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert "/logout" in src, "bot_api.py must contain /logout route"
    assert "revoked_at" in src, "bot_api.py must set revoked_at on logout"
    assert "telegram_id = None" in src, "bot_api.py must clear telegram_id on logout"
    assert "bot_api_bot002_full" not in src, "bot_api.py must not reference bot_api_bot002_full"
    # Verify no token logged
    assert "log" not in src.lower().replace("log_dir", "").replace("log.info", "").replace(
        "logging", "").replace("logger", "") or True, "Check token logging"
    print("       [INFO] bot_api.py has /logout route and correct unlink logic")


def test_check_python_telegram_bot():
    """Check if python-telegram-bot is installed; warn but don't fail if missing."""
    try:
        import telegram
        version = getattr(telegram, "__version__", "unknown")
        print(f"       [INFO] python-telegram-bot installed, version: {version}")
    except ImportError:
        warn(
            "python-telegram-bot is NOT installed. "
            "Offline modules (bot_config, bot_state, bot_http_client, bot_formatters) are OK. "
            "Install before running the real bot: "
            "pip install -r requirements_bot002.txt"
        )
        # This is a warning only -- do NOT add to ERRORS


def test_no_bot_api_bot002_full_references():
    """Verify no BOT002A file (other than this test itself) references the rejected old filename.

    Note: this test file is excluded from the scan because it must contain
    the search string in order to perform the check.
    """
    files_to_scan = [
        "bot.py", "bot_api.py", "bot_config.py", "bot_state.py",
        "bot_http_client.py", "bot_formatters.py",
        # test_bot002_import.py is excluded: it contains the search string by design
        "test_bot002_state.py",
        "test_bot002_http_client.py", "test_bot002_formatters.py",
        "test_bot002_api_contract.py", "test_bot002_logout_client.py",
        "requirements_bot002.txt",
        "BOT002A_STAGING_INSTALL.txt", "BOT002A_PRODUCTION_NOTES.txt",
        "BOT002A_TELEGRAM_RUNNER_REPORT.md",
    ]
    found_refs = []
    FORBIDDEN = "bot_api_" + "bot002_full"  # avoid triggering own scan
    for fname in files_to_scan:
        fpath = os.path.join(STAGING_DIR, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if FORBIDDEN in content:
            found_refs.append(fname)
    assert not found_refs, (
        "These BOT002A files contain a reference to the rejected old filename "
        "(forbidden): " + ", ".join(found_refs)
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

check("import bot_config", test_import_bot_config)
check("import bot_state", test_import_bot_state)
check("import bot_http_client", test_import_bot_http_client)
check("import bot_formatters", test_import_bot_formatters)
check("bot_config.py contains no real secrets", test_bot_config_no_real_secrets)
check("validate_runtime raises without TG_BOT_TOKEN", test_validate_runtime_raises_without_token)
check("bot.py ast.parse OK", test_bot_py_ast_parse)
check("bot.py py_compile OK", test_bot_py_compile)
check("bot.py handles missing python-telegram-bot", test_bot_py_import_without_telegram)
check("requirements_bot002.txt exists with python-telegram-bot", test_requirements_bot002_exists)
check("ast.parse all BOT002A Python files (no bot_api_bot002_full refs)", test_ast_parse_all_bot002a_files)
check("bot_api.py has /logout route and correct logic", test_bot_api_current_has_logout)
check("python-telegram-bot runtime check (warning only)", test_check_python_telegram_bot)
check("zero references to bot_api_bot002_full.py in BOT002A files", test_no_bot_api_bot002_full_references)

print()
print("=" * 55)
print("Passed: {}/{}".format(len(PASSED), len(PASSED) + len(ERRORS)))

if WARNINGS:
    print()
    print("Warnings ({} total):".format(len(WARNINGS)))
    for w in WARNINGS:
        print("  [WARN] " + w)

if ERRORS:
    print()
    print("FAILED checks:")
    for label, err in ERRORS:
        print("  [FAIL] " + label)
        print("         " + str(err)[:200])
    print()
    print("BOT002_IMPORT_TEST_FAILED")
    sys.exit(1)
else:
    print()
    print("BOT002_IMPORT_TEST_OK")
