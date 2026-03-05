#!/usr/bin/env python3
"""
ANZ Finance Importer — Web Frontend Backend (multi-user, multi-file)
Run: python3 app.py
"""

from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import csv, json, os, re, subprocess, threading, uuid, queue, time
import requests as http_requests
from dateutil import parser as dateparser
from io import StringIO

app = Flask(__name__)
CORS(app)

# ─── PATHS ───────────────────────────────────────────────────────────────────
BASE_DIR          = "/home/opc/finance"
SCRIPTS_DIR       = os.path.join(BASE_DIR, "scripts")
PROFILES_FILE     = os.path.join(BASE_DIR, "importer", "profiles.json")
TRANSACTIONS_FILE = os.path.join(SCRIPTS_DIR, "categorized_transactions.json")

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL    = "llama3.1:8b"

CATEGORY_PREFERENCES = """
My budget categories are:
- Groceries (supermarkets: Countdown, New World, Pak'nSave, Woolworths, Four Square, Costco)
- Eating Out (cafes, restaurants, fast food, takeaways, UberEats, DoorDash, CJ)
- Transport (petrol, parking, Uber, taxi, AT HOP, public transport)
- Utilities (power, internet, water, gas — Vector, Genesis, Contact, Spark, Vodafone, 2degrees, One NZ)
- Rent (rent payments, $165 to Olivia Johnston)
- Health (pharmacy, doctor, dentist, optometrist)
- Shopping (clothing, electronics, department stores — Farmers, Kmart, The Warehouse)
- Entertainment (movies, events, gaming, streaming — Netflix, Spotify, Disney+, Blizzard, Google, Youtube, Paddle.net)
- Personal Care (haircut, gym, beauty)
- Insurance (car insurance, house insurance, health insurance)
- Transfers (transfers between accounts, payments to savings)
- IOUs (transfers/payments to an individual)
- Income (salary, wages, government payments)
- Savings (transfers into savings/Kiwisaver)
- Shared Flat Savings ($4 transfers to Olivia Johnston)
- Uncategorized (if you genuinely cannot determine the category)
"""

CATEGORIES = [
    "Groceries", "Eating Out", "Transport", "Utilities", "Rent",
    "Health", "Shopping", "Entertainment", "Personal Care", "Insurance",
    "Transfers", "IOUs", "Income", "Savings", "Shared Flat Savings", "Uncategorized"
]

# ─── IN-MEMORY JOB STATE ─────────────────────────────────────────────────────
jobs = {}

# ─── PROFILE HELPERS ─────────────────────────────────────────────────────────

def load_profiles():
    if os.path.exists(PROFILES_FILE):
        with open(PROFILES_FILE) as f:
            return json.load(f)
    return {}

def save_profiles(profiles):
    with open(PROFILES_FILE, "w") as f:
        json.dump(profiles, f, indent=2)

def get_corrections_path(profile_id):
    return os.path.join(BASE_DIR, "importer", f"corrections_{profile_id}.json")

def load_corrections(profile_id):
    path = get_corrections_path(profile_id)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def save_corrections_for_profile(profile_id, corrections):
    with open(get_corrections_path(profile_id), "w") as f:
        json.dump(corrections, f, indent=2)

# ─── CSV / CATEGORIZATION HELPERS ────────────────────────────────────────────

def extract_merchant(description):
    parts = [p.strip() for p in description.split("|")]
    if re.match(r'^\d{4}[-\s]+[\*\d]{4}[-\s]+[\*\d]{4}[-\s]+[\*\d]{4}', parts[0]):
        idx = 1
        if idx < len(parts) and re.match(r'^\d+(\.\d+)?$', parts[idx]):
            idx += 1
        merchant = parts[idx].strip() if idx < len(parts) else parts[0]
    else:
        merchant = parts[0]
    merchant = re.sub(r'\s+\d{4,}$', '', merchant)
    merchant = re.sub(r'\s+(Visa Purchase|Eftpos|Online|Payment).*$', '', merchant, flags=re.IGNORECASE)
    return merchant.strip() or description[:30]

def parse_anz_csv(file_content):
    transactions = []
    reader = csv.DictReader(StringIO(file_content))
    for row in reader:
        row = {k.strip(): v.strip() for k, v in row.items() if k}
        date_str   = row.get("Date", "")
        amount_str = row.get("Amount", "")
        description_parts = []
        for field in ["Details", "Description", "Particulars", "Code", "Reference", "Type"]:
            val = row.get(field, "").strip()
            if val:
                description_parts.append(val)
        description = " | ".join(description_parts) if description_parts else "Unknown"
        if not date_str or not amount_str or date_str.lower() in ("date", ""):
            continue
        try:
            date   = dateparser.parse(date_str, dayfirst=True)
            amount = float(amount_str.replace(",", ""))
        except (ValueError, TypeError):
            continue
        transactions.append({
            "date":        date.strftime("%Y-%m-%d"),
            "amount":      amount,
            "description": description,
        })
    return transactions

def categorize_transaction(transaction, corrections):
    desc_upper = transaction["description"].upper()
    merchant   = extract_merchant(transaction["description"])

    # Check corrections file first
    for keyword, category in corrections.items():
        if keyword.upper() in desc_upper:
            return category, "corrections", merchant

    # Hard amount-aware rules
    if "OLIVIA JOHNSTON" in desc_upper:
        if abs(transaction["amount"]) == 165.00:
            return "Rent", "rules", merchant
        elif abs(transaction["amount"]) == 4.00:
            return "Shared Flat Savings", "rules", merchant
        else:
            return "Transfers", "rules", merchant

    # Fall back to Ollama
    prompt = f"""You are a personal finance assistant helping categorize bank transactions for a New Zealand user.

{CATEGORY_PREFERENCES}

Categorize this transaction. Reply with ONLY the category name from the list above, nothing else. No explanation.

Transaction:
- Date: {transaction['date']}
- Amount: {transaction['amount']} NZD
- Description: {transaction['description']}

Category:"""

    try:
        resp = http_requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.1, "num_predict": 20}},
            timeout=120
        )
        resp.raise_for_status()
        category = resp.json().get("response", "Uncategorized").strip()
        category = re.sub(r'[^\w\s&\'/\-]', '', category).strip()
        return (category or "Uncategorized"), "ollama", merchant
    except Exception:
        return "Uncategorized", "error", merchant

def run_categorization(job_id, file_batches, profile_id):
    """
    file_batches: list of {"transactions": [...], "account_name": "..."}
    Processes all files sequentially, emitting progress events throughout.
    """
    job         = jobs[job_id]
    q           = job["queue"]
    corrections = load_corrections(profile_id)

    # Count total transactions across all files
    total = sum(len(b["transactions"]) for b in file_batches)
    q.put({"type": "start", "total": total})

    all_results     = []   # flat list of all transactions with account_name attached
    new_merchants   = {}
    global_index    = 0

    for batch in file_batches:
        account_name  = batch["account_name"]
        transactions  = batch["transactions"]

        q.put({"type": "file_start", "account": account_name, "count": len(transactions)})

        for txn in transactions:
            global_index += 1

            # Tell the frontend we're about to ask the AI (so it can show spinner)
            if True:  # always emit — rules are fast, AI is slow, both show in feed
                q.put({
                    "type":        "thinking",
                    "index":       global_index,
                    "total":       total,
                    "description": txn["description"][:50],
                    "account":     account_name,
                })

            category, source, merchant = categorize_transaction(txn, corrections)
            txn["category"]    = category
            txn["source"]      = source
            txn["merchant"]    = merchant
            txn["account_name"] = account_name
            all_results.append(txn)

            q.put({
                "type":        "progress",
                "index":       global_index,
                "total":       total,
                "date":        txn["date"],
                "amount":      txn["amount"],
                "description": txn["description"][:45],
                "category":    category,
                "source":      source,
                "account":     account_name,
            })

    # Find new merchants across all results
    for txn in all_results:
        if txn.get("source") not in ("ollama", "error", "timeout"):
            continue
        merchant       = txn.get("merchant", txn["description"][:30])
        merchant_upper = merchant.upper()
        already_known  = any(k.upper() in merchant_upper or merchant_upper in k.upper()
                             for k in corrections.keys())
        if already_known or merchant in new_merchants:
            continue
        new_merchants[merchant] = {
            "category": txn["category"],
            "count":    sum(1 for t in all_results if t.get("merchant") == merchant),
            "example":  txn["description"][:60],
            "failed":   txn.get("source") in ("error", "timeout")
        }

    job["transactions"]  = all_results
    job["file_batches"]  = file_batches
    job["new_merchants"] = new_merchants
    job["done"]          = True

    q.put({
        "type":          "done",
        "new_merchants": new_merchants,
        "summary":       {cat: sum(1 for t in all_results if t["category"] == cat)
                          for cat in set(t["category"] for t in all_results)}
    })

# ─── ROUTES ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    profiles = load_profiles()
    return render_template("index.html", profiles=profiles, categories=CATEGORIES)

@app.route("/api/status")
def status():
    try:
        resp      = http_requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        ollama_ok = resp.ok
    except Exception:
        ollama_ok = False
    return jsonify({"ollama": ollama_ok})

# ── Profile CRUD ──

@app.route("/api/profiles", methods=["GET"])
def get_profiles():
    return jsonify(load_profiles())

@app.route("/api/profiles", methods=["POST"])
def create_profile():
    data     = request.json
    profiles = load_profiles()
    pid      = str(uuid.uuid4())[:8]
    profiles[pid] = {
        "name":       data["name"],
        "actual_url": data.get("actual_url", "http://localhost:5006"),
        "password":   data["password"],
        "budget_id":  data["budget_id"],
        "accounts":   data.get("accounts", ["Spending", "Savings"]),
    }
    save_profiles(profiles)
    return jsonify({"id": pid, **profiles[pid]})

@app.route("/api/profiles/<pid>", methods=["PUT"])
def update_profile(pid):
    data     = request.json
    profiles = load_profiles()
    if pid not in profiles:
        return jsonify({"error": "Profile not found"}), 404
    profiles[pid].update({
        "name":       data["name"],
        "actual_url": data.get("actual_url", "http://localhost:5006"),
        "password":   data["password"],
        "budget_id":  data["budget_id"],
        "accounts":   data.get("accounts", []),
    })
    save_profiles(profiles)
    return jsonify({"id": pid, **profiles[pid]})

@app.route("/api/profiles/<pid>", methods=["DELETE"])
def delete_profile(pid):
    profiles = load_profiles()
    if pid in profiles:
        del profiles[pid]
        save_profiles(profiles)
    return jsonify({"ok": True})

# ── Upload (multi-file) ──

@app.route("/api/upload", methods=["POST"])
def upload():
    files      = request.files.getlist("files")
    accounts   = request.form.getlist("accounts")
    profile_id = request.form.get("profile_id")

    if not files:
        return jsonify({"error": "No files provided"}), 400
    if not profile_id or profile_id not in load_profiles():
        return jsonify({"error": "Please select a valid profile"}), 400

    file_batches = []
    for i, file in enumerate(files):
        account_name = accounts[i] if i < len(accounts) else "Spending"
        content      = file.read().decode("utf-8-sig")
        transactions = parse_anz_csv(content)
        if transactions:
            file_batches.append({"account_name": account_name, "transactions": transactions, "filename": file.filename})

    if not file_batches:
        return jsonify({"error": "No transactions found in any file. Check your CSV format."}), 400

    total    = sum(len(b["transactions"]) for b in file_batches)
    job_id   = str(uuid.uuid4())
    jobs[job_id] = {"queue": queue.Queue(), "transactions": [], "done": False, "profile_id": profile_id}

    thread = threading.Thread(
        target=run_categorization,
        args=(job_id, file_batches, profile_id),
        daemon=True
    )
    thread.start()
    return jsonify({"job_id": job_id, "total": total, "files": len(file_batches)})

@app.route("/api/stream/<job_id>")
def stream(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404

    def generate():
        q = jobs[job_id]["queue"]
        while True:
            try:
                event = q.get(timeout=180)
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] == "done":
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.route("/api/save-corrections", methods=["POST"])
def save_corrections_route():
    data                 = request.json
    job_id               = data.get("job_id")
    merchant_corrections = data.get("corrections", {})
    profile_id           = jobs.get(job_id, {}).get("profile_id") if job_id else None

    if profile_id:
        corrections = load_corrections(profile_id)
        corrections.update(merchant_corrections)
        save_corrections_for_profile(profile_id, corrections)
        if job_id in jobs:
            for txn in jobs[job_id].get("transactions", []):
                if txn.get("merchant") in merchant_corrections:
                    txn["category"] = merchant_corrections[txn["merchant"]]

    return jsonify({"saved": len(merchant_corrections)})

@app.route("/api/import", methods=["POST"])
def do_import():
    data       = request.json
    job_id     = data.get("job_id")

    if not job_id or job_id not in jobs:
        return jsonify({"error": "Invalid job ID"}), 400

    job          = jobs[job_id]
    transactions = job.get("transactions", [])
    profile_id   = job.get("profile_id")
    profiles     = load_profiles()

    if not profile_id or profile_id not in profiles:
        return jsonify({"error": "Profile not found"}), 400

    profile  = profiles[profile_id]
    js_path  = os.path.join(SCRIPTS_DIR, "actual_import.js")
    if not os.path.exists(js_path):
        return jsonify({"error": "actual_import.js not found"}), 500

    # Group transactions by account and import each separately
    accounts_seen = list(dict.fromkeys(t["account_name"] for t in transactions))
    all_output    = []

    for account_name in accounts_seen:
        account_txns = [t for t in transactions if t["account_name"] == account_name]

        with open(TRANSACTIONS_FILE, "w") as f:
            json.dump({"account": account_name, "transactions": account_txns}, f, indent=2)

        tmp_config = os.path.join(SCRIPTS_DIR, "import_config.json")
        with open(tmp_config, "w") as f:
            json.dump({
                "actual_url": profile["actual_url"],
                "password":   profile["password"],
                "budget_id":  profile["budget_id"],
            }, f)

        result = subprocess.run(
            ["node", "actual_import.js"],
            cwd=SCRIPTS_DIR,
            capture_output=True,
            text=True
        )
        os.remove(tmp_config)

        if result.returncode != 0:
            return jsonify({"error": f"Import failed for {account_name}: {result.stderr}"}), 500

        all_output.append(f"{account_name}: {result.stdout.strip()}")

    del jobs[job_id]
    return jsonify({"success": True, "output": "\n".join(all_output)})

@app.route("/api/categories")
def get_categories():
    return jsonify(CATEGORIES)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5007, debug=False)
