# Ledger

Private Akahu-backed account, balance, and category spending viewer for ANZ accounts.

Ledger v2 does not depend on Actual Budget. Akahu is the source of truth for accounts,
balances, merchants, categories, and settled transactions.

## Features

- Daily Akahu sync with stable transaction IDs
- ANZ official-open-banking safety check
- Integer-cent money storage
- Account balances and balance snapshots
- Category spending views and transaction filters
- Merchant/category overrides remembered locally
- Review queue for uncategorized or changed transactions
- Weekly Discord reminders when review is needed
- Single-owner password login

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

$env:AKAHU_APP_TOKEN = "..."
$env:AKAHU_USER_TOKEN = "..."
$env:LEDGER_ADMIN_PASSWORD = "choose-a-local-password"
$env:LEDGER_SECRET_KEY = "change-me"
$env:LEDGER_PUBLIC_URL = "https://ledger.emlw.dev/"

ledger init-db
ledger sync --backfill
python app.py
```

The app runs on `http://127.0.0.1:5007/` by default.

## Production

Production is intended to run at `https://ledger.emlw.dev/`.

Use environment variables or a systemd environment file:

- `AKAHU_APP_TOKEN`
- `AKAHU_USER_TOKEN`
- `LEDGER_SECRET_KEY`
- `LEDGER_PASSWORD_HASH` or `LEDGER_ADMIN_PASSWORD`
- `LEDGER_PUBLIC_URL=https://ledger.emlw.dev/`
- `DISCORD_WEBHOOK_URL`
- `LEDGER_DATABASE=/home/opc/finance/ledger/ledger.sqlite3`

The GitHub repo also needs:

- Actions variable `SERVER_HOST`
- Actions secret `SSH_PRIVATE_KEY`
- Optional Actions secrets `AKAHU_APP_TOKEN`, `AKAHU_USER_TOKEN`,
  `DISCORD_WEBHOOK_URL`, `LEDGER_ADMIN_PASSWORD`, or `LEDGER_PASSWORD_HASH`.

Akahu and Discord tokens can also be saved from the in-app Settings page after
signing in. Environment/GitHub secrets remain preferred for production.

Install timers from `deploy/` to sync daily and send weekly review reminders.

## Safety Notes

Ledger only syncs configured connection names, defaulting to `ANZ`, and refuses ANZ
accounts unless Akahu reports `connection.connection_type` as `official`.

Pending transactions are not included in settled spending totals in v1.
