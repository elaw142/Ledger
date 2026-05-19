from __future__ import annotations

from functools import wraps
from typing import Optional, Type, Union

from flask import Flask, current_app, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .akahu import AkahuClient
from .config import Config
from .db import init_db
from .notify import notify_review_needed, send_discord
from .queries import accounts, categories, latest_sync, review_count, spending_by_category, transactions
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
        current_app.config["AKAHU_APP_TOKEN"],
        current_app.config["AKAHU_USER_TOKEN"],
    )


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
        return jsonify(
            {
                "accounts": accounts(_db_path()),
                "latest_sync": latest_sync(_db_path()),
                "review_count": review_count(_db_path()),
                "spending_by_category": spending_by_category(
                    _db_path(),
                    start=request.args.get("start"),
                    end=request.args.get("end"),
                ),
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
        return jsonify({"categories": categories(_db_path())})

    @app.post("/api/sync")
    @login_required
    def api_sync():
        body = request.get_json(silent=True) or {}
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

    @app.post("/api/transactions/<transaction_id>/override")
    @login_required
    def api_transaction_override(transaction_id):
        body = request.get_json(force=True)
        apply_transaction_override(
            _db_path(),
            transaction_id=transaction_id,
            display_merchant=body.get("display_merchant"),
            category_name=body.get("category_name"),
            category_id=body.get("category_id"),
        )
        if body.get("apply_to_merchant") and body.get("merchant_key"):
            apply_merchant_override(
                _db_path(),
                merchant_key=body["merchant_key"],
                display_merchant=body.get("display_merchant"),
                category_name=body.get("category_name"),
                category_id=body.get("category_id"),
            )
        return jsonify({"status": "ok"})

    @app.post("/api/merchant-overrides")
    @login_required
    def api_merchant_overrides():
        body = request.get_json(force=True)
        updated = apply_merchant_override(
            _db_path(),
            merchant_key=body["merchant_key"],
            display_merchant=body.get("display_merchant"),
            category_name=body.get("category_name"),
            category_id=body.get("category_id"),
        )
        return jsonify({"status": "ok", "transactions_updated": updated})

    @app.post("/api/notifications/test")
    @login_required
    def api_notification_test():
        result = send_discord(
            _db_path(),
            current_app.config["DISCORD_WEBHOOK_URL"],
            {"content": f"Ledger notification test from {current_app.config['PUBLIC_URL']}"},
        )
        status_code = 200 if result["status"] in {"sent", "skipped"} else 502
        return jsonify(result), status_code

    @app.post("/api/notifications/review-needed")
    @login_required
    def api_notification_review_needed():
        result = notify_review_needed(
            _db_path(),
            current_app.config["DISCORD_WEBHOOK_URL"],
            current_app.config["PUBLIC_URL"],
        )
        status_code = 200 if result["status"] in {"sent", "skipped"} else 502
        return jsonify(result), status_code
