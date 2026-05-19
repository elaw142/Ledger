const state = {
  accounts: [],
  categories: [],
  transactions: [],
  summary: null,
};

function money(cents, currency = "NZD") {
  if (cents === null || cents === undefined) return "-";
  const value = Number(cents) / 100;
  return value.toLocaleString("en-NZ", {
    style: "currency",
    currency,
  });
}

function shortDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleDateString("en-NZ", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || data.message || `Request failed: ${response.status}`);
  }
  return data;
}

function activeView() {
  const path = window.location.pathname.replace("/", "") || "dashboard";
  return ["transactions", "categories", "review", "settings"].includes(path) ? path : "dashboard";
}

function setActiveView() {
  const view = activeView();
  document.querySelectorAll(".view").forEach((node) => {
    node.classList.toggle("active", node.id === `view-${view}`);
  });
  document.querySelectorAll(".nav-tabs a").forEach((node) => {
    node.classList.toggle("active", node.dataset.view === view);
  });
}

function renderStatus() {
  const latest = state.summary?.latest_sync;
  document.querySelector("#last-sync").textContent = latest?.finished_at
    ? `${latest.status} at ${shortDate(latest.finished_at)}`
    : "No sync yet";
  document.querySelector("#review-count").textContent = String(state.summary?.review_count || 0);
}

function renderAccounts() {
  const root = document.querySelector("#accounts");
  if (!state.accounts.length) {
    root.innerHTML = `<div class="empty">No accounts synced yet.</div>`;
    return;
  }
  root.innerHTML = state.accounts
    .map(
      (account) => `
        <article class="account-card">
          <h3>${escapeHtml(account.name)}</h3>
          <p class="balance">${money(account.balance_current_cents, account.currency)}</p>
          <p class="meta">${escapeHtml(account.connection_name || "")} ${escapeHtml(account.formatted_account || "")}</p>
        </article>
      `,
    )
    .join("");
}

function renderCategorySpend() {
  const html = categoryRows(state.summary?.spending_by_category || []);
  document.querySelector("#category-spend").innerHTML = html;
  document.querySelector("#categories-full").innerHTML = html;
}

function categoryRows(rows) {
  if (!rows.length) {
    return `<div class="empty">No spending data for this period.</div>`;
  }
  return rows
    .map(
      (row) => `
        <div class="metric-row">
          <span class="metric-name">${escapeHtml(row.category)}</span>
          <span class="metric-value">${money(row.spend_cents)}</span>
        </div>
      `,
    )
    .join("");
}

function fillFilters() {
  const accountSelect = document.querySelector("#filter-account");
  const categorySelect = document.querySelector("#filter-category");
  accountSelect.innerHTML = `<option value="">All accounts</option>` + state.accounts
    .map((account) => `<option value="${escapeAttr(account.id)}">${escapeHtml(account.name)}</option>`)
    .join("");
  categorySelect.innerHTML = `<option value="">All categories</option>` + state.categories
    .map((category) => `<option value="${escapeAttr(category)}">${escapeHtml(category)}</option>`)
    .join("");
}

function transactionQuery(reviewOnly = false) {
  const params = new URLSearchParams();
  const fields = [
    ["account", "#filter-account"],
    ["category", "#filter-category"],
    ["start", "#filter-start"],
    ["end", "#filter-end"],
  ];
  for (const [name, selector] of fields) {
    const value = document.querySelector(selector)?.value;
    if (value) params.set(name, value);
  }
  if (reviewOnly) params.set("review_status", "needs_review");
  return params.toString();
}

async function loadTransactions(reviewOnly = false) {
  const query = transactionQuery(reviewOnly);
  const data = await api(`/api/transactions${query ? `?${query}` : ""}`);
  state.transactions = data.transactions || [];
  renderTransactions();
  renderReview();
}

function renderTransactions() {
  const root = document.querySelector("#transactions-table");
  if (!state.transactions.length) {
    root.innerHTML = `<tr><td colspan="5">No transactions found.</td></tr>`;
    return;
  }
  root.innerHTML = state.transactions
    .map((tx) => {
      const amountClass = tx.amount_cents < 0 ? "spend" : "income";
      return `
        <tr>
          <td>${shortDate(tx.date)}</td>
          <td>${escapeHtml(tx.effective_merchant)}</td>
          <td>${escapeHtml(tx.effective_category || "Uncategorized")}</td>
          <td>${escapeHtml(tx.account_name || "")}</td>
          <td class="money ${amountClass}">${money(tx.amount_cents, tx.currency)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderReview() {
  const reviewItems = state.transactions.filter((tx) => tx.review_status === "needs_review");
  document.querySelector("#review-preview").innerHTML = reviewMarkup(reviewItems.slice(0, 5), true);
  document.querySelector("#review-list").innerHTML = reviewMarkup(reviewItems, false);
}

function reviewMarkup(items, compact) {
  if (!items.length) {
    return `<div class="empty">Nothing waiting for review.</div>`;
  }
  return items
    .map(
      (tx) => `
        <article class="review-item" data-transaction="${escapeAttr(tx.id)}">
          <div class="review-title">
            <strong>${escapeHtml(tx.effective_merchant)}</strong>
            <span class="pill">${escapeHtml(tx.review_reason || "review")}</span>
          </div>
          <p class="meta">${shortDate(tx.date)} · ${escapeHtml(tx.description)} · ${money(tx.amount_cents, tx.currency)}</p>
          ${compact ? "" : reviewForm(tx)}
        </article>
      `,
    )
    .join("");
}

function reviewForm(tx) {
  return `
    <form class="review-form" data-transaction="${escapeAttr(tx.id)}" data-merchant-key="${escapeAttr(tx.merchant_key)}">
      <input name="display_merchant" value="${escapeAttr(tx.effective_merchant)}" placeholder="Merchant">
      <input name="category_name" value="${escapeAttr(tx.effective_category || "")}" placeholder="Category">
      <button type="submit">Approve</button>
    </form>
  `;
}

async function approveReview(event) {
  const form = event.target.closest(".review-form");
  if (!form) return;
  event.preventDefault();
  const payload = {
    display_merchant: form.elements.display_merchant.value,
    category_name: form.elements.category_name.value,
    merchant_key: form.dataset.merchantKey,
    apply_to_merchant: true,
  };
  await api(`/api/transactions/${encodeURIComponent(form.dataset.transaction)}/override`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  await refresh();
  await loadTransactions(true);
}

async function refresh() {
  const [summary, accountData, categoryData] = await Promise.all([
    api("/api/summary"),
    api("/api/accounts"),
    api("/api/categories"),
  ]);
  state.summary = summary;
  state.accounts = accountData.accounts || [];
  state.categories = categoryData.categories || [];
  renderStatus();
  renderAccounts();
  renderCategorySpend();
  fillFilters();
}

async function syncNow(backfill = false) {
  const button = backfill ? document.querySelector("#sync-backfill") : document.querySelector("#sync-now");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Syncing";
  try {
    const result = await api("/api/sync", {
      method: "POST",
      body: JSON.stringify({ backfill }),
    });
    settingsOutput(result);
    await refresh();
    await loadTransactions(activeView() === "review");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function settingsOutput(value) {
  const node = document.querySelector("#settings-output");
  if (node) node.textContent = JSON.stringify(value, null, 2);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

async function boot() {
  setActiveView();
  await refresh();
  await loadTransactions(activeView() === "review");
  document.querySelector("#apply-filters")?.addEventListener("click", () => loadTransactions(false));
  document.querySelector("#sync-now")?.addEventListener("click", () => syncNow(false));
  document.querySelector("#sync-backfill")?.addEventListener("click", () => syncNow(true));
  document.querySelector("#review-list")?.addEventListener("submit", approveReview);
  document.querySelector("#test-discord")?.addEventListener("click", async () => {
    settingsOutput(await api("/api/notifications/test", { method: "POST", body: "{}" }));
  });
  document.querySelector("#notify-now")?.addEventListener("click", async () => {
    settingsOutput(await api("/api/notifications/review-needed", { method: "POST", body: "{}" }));
  });
}

boot().catch((error) => {
  settingsOutput({ error: error.message });
  console.error(error);
});

