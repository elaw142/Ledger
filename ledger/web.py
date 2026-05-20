from __future__ import annotations

from functools import wraps
from typing import Optional, Type, Union

from flask import Flask, current_app, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .akahu import AkahuClient
from .categories import clean_category_name, clean_color, ensure_category, seed_categories, validate_category_name
from .config import Config
from .db import connection, init_db, utc_now
from .notify import notify_review_needed, send_discord
from .queries import (
    accounts,
    categories,
    latest_sync,
    monthly_spending,
    net_cash_flow,
    review_count,
    spending_by_category,
    total_spend,
    transactions,
)
from .settings import configured_settings, get_runtime_setting, set_setting
from .sync import apply_merchant_override, apply_transaction_override, sync_akahu


def create_app(config_object: Optional[Union[Type[Config], dict]] = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_object:
        if isinstance(config_object, dict):
            app.config.update(config_object)
        else:
            app.config.from_object(config_object)
    if app.config["PUBLIC_URL"].startswith("https://"):
        app.config["SESSION_COOKIE_SECURE"] = True
    init_db(app.config["DATABASE"])
    register_routes(app)
    return app


def _db_path() -> str:
    return current_app.config["DATABASE"]


def _configured_password_ok(password: str) -> bool:
    password_hash = current_app.config.get("PASSWORD_HASH")
    admin_password = current_app.config.get("ADMIN_PASSWORD")
    if password_hash:
        return check_password_hash(password_hash, password)
    return bool(admin_password and password == admin_password)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication_required"}), 401
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def _akahu_client() -> AkahuClient:
    return AkahuClient(
        current_app.config["AKAHU_BASE_URL"],
        get_runtime_setting(_db_path(), current_app.config, "AKAHU_APP_TOKEN"),
        get_runtime_setting(_db_path(), current_app.config, "AKAHU_USER_TOKEN"),
    )


def _category_row(con, name: str):
    return con.execute(
        "SELECT * FROM categories WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()


def _require_category_name(name: Optional[str]) -> str:
    category_name = clean_category_name(name)
    if not category_name:
        return ""
    with connection(_db_path()) as con:
        seed_categories(con)
        row = _category_row(con, category_name)
        if not row:
            raise ValueError(f"Unknown category: {category_name}")
        return row["name"]


def _category_usage(con, name: str) -> int:
    counts = [
        con.execute(
            "SELECT COUNT(*) AS count FROM transactions WHERE effective_category = ? COLLATE NOCASE",
            (name,),
        ).fetchone()["count"],
        con.execute(
            "SELECT COUNT(*) AS count FROM merchant_overrides WHERE category_name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()["count"],
        con.execute(
            "SELECT COUNT(*) AS count FROM transaction_overrides WHERE category_name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()["count"],
    ]
    return int(sum(counts))


def _category_payload(row) -> dict:
    return {
        "name": row["name"],
        "color": row["color"],
        "sort_order": row["sort_order"],
        "is_system": bool(row["is_system"]),
    }


def _category_error(exc: ValueError):
    return jsonify({"error": str(exc)}), 400


def register_routes(app: Flask) -> None:
    @app.get("/login")
    def login():
        if session.get("authenticated"):
            return redirect(url_for("home"))
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        password = request.form.get("password", "")
        if _configured_password_ok(password):
            session.clear()
            session["authenticated"] = True
            return redirect(request.args.get("next") or url_for("home"))
        return render_template("login.html", error="Invalid password"), 401

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @app.get("/transactions")
    @app.get("/categories")
    @app.get("/review")
    @app.get("/settings")
    @login_required
    def home():
        return render_template("app.html", public_url=current_app.config["PUBLIC_URL"])

    @app.get("/api/summary")
    @login_required
    def api_summary():
        start = request.args.get("start")
        end = request.args.get("end")
        return jsonify(
            {
                "accounts": accounts(_db_path()),
                "latest_sync": latest_sync(_db_path()),
                "review_count": review_count(_db_path()),
                "selected_period_spend_cents": total_spend(_db_path(), start=start, end=end),
                "selected_period_cash_flow_cents": net_cash_flow(_db_path(), start=start, end=end),
                "this_month_spend_cents": total_spend(_db_path()),
                "monthly_spending": monthly_spending(_db_path()),
                "spending_by_category": spending_by_category(_db_path(), start=start, end=end),
            }
        )

    @app.get("/api/accounts")
    @login_required
    def api_accounts():
        return jsonify({"accounts": accounts(_db_path())})

    @app.get("/api/transactions")
    @login_required
    def api_transactions():
        filters = {
            "account": request.args.get("account") or None,
            "category": request.args.get("category") or None,
            "start": request.args.get("start") or None,
            "end": request.args.get("end") or None,
            "review_status": request.args.get("review_status") or None,
        }
        return jsonify({"transactions": transactions(_db_path(), filters)})

    @app.get("/api/categories")
    @login_required
    def api_categories():
        return jsonify(
            {
                "categories": categories(
                    _db_path(),
                    start=request.args.get("start"),
                    end=request.args.get("end"),
                )
            }
        )

    @app.post("/api/categories")
    @login_required
    def api_categories_create():
        body = request.get_json(force=True)
        try:
            name = validate_category_name(body.get("name"))
        except ValueError as exc:
            return _category_error(exc)
        with connection(_db_path()) as con:
            seed_categories(con)
            if _category_row(con, name):
                return jsonify({"error": "Category already exists"}), 409
            stored_name = ensure_category(con, name, color=clean_color(body.get("color"), name))
            row = _category_row(con, stored_name)
            return jsonify({"category": _category_payload(row)}), 201

    @app.patch("/api/categories/<path:category_name>")
    @login_required
    def api_categories_update(category_name):
        body = request.get_json(force=True)
        with connection(_db_path()) as con:
            seed_categories(con)
            existing = _category_row(con, category_name)
            if not existing:
                return jsonify({"error": "Category not found"}), 404
            new_name = clean_category_name(body.get("name") or existing["name"])
            try:
                new_name = validate_category_name(new_name)
            except ValueError as exc:
                return _category_error(exc)
            if existing["is_system"] and new_name.lower() != existing["name"].lower():
                return jsonify({"error": "System categories cannot be renamed"}), 400
            duplicate = _category_row(con, new_name)
            if duplicate and duplicate["name"].lower() != existing["name"].lower():
                return jsonify({"error": "Category already exists"}), 409
            color = clean_color(body.get("color") or existing["color"], new_name)
            now = utc_now()
            con.execute(
                "UPDATE categories SET name = ?, color = ?, updated_at = ? WHERE name = ? COLLATE NOCASE",
                (new_name, color, now, existing["name"]),
            )
            if new_name.lower() != existing["name"].lower():
                for table in ("transactions", "merchant_overrides", "transaction_overrides"):
                    column = "effective_category" if table == "transactions" else "category_name"
                    con.execute(
                        f"UPDATE {table} SET {column} = ? WHERE {column} = ? COLLATE NOCASE",
                        (new_name, existing["name"]),
                    )
            return jsonify({"category": _category_payload(_category_row(con, new_name))})

    @app.delete("/api/categories/<path:category_name>")
    @login_required
    def api_categories_delete(category_name):
        body = request.get_json(silent=True) or {}
        replacement = clean_category_name(body.get("replacement_category"))
        with connection(_db_path()) as con:
            seed_categories(con)
            existing = _category_row(con, category_name)
            if not existing:
                return jsonify({"error": "Category not found"}), 404
            if existing["is_system"]:
                return jsonify({"error": "System categories cannot be removed"}), 400
            usage = _category_usage(con, existing["name"])
            if usage and not replacement:
                return jsonify({"error": "Replacement category required", "requires_replacement": True, "usage_count": usage}), 400
            if replacement:
                replacement_row = _category_row(con, replacement)
                if not replacement_row:
                    return jsonify({"error": "Replacement category not found"}), 400
                if replacement_row["name"].lower() == existing["name"].lower():
                    return jsonify({"error": "Replacement category must be different"}), 400
                now = utc_now()
                con.execute(
                    """
                    INSERT INTO transaction_overrides (
                        transaction_id, display_merchant, category_name, category_id, updated_at
                    )
                    SELECT id, effective_merchant, ?, NULL, ?
                    FROM transactions
                    WHERE effective_category = ? COLLATE NOCASE
                    ON CONFLICT(transaction_id) DO UPDATE SET
                        category_name = excluded.category_name,
                        updated_at = excluded.updated_at
                    """,
                    (replacement_row["name"], now, existing["name"]),
                )
                con.execute(
                    """
                    UPDATE transactions
                    SET effective_category = ?,
                        review_status = 'reviewed',
                        review_reason = NULL,
                        updated_at = ?
                    WHERE effective_category = ? COLLATE NOCASE
                    """,
                    (replacement_row["name"], now, existing["name"]),
                )
                con.execute(
                    "UPDATE merchant_overrides SET category_name = ?, updated_at = ? WHERE category_name = ? COLLATE NOCASE",
                    (replacement_row["name"], now, existing["name"]),
                )
                con.execute(
                    "UPDATE transaction_overrides SET category_name = ?, updated_at = ? WHERE category_name = ? COLLATE NOCASE",
                    (replacement_row["name"], now, existing["name"]),
                )
            con.execute("DELETE FROM categories WHERE name = ? COLLATE NOCASE", (existing["name"],))
            return jsonify({"status": "deleted", "usage_count": usage})

    @app.get("/api/analytics/spending")
    @login_required
    def api_analytics_spending():
        start = request.args.get("start")
        end = request.args.get("end")
        interval = request.args.get("interval") or "month"
        if interval != "month":
            return jsonify({"error": "Only month interval is supported"}), 400
        return jsonify(
            {
                "total_spend_cents": total_spend(_db_path(), start=start, end=end),
                "monthly_spending": monthly_spending(_db_path(), start=start, end=end),
                "spending_by_category": spending_by_category(_db_path(), start=start, end=end),
            }
        )

    @app.post("/api/sync")
    @login_required
    def api_sync():
        body = request.get_json(silent=True) or {}
        if not get_runtime_setting(_db_path(), current_app.config, "AKAHU_APP_TOKEN"):
            return jsonify({"error": "Akahu App ID token is not configured"}), 400
        if not get_runtime_setting(_db_path(), current_app.config, "AKAHU_USER_TOKEN"):
            return jsonify({"error": "Akahu User Access token is not configured"}), 400
        backfill = bool(body.get("backfill"))
        since_days = None if backfill else current_app.config["SYNC_LOOKBACK_DAYS"]
        result = sync_akahu(
            _db_path(),
            _akahu_client(),
            allowed_connections=current_app.config["ALLOWED_CONNECTIONS"],
            since_days=since_days,
        )
        status_code = 200 if result.status == "success" else 502
        return jsonify(result.as_dict()), status_code

    @app.get("/api/sync/status")
    @login_required
    def api_sync_status():
        return jsonify({"latest_sync": latest_sync(_db_path())})

    @app.get("/api/settings")
    @login_required
    def api_settings():
        return jsonify(configured_settings(_db_path(), current_app.config))

    @app.post("/api/settings")
    @login_required
    def api_settings_post():
        body = request.get_json(force=True)
        for key in ("AKAHU_APP_TOKEN", "AKAHU_USER_TOKEN", "DISCORD_WEBHOOK_URL"):
            if key in body:
                set_setting(_db_path(), key, body.get(key))
        return jsonify(configured_settings(_db_path(), current_app.config))

    @app.post("/api/transactions/<transaction_id>/override")
    @login_required
    def api_transaction_override(transaction_id):
        body = request.get_json(force=True)
        try:
            category_name = _require_category_name(body.get("category_name"))
        except ValueError as exc:
            return _category_error(exc)
        apply_transaction_override(
            _db_path(),
            transaction_id=transaction_id,
            display_merchant=body.get("display_merchant"),
            category_name=category_name,
            category_id=body.get("category_id"),
        )
        if body.get("apply_to_merchant") and body.get("merchant_key"):
            apply_merchant_override(
                _db_path(),
                merchant_key=body["merchant_key"],
                display_merchant=body.get("display_merchant"),
                category_name=category_name,
                category_id=body.get("category_id"),
            )
        return jsonify({"status": "ok"})

    @app.post("/api/merchant-overrides")
    @login_required
    def api_merchant_overrides():
        body = request.get_json(force=True)
        try:
            category_name = _require_category_name(body.get("category_name"))
        except ValueError as exc:
            return _category_error(exc)
        updated = apply_merchant_override(
            _db_path(),
            merchant_key=body["merchant_key"],
            display_merchant=body.get("display_merchant"),
            category_name=category_name,
            category_id=body.get("category_id"),
        )
        return jsonify({"status": "ok", "transactions_updated": updated})

    @app.post("/api/notifications/test")
    @login_required
    def api_notification_test():
        result = send_discord(
            _db_path(),
            get_runtime_setting(_db_path(), current_app.config, "DISCORD_WEBHOOK_URL"),
            {"content": f"Ledger notification test from {current_app.config['PUBLIC_URL']}"},
        )
        status_code = 200 if result["status"] in {"sent", "skipped"} else 502
        return jsonify(result), status_code

    @app.post("/api/notifications/review-needed")
    @login_required
    def api_notification_review_needed():
        result = notify_review_needed(
            _db_path(),
            get_runtime_setting(_db_path(), current_app.config, "DISCORD_WEBHOOK_URL"),
            current_app.config["PUBLIC_URL"],
        )
        status_code = 200 if result["status"] in {"sent", "skipped"} else 502
        return jsonify(result), status_code
