"""
backup_transport_db.py - SQLite online backup for Vehicle Soft production database.
TASK-DEPLOY-004B: replaces raw file copy with sqlite3.Connection.backup().

Usage:
  python backup_transport_db.py
  python backup_transport_db.py --dest-dir C:\\my-backups\\dir
  python backup_transport_db.py --dest-dir C:\\my-backups\\before_update --suffix before_update

Exit codes: 0 = success, 1 = failure.
stdlib only. No Flask imports. ASCII-only output.
"""

import sys
import os
import sqlite3
import argparse
from datetime import datetime

SOURCE_PATH = r"C:\transport-report\instance\transport.db"
DEFAULT_DEST_DIR = r"C:\transport-report-backups\daily"


def main():
    parser = argparse.ArgumentParser(
        description="SQLite online backup for Vehicle Soft production database."
    )
    parser.add_argument(
        "--dest-dir",
        default=DEFAULT_DEST_DIR,
        help="Destination directory for backup file (default: C:\\transport-report-backups\\daily).",
    )
    parser.add_argument(
        "--suffix",
        default="",
        help="Optional suffix appended after the timestamp in the backup filename.",
    )
    args = parser.parse_args()

    source_path = SOURCE_PATH
    dest_dir = args.dest_dir
    suffix = args.suffix.strip()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if suffix:
        dest_filename = "transport_{}_{}.db".format(timestamp, suffix)
    else:
        dest_filename = "transport_{}.db".format(timestamp)

    dest_path = os.path.join(dest_dir, dest_filename)

    print("=" * 60)
    print(" Transport DB Backup  (SQLite online backup API)")
    print("=" * 60)
    print(" Source : {}".format(source_path))
    print(" Dest   : {}".format(dest_path))
    print()

    if not os.path.isfile(source_path):
        print("ERROR: Source database not found.")
        print("       Expected: {}".format(source_path))
        print("       Check that the instance folder exists.")
        print()
        print("Backup FAILED.")
        sys.exit(1)

    source_size = os.path.getsize(source_path)
    print(" Source size : {:,} bytes".format(source_size))
    print()

    if not os.path.isdir(dest_dir):
        print("Creating destination directory: {}".format(dest_dir))
        try:
            os.makedirs(dest_dir)
            print("Directory created.")
            print()
        except OSError as exc:
            print("ERROR: Cannot create destination directory.")
            print("       {}".format(exc))
            print()
            print("Backup FAILED.")
            sys.exit(1)

    print("Running SQLite online backup...")
    try:
        # [REASON]: sqlite3.Connection.backup() produces a consistent snapshot even
        # when WAL mode is active and the service is running. Raw file copy of .db
        # while WAL has uncheckpointed pages produces an inconsistent backup.
        src_conn = sqlite3.connect(source_path, timeout=30)
        dst_conn = sqlite3.connect(dest_path)
        src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()
    except Exception as exc:
        print("ERROR: Backup operation failed.")
        print("       {}".format(exc))
        print()
        print("Backup FAILED.")
        sys.exit(1)

    if not os.path.isfile(dest_path):
        print("ERROR: Destination file was not created.")
        print("       Expected: {}".format(dest_path))
        print()
        print("Backup FAILED.")
        sys.exit(1)

    dest_size = os.path.getsize(dest_path)
    if dest_size == 0:
        print("ERROR: Destination file is 0 bytes.")
        print("       Something went wrong during the backup.")
        print()
        print("Backup FAILED.")
        sys.exit(1)

    print(" Dest size   : {:,} bytes".format(dest_size))
    print()

    print("Running integrity check on destination database...")
    try:
        check_conn = sqlite3.connect(dest_path, timeout=30)
        cursor = check_conn.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        check_conn.close()
    except Exception as exc:
        print("ERROR: Integrity check raised an exception.")
        print("       {}".format(exc))
        print()
        print("Backup FAILED.")
        sys.exit(1)

    if result is None or result[0].strip().lower() != "ok":
        print("ERROR: Integrity check FAILED.")
        print("       Result: {}".format(result))
        print()
        print("Backup FAILED.")
        sys.exit(1)

    print(" Integrity check : ok")
    print()
    print("SUCCESS: Backup written to:")
    print("         {}".format(dest_path))
    print()
    sys.exit(0)


if __name__ == "__main__":
    main()
