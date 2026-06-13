import argparse
import os
import sqlite3
import sys

CORRUPTED = "\u0420\u2019\u0420\xb0\u0422\u203a\u0421\u201a\u0420\u0451\u0420\u0405\u0421\u2021\u0420\xb0 \u0420\xb1\u0421\u045b\u0421\u20ac"
CORRECTED = "\u0412\u0430\u049b\u0442\u0438\u043d\u0447\u0430 \u0431\u045e\u0448"
TABLE = "daily_records"

def default_db_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "transport.db")

def connect(db_path):
    if not os.path.isfile(db_path):
        print("ERROR: database not found: {}".format(db_path))
        sys.exit(2)
    return sqlite3.connect(db_path)

def count_rows(cur):
    cur.execute("SELECT COUNT(*) FROM daily_records WHERE idle_reason = ?", (CORRUPTED,))
    return cur.fetchone()[0]

def fetch_rows(cur):
    cur.execute(
        "SELECT id, work_date, equipment_id FROM daily_records WHERE idle_reason = ? ORDER BY id",
        (CORRUPTED,)
    )
    return cur.fetchall()

def report(cur, db_path):
    count = count_rows(cur)
    print("FIX001 idle_reason mojibake report")
    print("=" * 50)
    print("Database: {}".format(db_path))
    print("Table: {}".format(TABLE))
    print("Corrupted value repr: {}".format(repr(CORRUPTED)))
    print("Corrected value repr: {}".format(repr(CORRECTED)))
    print("Rows found: {}".format(count))
    if count:
        print("Rows that would be updated:")
        for row_id, work_date, equipment_id in fetch_rows(cur):
            print("  id={} work_date={} equipment_id={}".format(row_id, work_date, equipment_id))
    print("Mode: report only. No DB changes were made.")
    return count

def apply_fix(cur, con, db_path):
    before = count_rows(cur)
    print("FIX001 idle_reason mojibake apply")
    print("=" * 50)
    print("Database: {}".format(db_path))
    print("Rows before: {}".format(before))
    if before == 0:
        print("Nothing to update.")
        return 0
    cur.execute(
        "UPDATE daily_records SET idle_reason = ? WHERE idle_reason = ?",
        (CORRECTED, CORRUPTED)
    )
    updated = cur.rowcount
    con.commit()
    after = count_rows(cur)
    print("Rows updated: {}".format(updated))
    print("Rows remaining: {}".format(after))
    return updated

def main():
    parser = argparse.ArgumentParser(description="FIX001 data fix for one mojibake idle_reason value.")
    parser.add_argument("--report", action="store_true", help="Report only; do not modify DB.")
    parser.add_argument("--apply", action="store_true", help="Apply the update.")
    parser.add_argument("--db", default=None, help="SQLite DB path. Default: ./instance/transport.db")
    args = parser.parse_args()

    if args.report and args.apply:
        print("ERROR: choose only one mode: --report or --apply")
        sys.exit(2)

    db_path = args.db or default_db_path()
    con = connect(db_path)
    try:
        cur = con.cursor()
        if args.apply:
            apply_fix(cur, con, db_path)
        else:
            report(cur, db_path)
    finally:
        con.close()

if __name__ == "__main__":
    main()
