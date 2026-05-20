from ledger.db import init_db
from ledger.queries import accounts, review_count, spending_by_category, transactions
from ledger.sync import sync_akahu


class FakeAkahu:
    def __init__(self):
        self.transaction_pages = [
            {
                "_id": "tx_1",
                "date": "2026-05-01",
                "description": "Countdown Auckland",
                "amount": "-42.30",
                "balance": "100.00",
                "merchant": {"_id": "m_1", "name": "Countdown"},
                "category": {"_id": "c_1", "name": "Groceries"},
            },
            {
                "_id": "tx_2",
                "date": "2026-05-02",
                "description": "Mystery",
                "amount": "-10.00",
                "merchant": {"_id": "m_2", "name": "Mystery Shop"},
                "category": None,
            },
        ]

    def accounts(self):
        return [
            {
                "_id": "acc_1",
                "name": "Everyday",
                "currency": "NZD",
                "balance": {"current": "57.70", "available": "57.70"},
                "attributes": ["TRANSACTIONS"],
                "connection": {"name": "ANZ", "connection_type": "official"},
            }
        ]

    def transactions(self, account_id, since_days=None):
        assert account_id == "acc_1"
        return list(self.transaction_pages)


def test_sync_backfill_is_idempotent(tmp_path):
    database = str(tmp_path / "ledger.sqlite3")
    init_db(database)
    client = FakeAkahu()

    first = sync_akahu(database, client, allowed_connections=["ANZ"], since_days=None)
    second = sync_akahu(database, client, allowed_connections=["ANZ"], since_days=None)

    assert first.status == "success"
    assert first.new_transactions == 2
    assert second.status == "success"
    assert second.new_transactions == 0
    assert second.updated_transactions == 2
    assert len(accounts(database)) == 1
    assert len(transactions(database, {})) == 2
    assert review_count(database) == 1


def test_sync_skips_accounts_without_transaction_attribute(tmp_path):
    class NoTransactionAkahu(FakeAkahu):
        def accounts(self):
            account = super().accounts()[0]
            account["attributes"] = ["PAYMENT_TO"]
            return [account]

        def transactions(self, account_id, since_days=None):
            raise AssertionError("transactions should not be requested")

    database = str(tmp_path / "ledger.sqlite3")
    init_db(database)
    result = sync_akahu(database, NoTransactionAkahu(), allowed_connections=["ANZ"], since_days=None)

    assert result.status == "success"
    assert result.accounts_seen == 1
    assert result.transactions_seen == 0


def test_sync_uses_personal_finance_category_group(tmp_path):
    database = str(tmp_path / "ledger.sqlite3")
    init_db(database)
    client = FakeAkahu()
    client.transaction_pages[0]["category"]["groups"] = {
        "personal_finance": {"_id": "group_food", "name": "Food"}
    }

    sync_akahu(database, client, allowed_connections=["ANZ"], since_days=None)

    tx = [row for row in transactions(database, {}) if row["id"] == "tx_1"][0]
    assert tx["effective_category"] == "Food"


def test_transfer_type_is_auto_categorized_and_excluded_from_spending(tmp_path):
    database = str(tmp_path / "ledger.sqlite3")
    init_db(database)
    client = FakeAkahu()
    client.transaction_pages = [
        {
            "_id": "tx_transfer",
            "date": "2026-05-03",
            "description": "Transfer to savings",
            "amount": "-100.00",
            "type": "TRANSFER",
            "category": None,
        },
        {
            "_id": "tx_shop",
            "date": "2026-05-04",
            "description": "Groceries",
            "amount": "-50.00",
            "type": "EFTPOS",
            "category": {"_id": "c_1", "name": "Groceries"},
        },
    ]

    sync_akahu(database, client, allowed_connections=["ANZ"], since_days=None)

    rows = {row["id"]: row for row in transactions(database, {})}
    spending = spending_by_category(database, start="2026-05-01", end="2026-05-31")
    assert rows["tx_transfer"]["effective_category"] == "Transfers"
    assert rows["tx_transfer"]["review_status"] == "auto"
    assert spending[0]["category"] == "Groceries"
    assert spending[0]["spend_cents"] == 5000
    assert spending[0]["count"] == 1


def test_matching_internal_movements_are_auto_categorized_as_transfers(tmp_path):
    class TransferPairAkahu(FakeAkahu):
        def accounts(self):
            account_1 = super().accounts()[0]
            account_2 = dict(account_1, _id="acc_2", name="Savings")
            return [account_1, account_2]

        def transactions(self, account_id, since_days=None):
            if account_id == "acc_1":
                return [
                    {
                        "_id": "tx_out",
                        "date": "2026-05-05",
                        "description": "Payment to savings",
                        "amount": "-250.00",
                        "type": "PAYMENT",
                        "category": None,
                    }
                ]
            return [
                {
                    "_id": "tx_in",
                    "date": "2026-05-06",
                    "description": "Transfer from everyday",
                    "amount": "250.00",
                    "type": "DIRECT CREDIT",
                    "category": None,
                }
            ]

    database = str(tmp_path / "ledger.sqlite3")
    init_db(database)
    sync_akahu(database, TransferPairAkahu(), allowed_connections=["ANZ"], since_days=None)

    rows = {row["id"]: row for row in transactions(database, {})}
    assert rows["tx_out"]["effective_category"] == "Transfers"
    assert rows["tx_in"]["effective_category"] == "Transfers"
