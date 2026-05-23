"""
Production server - runs with Waitress (Windows-compatible WSGI server).
Usage: python run_server.py

IMPORTANT: This file must stay ASCII-only. Cyrillic characters in print()
will crash on Windows Server 2012 R2 due to cp1251 codec limitations
when output is redirected to a log file by NSSM.
"""

import os
import sys

# Set environment before importing app
os.environ.setdefault('FLASK_ENV', 'sqlite_prod')
os.environ['PYTHONIOENCODING'] = 'utf-8'

# [REASON]: Fail fast before importing the app so NSSM logs show a clear cause.
# Flask sessions are broken if SECRET_KEY is None; better to exit explicitly here.
if not os.environ.get('SECRET_KEY'):
    print('ERROR: SECRET_KEY environment variable is not set.')
    print('Set it once with:')
    print('  setx SECRET_KEY "your-strong-random-key" /M')
    print('Then restart the TransportReport service.')
    print('See docs/DEPLOYMENT_SECURITY.md for full instructions.')
    sys.exit(1)

from waitress import serve
from app import app

HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', '5050'))

if __name__ == '__main__':
    print('=' * 50)
    print('  Bukhoro Agrocluster - Transport Report')
    print('=' * 50)
    print('  Server: http://{}:{}'.format(HOST, PORT))
    print('  Mode:   {}'.format('PostgreSQL' if 'postgresql' in app.config['SQLALCHEMY_DATABASE_URI'] else 'SQLite'))
    print('  Stop:   Ctrl+C')
    print('=' * 50)

    serve(app, host=HOST, port=PORT, threads=4,
          url_scheme='http',
          channel_timeout=120,
          ident='TransportReport')
