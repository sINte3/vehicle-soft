"""
Configuration for different environments.
Edit this file to switch between SQLite (development) and PostgreSQL (production).
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    # [REASON]: No fallback — production must set SECRET_KEY via environment variable.
    # run_server.py exits early if SECRET_KEY is missing so Flask never starts with None.
    SECRET_KEY = os.environ.get('SECRET_KEY')

    # [REASON]: Topaz agent API token read from environment; if missing, fuel_sync
    # denies all requests (safe default) rather than accepting a hardcoded token.
    FUEL_API_TOKEN = os.environ.get('FUEL_API_TOKEN')

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REPORTS_DIR = os.path.join(BASE_DIR, 'reports')

    # [REASON]: SPARE-STAGE1 — spare part photo storage. Kept as a plain path
    # relative to the app folder (not wired through Flask app context) so the
    # separate Telegram bot process can resolve the same files independently.
    UPLOAD_FOLDER = 'instance/uploads/spare_parts'

    # Session settings
    PERMANENT_SESSION_LIFETIME = 86400  # 24 hours


class DevelopmentConfig(Config):
    """SQLite — для локальной разработки и тестирования."""
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'transport.db')

    # [REASON]: FIX002 - SQLite engine options for multi-process safety.
    # connect_args timeout=30 gives sqlite3 30s before SQLITE_BUSY.
    # pool_size=5 and pool_pre_ping=True help with concurrent access.
    SQLALCHEMY_ENGINE_OPTIONS = {
        'connect_args': {'timeout': 30},
        'pool_size': 5,
        'pool_pre_ping': True,
    }

    # [REASON]: Dev-only fallback so local dev works without setting env vars.
    # This value must never be used on the production server.
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-only-insecure-key-do-not-use-in-production')


class ProductionConfig(Config):
    """PostgreSQL — для сервера."""
    DEBUG = False
    
    # ─── НАСТРОЙКИ POSTGRESQL ───────────────────────────────────────
    # Измените эти значения под ваш сервер:
    PG_HOST = os.environ.get('PG_HOST', 'localhost')
    PG_PORT = os.environ.get('PG_PORT', '5432')
    PG_DB   = os.environ.get('PG_DB',   'transport_report')
    PG_USER = os.environ.get('PG_USER', 'transport_user')
    PG_PASS = os.environ.get('PG_PASS', 'changeme')
    
    SQLALCHEMY_DATABASE_URI = (
        f'postgresql://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{PG_DB}'
    )


class SqliteProductionConfig(Config):
    """SQLite в продакшен-режиме — если PostgreSQL ещё не установлен."""
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'transport.db')
    # [REASON]: FIX002 - SQLite engine options for multi-process safety.
    # connect_args timeout=30 gives sqlite3 30s before SQLITE_BUSY.
    # pool_size=5 and pool_pre_ping=True help with concurrent access.
    SQLALCHEMY_ENGINE_OPTIONS = {
        'connect_args': {'timeout': 30},
        'pool_size': 5,
        'pool_pre_ping': True,
    }


# ─── Выбор конфигурации ─────────────────────────────────────────────
# Переключите на нужную:
#   'dev'        — SQLite + debug
#   'prod'       — PostgreSQL
#   'sqlite_prod' — SQLite без debug (для начала на сервере)

CONFIGS = {
    'dev': DevelopmentConfig,
    'prod': ProductionConfig,
    'sqlite_prod': SqliteProductionConfig,
}

def get_config():
    env = os.environ.get('FLASK_ENV', 'sqlite_prod')
    return CONFIGS.get(env, SqliteProductionConfig)
