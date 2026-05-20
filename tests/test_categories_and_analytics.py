from datetime import date, timedelta

from ledger.db import connection
from ledger.web import create_app


def logged_in_client(tmp_path):
    database = str(tmp_path / "ledger.sqlite3")
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": database,
            "ADMIN_PASSWORD": "secret",
            "PASSWORD_HASH": "",
            "PUBLIC_URL": "http://ledger.test/",
        }
    )
    client = app.test_client()
    client.post("/login", data={"password": "secret"})
    return client, database


def insert_account(con):
    con.execute(
        """
        INSERT INTO accounts (id, name, raw_json, updated_at)
        VALUES ('acc', 'Everyday', '{}', 'now')
        """
    )


def insert_transaction(con, *, tx_id, tx_date, amount_cents, category):
    con.execute(
        """
        INSERT INTO transactions (
            id, account_id, date, description, amount_cents, merchant_key,
            effective_merchant, effective_category, review_status, raw_json,
            first_seen_at, updated_at
        )
        VALUES (?, 'acc', ?, ?, ?, ?, ?, ?, 'auto', '{}', 'now', 'now')
        """,
        (tx_id, tx_date, tx_id, amount_cents, tx_id, tx_id, category),
    )


def test_category_crud_and_reassignment(tmp_path):
    client, database = logged_in_client(tmp_path)
    with connection(database) as con:
        insert_account(con)
        insert_transaction(
            con,
            tx_id="tx_food",
            tx_date=date.today().isoformat(),
            amount_cents=-1200,
            category="Dining",
        )

    categories = client.get("/api/categories").get_json()["categories"]
    assert any(category["name"] == "Dining" for category in categories)
    assert any(category["name"] == "Transfers" and category["is_system"] for category in categories)

    created = client.post("/api/categories", json={"name": "Household", "color": "#2f855a"})
    assert created.status_code == 201
    assert client.post("/api/categories", json={"name": "household"}).status_code == 409

    assert client.delete("/api/categories/Transfers").status_code == 400
    needs_replacement = client.delete("/api/categories/Dining").get_json()
    assert needs_replacement["requires_replacement"] is True

    deleted = client.delete("/api/categories/Dining", json={"replacement_category": "Household"})
    assert deleted.status_code == 200
    with connection(database) as con:
        tx = con.execute("SELECT effective_category FROM transactions WHERE id = 'tx_food'").fetchone()
        override = con.execute(
            "SELECT category_name FROM transaction_overrides WHERE transaction_id = 'tx_food'"
        ).fetchone()
    assert tx["effective_category"] == "Household"
    assert override["category_name"] == "Household"


def test_override_api_rejects_unknown_categories(tmp_path):
    client, database = logged_in_client(tmp_path)
    with connection(database) as con:
        insert_account(con)
        insert_transaction(
            con,
            tx_id="tx_review",
            tx_date=date.today().isoformat(),
            amount_cents=-1000,
            category=None,
        )

    rejected = client.post(
        "/api/transactions/tx_review/override",
        json={"display_merchant": "Shop", "category_name": "Not Real"},
    )
    assert rejected.status_code == 400

    client.post("/api/categories", json={"name": "Groceries"})
    accepted = client.post(
        "/api/transactions/tx_review/override",
        json={"display_merchant": "Shop", "category_name": "Groceries"},
    )
    assert accepted.status_code == 200


def test_summary_and_analytics_exclude_income_and_transfers(tmp_path):
    client, database = logged_in_client(tmp_path)
    today = date.today()
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    with connection(database) as con:
        insert_account(con)
        insert_transaction(
            con,
            tx_id="tx_grocery",
            tx_date=today.isoformat(),
            amount_cents=-4200,
            category="Groceries",
        )
        insert_transaction(
            con,
            tx_id="tx_transfer",
            tx_date=today.isoformat(),
            amount_cents=-10000,
            category="Transfers",
        )
        insert_transaction(
            con,
            tx_id="tx_salary",
            tx_date=today.isoformat(),
            amount_cents=250000,
            category="Income",
        )
        insert_transaction(
            con,
            tx_id="tx_old",
            tx_date=last_month.isoformat(),
            amount_cents=-3000,
            category="Groceries",
        )

    summary = client.get("/api/summary").get_json()
    assert summary["this_month_spend_cents"] == 4200
    assert summary["selected_period_spend_cents"] == 4200
    assert summary["spending_by_category"][0]["category"] == "Groceries"
    assert summary["spending_by_category"][0]["spend_cents"] == 4200

    analytics = client.get("/api/analytics/spending").get_json()
    months = {row["month"]: row["spend_cents"] for row in analytics["monthly_spending"]}
    assert months[today.strftime("%Y-%m")] == 4200
    assert months[last_month.strftime("%Y-%m")] == 3000
