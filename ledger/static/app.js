const state = {
  accounts: [],
  categories: [],
  transactions: [],
  editingTransactionId: null,
  reviewDrafts: {},
  summary: null,
  settings: null,
  period: {
    start: firstDayOfMonth(),
    end: todayIso(),
  },
};

function todayIso() {
  const date = new Date();
  return date.toISOString().slice(0, 10);
}

function firstDayOfMonth() {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-01`;
}

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

function monthLabel(value) {
  if (!value) return "-";
  const [year, month] = value.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleDateString("en-NZ", {
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
    throw new Error(
      data.error || data.message || `Request failed: ${response.status}`,
    );
  }
  return data;
}

function activeView() {
  const path = window.location.pathname.replace("/", "") || "dashboard";
  return ["transactions", "categories", "review", "settings"].includes(path)
    ? path
    : "dashboard";
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

function categoryByName(name) {
  return state.categories.find(
    (category) =>
      category.name.toLowerCase() === String(name || "").toLowerCase(),
  );
}

function categoryColor(name) {
  return categoryByName(name)?.color || "#006D77";
}

function categoryOptions(selected = "", options = {}) {
  const selectedName = selected || "Uncategorized";
  const exclude = String(options.exclude || "").toLowerCase();
  const rows = state.categories.filter(
    (category) => category.name.toLowerCase() !== exclude,
  );
  const hasSelected = rows.some(
    (category) => category.name.toLowerCase() === selectedName.toLowerCase(),
  );
  const optionRows =
    hasSelected || !selectedName
      ? rows
      : [{ name: selectedName, color: "#006D77" }, ...rows];
  return optionRows
    .map((category) => {
      const isSelected =
        category.name.toLowerCase() === selectedName.toLowerCase();
      return `<option value="${escapeAttr(category.name)}" ${isSelected ? "selected" : ""}>${escapeHtml(category.name)}</option>`;
    })
    .join("");
}

function renderStatus() {
  const latest = state.summary?.latest_sync;
  document.querySelector("#last-sync").textContent = latest?.finished_at
    ? `${latest.status} at ${shortDate(latest.finished_at)}`
    : "No sync yet";
  document.querySelector("#review-count").textContent = String(
    state.summary?.review_count || 0,
  );
  document.querySelector("#period-label").textContent = periodLabel();
}

function periodLabel() {
  if (
    state.period.start === firstDayOfMonth() &&
    state.period.end === todayIso()
  ) {
    return "This month";
  }
  return `${shortDate(state.period.start)} to ${shortDate(state.period.end)}`;
}

function renderSettings() {
  const node = document.querySelector("#settings-status");
  if (!node || !state.settings) return;
  node.textContent = [
    `Akahu app token: ${state.settings.akahu_app_token ? "configured" : "missing"}`,
    `Akahu user token: ${state.settings.akahu_user_token ? "configured" : "missing"}`,
    `Discord: ${state.settings.discord_webhook_url ? "configured" : "missing"}`,
  ].join(" | ");
}

function renderKpis() {
  const cards = [
    {
      label: "Total spent",
      value: money(state.summary?.this_month_spend_cents || 0),
      note: "This month, transfers excluded",
    },
    {
      label: "Net cash flow",
      value: money(state.summary?.selected_period_cash_flow_cents || 0),
      note: periodLabel(),
    },
    {
      label: "Largest category",
      value: state.summary?.spending_by_category?.[0]
        ? money(state.summary.spending_by_category[0].spend_cents || 0)
        : money(0),
      note:
        state.summary?.spending_by_category?.[0]?.category || "No spending yet",
    },
  ];
  document.querySelector("#kpi-grid").innerHTML = cards
    .map(
      (card) => `
        <article class="kpi-card">
          <span class="label">${escapeHtml(card.label)}</span>
          <strong>${escapeHtml(card.value)}</strong>
          <p>${escapeHtml(card.note)}</p>
        </article>
      `,
    )
    .join("");
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
          <div>
            <h3>${escapeHtml(account.name)}</h3>
            <p class="meta">${escapeHtml(account.connection_name || "")} ${escapeHtml(account.formatted_account || "")}</p>
          </div>
          <p class="balance">${money(account.balance_current_cents, account.currency)}</p>
        </article>
      `,
    )
    .join("");
}

function renderCategorySpend() {
  document.querySelector("#category-spend").innerHTML = categoryRows(
    state.summary?.spending_by_category || [],
  );
}

function categoryRows(rows) {
  if (!rows.length) {
    return `<div class="empty">No spending data for this period.</div>`;
  }
  const max = Math.max(...rows.map((row) => Number(row.spend_cents || 0)), 1);
  return rows
    .map((row) => {
      const width = Math.max(
        4,
        Math.round((Number(row.spend_cents || 0) / max) * 100),
      );
      const color = row.color || categoryColor(row.category);
      return `
        <div class="metric-row" style="--category-color: ${escapeAttr(color)}">
          <span class="metric-name"><span class="swatch"></span>${escapeHtml(row.category)}</span>
          <span class="metric-value">${money(row.spend_cents)}</span>
          <span class="metric-bar" aria-hidden="true"><span style="width: ${width}%"></span></span>
        </div>
      `;
    })
    .join("");
}

function renderMonthlyChart() {
  const rows = state.summary?.monthly_spending || [];
  const root = document.querySelector("#monthly-chart");
  if (!rows.length) {
    root.innerHTML = `<div class="empty">No monthly spending history yet.</div>`;
    return;
  }
  const max = Math.max(...rows.map((row) => Number(row.spend_cents || 0)), 1);
  root.innerHTML = rows
    .map((row) => {
      const width = Math.max(
        4,
        Math.round((Number(row.spend_cents || 0) / max) * 100),
      );
      return `
        <div class="month-row">
          <span class="month-label">${escapeHtml(monthLabel(row.month))}</span>
          <span class="month-track" aria-label="${escapeAttr(`${monthLabel(row.month)} spending ${money(row.spend_cents)}`)}">
            <span style="width: ${width}%"></span>
          </span>
          <strong>${money(row.spend_cents)}</strong>
        </div>
      `;
    })
    .join("");
}

function renderCategoryList() {
  const root = document.querySelector("#category-list");
  if (!state.categories.length) {
    root.innerHTML = `<div class="empty">No categories yet.</div>`;
    return;
  }
  root.innerHTML = state.categories
    .map((category) => {
      const replacement = category.transaction_count
        ? `
          <label class="replacement-field">
            Reassign to
            <select name="replacement">
              <option value="">Choose replacement</option>
              ${categoryOptions("", { exclude: category.name })}
            </select>
          </label>
        `
        : "";
      return `
        <article class="category-card ${category.is_system ? "system" : ""}" style="--category-color: ${escapeAttr(category.color)}">
          <form class="category-edit" data-category="${escapeAttr(category.name)}" data-count="${Number(category.transaction_count || 0)}">
            <span class="swatch large"></span>
            <label>
              Name
              <input name="name" maxlength="64" value="${escapeAttr(category.name)}" ${category.is_system ? "disabled" : ""}>
            </label>
            <label>
              Color
              <input name="color" type="color" value="${escapeAttr(category.color)}">
            </label>
            <div class="category-stats">
              <strong>${money(category.spend_cents || 0)}</strong>
              <span>${Number(category.transaction_count || 0)} transaction${Number(category.transaction_count || 0) === 1 ? "" : "s"}</span>
            </div>
            ${category.is_system ? `<span class="pill">System</span>` : replacement}
            <div class="category-actions">
              <button type="submit" class="ghost">Save</button>
              ${category.is_system ? "" : `<button type="button" class="danger delete-category">Delete</button>`}
            </div>
          </form>
        </article>
      `;
    })
    .join("");
}

function fillFilters() {
  const accountSelect = document.querySelector("#filter-account");
  const categorySelect = document.querySelector("#filter-category");
  const currentAccount = accountSelect.value;
  const currentCategory = categorySelect.value;
  accountSelect.innerHTML =
    `<option value="">All accounts</option>` +
    state.accounts
      .map(
        (account) =>
          `<option value="${escapeAttr(account.id)}">${escapeHtml(account.name)}</option>`,
      )
      .join("");
  categorySelect.innerHTML =
    `<option value="">All categories</option>` +
    state.categories
      .map(
        (category) =>
          `<option value="${escapeAttr(category.name)}">${escapeHtml(category.name)}</option>`,
      )
      .join("");
  accountSelect.value = currentAccount;
  categorySelect.value = currentCategory;
}

function syncPeriodInputs() {
  document.querySelector("#period-start").value = state.period.start;
  document.querySelector("#period-end").value = state.period.end;
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
    root.innerHTML = `<tr><td colspan="6">No transactions found.</td></tr>`;
    return;
  }
  root.innerHTML = state.transactions
    .map((tx) => {
      const amountClass = tx.amount_cents < 0 ? "spend" : "income";
      const isEditing = state.editingTransactionId === tx.id;
      return `
        <tr>
          <td>${shortDate(tx.date)}</td>
          <td>
            <strong>${escapeHtml(tx.effective_merchant)}</strong>
            <span class="table-note">${escapeHtml(tx.description || "")} - ${escapeHtml(tx.account_name || "")}</span>
          </td>
          <td>${categoryChip(tx.effective_category || "Uncategorized")}</td>
          <td class="money ${amountClass}">${money(tx.amount_cents, tx.currency)}</td>
          <td><span class="pill muted">${escapeHtml(tx.review_status || "auto")}</span></td>
          <td class="transaction-actions">
            ${
              isEditing
                ? transactionEditForm(tx)
                : `<button type="button" class="ghost edit-transaction" data-transaction="${escapeAttr(tx.id)}">Edit</button>`
            }
          </td>
        </tr>
      `;
    })
    .join("");
}

function categoryChip(name) {
  return `
    <span class="category-chip" style="--category-color: ${escapeAttr(categoryColor(name))}">
      <span class="swatch"></span>${escapeHtml(name)}
    </span>
  `;
}

function renderReview() {
  const reviewItems = state.transactions.filter(
    (tx) => tx.review_status === "needs_review",
  );
  const reviewIds = new Set(reviewItems.map((tx) => tx.id));
  for (const transactionId of Object.keys(state.reviewDrafts)) {
    if (!reviewIds.has(transactionId)) {
      delete state.reviewDrafts[transactionId];
    }
  }
  document.querySelector("#review-preview").innerHTML = reviewMarkup(
    reviewItems.slice(0, 5),
    true,
  );
  document.querySelector("#review-list").innerHTML = reviewMarkup(
    reviewItems,
    false,
  );
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
            <div>
              <strong>${escapeHtml(tx.effective_merchant)}</strong>
              <p class="meta">${shortDate(tx.date)} - ${escapeHtml(tx.description)} - ${money(tx.amount_cents, tx.currency)}</p>
            </div>
            <span class="pill">${escapeHtml(tx.review_reason || "review")}</span>
          </div>
          ${compact ? "" : reviewForm(tx)}
        </article>
      `,
    )
    .join("");
}

function reviewForm(tx) {
  const selectedCategory =
    state.reviewDrafts[tx.id] || tx.effective_category || "Uncategorized";
  return `
    <form class="review-form" data-transaction="${escapeAttr(tx.id)}" data-merchant-key="${escapeAttr(tx.merchant_key)}">
      <label>
        Merchant
        <input name="display_merchant" value="${escapeAttr(tx.effective_merchant)}" required>
      </label>
      <label>
        Category
        <select name="category_name" required>
          ${categoryOptions(selectedCategory)}
        </select>
      </label>
      <button type="submit">Approve</button>
    </form>
  `;
}

function transactionEditForm(tx) {
  return `
    <form class="transaction-edit-form" data-transaction="${escapeAttr(tx.id)}" data-merchant="${escapeAttr(tx.effective_merchant)}">
      <select name="category_name" required>
        ${categoryOptions(tx.effective_category || "Uncategorized")}
      </select>
      <button type="submit">Save</button>
      <button type="button" class="ghost cancel-transaction-edit">Cancel</button>
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
  await api(
    `/api/transactions/${encodeURIComponent(form.dataset.transaction)}/override`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
  delete state.reviewDrafts[form.dataset.transaction];
  await refresh();
  await loadTransactions(activeView() === "review");
}

function handleReviewDraftChange(event) {
  const select = event.target.closest(".review-form select[name='category_name']");
  if (!select) return;
  const form = select.closest(".review-form");
  if (!form) return;
  state.reviewDrafts[form.dataset.transaction] = select.value;
}

function handleTransactionActions(event) {
  const editButton = event.target.closest(".edit-transaction");
  if (editButton) {
    state.editingTransactionId = editButton.dataset.transaction;
    renderTransactions();
    return;
  }
  const cancelButton = event.target.closest(".cancel-transaction-edit");
  if (cancelButton) {
    state.editingTransactionId = null;
    renderTransactions();
  }
}

async function saveTransactionEdit(event) {
  const form = event.target.closest(".transaction-edit-form");
  if (!form) return;
  event.preventDefault();
  const transactionId = form.dataset.transaction;
  const payload = {
    category_name: form.elements.category_name.value,
    display_merchant: form.dataset.merchant,
  };
  await api(`/api/transactions/${encodeURIComponent(transactionId)}/override`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.editingTransactionId = null;
  await refresh();
  await loadTransactions(activeView() === "review");
}

async function refresh() {
  const params = new URLSearchParams();
  if (state.period.start) params.set("start", state.period.start);
  if (state.period.end) params.set("end", state.period.end);
  const periodQuery = params.toString();
  const [summary, accountData, categoryData, settingsData] = await Promise.all([
    api(`/api/summary${periodQuery ? `?${periodQuery}` : ""}`),
    api("/api/accounts"),
    api(`/api/categories${periodQuery ? `?${periodQuery}` : ""}`),
    api("/api/settings"),
  ]);
  state.summary = summary;
  state.accounts = accountData.accounts || [];
  state.categories = categoryData.categories || [];
  state.settings = settingsData;
  renderStatus();
  renderSettings();
  renderKpis();
  renderAccounts();
  renderCategorySpend();
  renderMonthlyChart();
  renderCategoryList();
  fillFilters();
  syncPeriodInputs();
}

async function syncNow(backfill = false) {
  const button = backfill
    ? document.querySelector("#sync-backfill")
    : document.querySelector("#sync-now");
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
  } catch (error) {
    settingsOutput({ error: error.message });
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {};
  for (const field of [
    "AKAHU_APP_TOKEN",
    "AKAHU_USER_TOKEN",
    "DISCORD_WEBHOOK_URL",
  ]) {
    const value = form.elements[field].value.trim();
    if (value) payload[field] = value;
  }
  if (!Object.keys(payload).length) {
    settingsOutput({ status: "skipped", reason: "No values entered" });
    return;
  }
  state.settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  form.reset();
  renderSettings();
  settingsOutput({ status: "saved", settings: state.settings });
}

async function createCategory(event) {
  event.preventDefault();
  const form = event.currentTarget;
  await api("/api/categories", {
    method: "POST",
    body: JSON.stringify({
      name: form.elements.name.value,
      color: form.elements.color.value,
    }),
  });
  form.reset();
  form.elements.color.value = "#006D77";
  await refresh();
}

async function saveCategory(event) {
  const form = event.target.closest(".category-edit");
  if (!form) return;
  event.preventDefault();
  await api(`/api/categories/${encodeURIComponent(form.dataset.category)}`, {
    method: "PATCH",
    body: JSON.stringify({
      name: form.elements.name.value,
      color: form.elements.color.value,
    }),
  });
  await refresh();
  await loadTransactions(activeView() === "review");
}

async function deleteCategory(event) {
  const button = event.target.closest(".delete-category");
  if (!button) return;
  const form = button.closest(".category-edit");
  const payload = {};
  if (Number(form.dataset.count || 0) > 0) {
    const replacement = form.elements.replacement.value;
    if (!replacement) {
      settingsOutput({
        error: "Choose a replacement category before deleting.",
      });
      form.elements.replacement.focus();
      return;
    }
    payload.replacement_category = replacement;
  }
  await api(`/api/categories/${encodeURIComponent(form.dataset.category)}`, {
    method: "DELETE",
    body: JSON.stringify(payload),
  });
  await refresh();
  await loadTransactions(activeView() === "review");
}

async function applyPeriod(event) {
  event.preventDefault();
  state.period.start =
    document.querySelector("#period-start").value || firstDayOfMonth();
  state.period.end = document.querySelector("#period-end").value || todayIso();
  await refresh();
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
  syncPeriodInputs();
  await refresh();
  await loadTransactions(activeView() === "review");
  document
    .querySelector("#apply-filters")
    ?.addEventListener("click", () => loadTransactions(false));
  document
    .querySelector("#period-form")
    ?.addEventListener("submit", applyPeriod);
  document
    .querySelector("#sync-now")
    ?.addEventListener("click", () => syncNow(false));
  document
    .querySelector("#sync-backfill")
    ?.addEventListener("click", () => syncNow(true));
  document
    .querySelector("#review-list")
    ?.addEventListener("submit", approveReview);
  document
    .querySelector("#review-list")
    ?.addEventListener("change", handleReviewDraftChange);
  document
    .querySelector("#settings-form")
    ?.addEventListener("submit", saveSettings);
  document
    .querySelector("#category-form")
    ?.addEventListener("submit", createCategory);
  document
    .querySelector("#category-list")
    ?.addEventListener("submit", saveCategory);
  document
    .querySelector("#category-list")
    ?.addEventListener("click", deleteCategory);
  document
    .querySelector("#transactions-table")
    ?.addEventListener("click", handleTransactionActions);
  document
    .querySelector("#transactions-table")
    ?.addEventListener("submit", saveTransactionEdit);
  document
    .querySelector("#test-discord")
    ?.addEventListener("click", async () => {
      settingsOutput(
        await api("/api/notifications/test", { method: "POST", body: "{}" }),
      );
    });
  document.querySelector("#notify-now")?.addEventListener("click", async () => {
    settingsOutput(
      await api("/api/notifications/review-needed", {
        method: "POST",
        body: "{}",
      }),
    );
  });
}

boot().catch((error) => {
  settingsOutput({ error: error.message });
  console.error(error);
});
