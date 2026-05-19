from ledger.db import init_db
from ledger.queries import transactions
from ledger.sync import apply_merchant_override, sync_akahu

from .test_sync import FakeAkahu


def test_merchant_override_updates_matching_transactions(tmp_path):
    database = str(tmp_path / "ledger.sqlite3")
    init_db(database)
    sync_akahu(database, FakeAkahu(), allowed_connections=["ANZ"], since_days=None)

    updated = apply_merchant_override(
        database,
        merchant_key="mystery shop",
        display_merchant="Local Market",
        category_name="Groceries",
    )

    mystery = [tx for tx in transactions(database, {}) if tx["id"] == "tx_2"][0]
    assert updated == 1
    assert mystery["effective_merchant"] == "Local Market"
    assert mystery["effective_category"] == "Groceries"
    assert mystery["review_status"] == "reviewed"

