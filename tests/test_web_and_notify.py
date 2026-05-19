from ledger.db import connection, init_db
from ledger.notify import notify_review_needed
from ledger.web import create_app


def test_auth_protects_api(tmp_path):
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
    assert client.get("/api/summary").status_code == 401
    assert client.post("/login", data={"password": "secret"}).status_code == 302
    assert client.get("/api/summary").status_code == 200


def test_notify_skips_when_review_queue_empty(tmp_path):
    database = str(tmp_path / "ledger.sqlite3")
    init_db(database)
    result = notify_review_needed(database, "https://discord.invalid/webhook", "https://ledger.emlw.dev/")
    assert result["status"] == "skipped"


def test_notify_sends_when_review_needed(tmp_path, monkeypatch):
    database = str(tmp_path / "ledger.sqlite3")
    init_db(database)
    with connection(database) as con:
        con.execute(
            """
            INSERT INTO accounts (
                id, name, raw_json, updated_at
            ) VALUES ('acc', 'Everyday', '{}', 'now')
            """
        )
        con.execute(
            """
            INSERT INTO transactions (
                id, account_id, date, description, amount_cents, merchant_key,
                effective_merchant, review_status, raw_json, first_seen_at, updated_at
            )
            VALUES ('tx', 'acc', '2026-05-01', 'Mystery', -1000, 'mystery',
                    'Mystery', 'needs_review', '{}', 'now', 'now')
            """
        )

    calls = []

    class Response:
        status_code = 204
        text = ""

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return Response()

    monkeypatch.setattr("ledger.notify.requests.post", fake_post)
    result = notify_review_needed(database, "https://discord.example/webhook", "https://ledger.emlw.dev/")

    assert result["status"] == "sent"
    assert result["review_count"] == 1
    assert calls[0][1]["content"].startswith("Ledger has 1 transaction")

