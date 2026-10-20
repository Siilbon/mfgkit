"""Command line: ``python -m mfgkit.eventlog discover CONN_STR [--out FILE]``."""

from __future__ import annotations

import argparse
import json
import sys

from .discover import EVENT_TABLE_HINT, discover


def main(argv=None, discover_fn=discover) -> int:
    parser = argparse.ArgumentParser(prog="python -m mfgkit.eventlog")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("discover", help="list event-like tables and suggest mappings")
    d.add_argument("conn_str", help="ODBC connection string (use a read-only account)")
    d.add_argument("--out", help="write the full result to this JSON file")
    d.add_argument("--pattern", default=EVENT_TABLE_HINT, help="regex on table names")
    d.add_argument("--rows", type=int, default=5, help="sample rows per table")
    args = parser.parse_args(argv)

    found = discover_fn(args.conn_str, pattern=args.pattern, sample_rows=args.rows)
    if not found:
        print("No event-like tables found.")
    for table, info in found.items():
        mapped = (info["suggested_mapping"] or {}).get("columns", {})
        print(f"{table}: {len(info['columns'])} columns; suggested mapping for {sorted(mapped) or 'none'}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(found, f, indent=2, default=str)
        print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
