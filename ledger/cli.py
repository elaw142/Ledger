from __future__ import annotations

import argparse
import sys
from typing import Optional

from werkzeug.security import generate_password_hash

from .akahu import AkahuClient
from .config import Config
from .db import init_db
from .notify import notify_review_needed
from .sync import sync_akahu
from .web import create_app


def _client(config: Config) -> AkahuClient:
    return AkahuClient(config.AKAHU_BASE_URL, config.AKAHU_APP_TOKEN, config.AKAHU_USER_TOKEN)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create or upgrade the SQLite database")

    sync_parser = sub.add_parser("sync", help="Sync Akahu accounts, balances, and settled transactions")
    sync_parser.add_argument("--backfill", action="store_true", help="Fetch all Akahu-accessible history")

    sub.add_parser("notify-review-needed", help="Send a Discord reminder when transactions need review")

    hash_parser = sub.add_parser("hash-password", help="Generate a LEDGER_PASSWORD_HASH value")
    hash_parser.add_argument("password")

    run_parser = sub.add_parser("runserver", help="Run the local Flask development server")
    run_parser.add_argument("--host", default="127.0.0.1")
    run_parser.add_argument("--port", default=5007, type=int)

    args = parser.parse_args(argv)
    config = Config()

    if args.command == "init-db":
        init_db(config.DATABASE)
        print(f"Initialized {config.DATABASE}")
        return 0

    if args.command == "hash-password":
        print(generate_password_hash(args.password))
        return 0

    if args.command == "sync":
        init_db(config.DATABASE)
        since_days = None if args.backfill else config.SYNC_LOOKBACK_DAYS
        result = sync_akahu(
            config.DATABASE,
            _client(config),
            allowed_connections=config.ALLOWED_CONNECTIONS,
            since_days=since_days,
        )
        print(result.as_dict())
        return 0 if result.status == "success" else 1

    if args.command == "notify-review-needed":
        init_db(config.DATABASE)
        result = notify_review_needed(config.DATABASE, config.DISCORD_WEBHOOK_URL, config.PUBLIC_URL)
        print(result)
        return 0 if result["status"] in {"sent", "skipped"} else 1

    if args.command == "runserver":
        app = create_app()
        app.run(host=args.host, port=args.port)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
