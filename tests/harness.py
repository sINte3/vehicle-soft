# -*- coding: utf-8 -*-
"""Shared disposable-SQLite test harness (RE-SP P0 release blockers).

Builds the REAL application via app.create_app(), but pointed at a
throwaway temp directory: a disposable SQLite file for the database and a
disposable uploads folder for attachments. No test in this suite ever
touches instance/transport.db, the real uploads directory, or any staging
or production data.

Run the whole suite from the project root with:

  python -m unittest discover -s tests -v

[REASON]: config is patched BEFORE the first `import app`, because app.py
builds its module-level singleton at import time; patching later would leave
that singleton bound to the real instance/ paths.
"""
import os
import sys
import atexit
import shutil
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

_TMP = tempfile.mkdtemp(prefix='vehicle_soft_tests_')
atexit.register(shutil.rmtree, _TMP, True)

os.environ['FLASK_ENV'] = 'dev'
os.environ.setdefault('SECRET_KEY', 'test-only-secret')

import config  # noqa: E402

TEST_DB_PATH = os.path.join(_TMP, 'test.db')
TEST_UPLOAD_DIR = os.path.join(_TMP, 'uploads')
config.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + TEST_DB_PATH
config.DevelopmentConfig.UPLOAD_FOLDER = TEST_UPLOAD_DIR

from app import app  # noqa: E402
from models import db, User, Organization, ROLE_ADMIN  # noqa: E402

app.config['TESTING'] = True

CSRF = 'test-csrf-token'


def reset_db():
    """Drop and recreate every table in the disposable test database."""
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()


def create_admin(username='admin'):
    """Create an admin user (bypasses module permission rows) and return its id."""
    with app.app_context():
        user = User(username=username, role=ROLE_ADMIN, full_name='Test Admin')
        user.set_password('test-password')
        db.session.add(user)
        db.session.commit()
        return user.id


def create_org(name='Test Org'):
    with app.app_context():
        org = Organization(name=name)
        db.session.add(org)
        db.session.commit()
        return org.id


def login(client, user_id):
    """Log a user in by writing the flask-login session keys directly, and
    plant a known CSRF token so POSTs can pass enforce_csrf_protection."""
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True
        sess['_csrf_token'] = CSRF
