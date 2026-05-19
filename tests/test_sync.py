from ledger.db import init_db
from ledger.queries import accounts, review_count, transactions
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

