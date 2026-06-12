// Wolf Grounds Stock Bot — dashboard frontend (read-only).
const REFRESH_MS = 15000;

const fmtUSD = (n) =>
  (n < 0 ? "-$" : "$") +
  Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPct = (n) => (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
const signClass = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "");

async function getJSON(url) {
  const r = await fetch(url);
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function setStatus(text, kind) {
  const el = document.getElementById("status");
  el.textContent = text;
  el.className = "status" + (kind ? " " + kind : "");
}

async function loadStatus() {
  try {
    const s = await getJSON("/api/status");
    if (s.logged_in) setStatus(`connected · ${s.trading_mode}`, "ok");
    else setStatus(s.error || "not logged in", "bad");
    return s;
  } catch (e) {
    setStatus(e.message, "bad");
    return null;
  }
}

async function loadPortfolio() {
  let data;
  try {
    data = await getJSON("/api/portfolio");
  } catch (e) {
    setStatus(e.message, "bad");
    return;
  }
  const { summary, holdings } = data;

  document.getElementById("total-equity").textContent = fmtUSD(summary.total_equity);
  document.getElementById("cash").textContent = fmtUSD(summary.cash);

  const dc = document.getElementById("day-change");
  dc.textContent = `${fmtUSD(summary.day_change)} (${fmtPct(summary.day_change_pct)})`;
  dc.className = "value " + signClass(summary.day_change);

  const tbody = document.querySelector("#holdings tbody");
  if (!holdings.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="muted">No positions.</td></tr>`;
  } else {
    tbody.innerHTML = holdings
      .map(
        (h) => `
      <tr>
        <td><strong>${h.symbol}</strong></td>
        <td>${h.quantity.toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
        <td class="num">${fmtUSD(h.price)}</td>
        <td class="num">${fmtUSD(h.average_buy_price)}</td>
        <td class="num">${fmtUSD(h.equity)}</td>
        <td class="num ${signClass(h.equity_change)}">${fmtUSD(h.equity_change)}</td>
        <td class="num ${signClass(h.percent_change)}">${fmtPct(h.percent_change)}</td>
      </tr>`
      )
      .join("");
  }
  document.getElementById("refresh-note").textContent =
    "· updated " + new Date().toLocaleTimeString();
}

async function lookupQuotes(symbols) {
  const box = document.getElementById("quote-results");
  box.innerHTML = `<div class="muted">loading…</div>`;
  try {
    const { quotes } = await getJSON("/api/quote?symbols=" + encodeURIComponent(symbols));
    if (!quotes.length) {
      box.innerHTML = `<div class="muted">No results.</div>`;
      return;
    }
    box.innerHTML = quotes
      .map(
        (q) => `
      <div class="quote-row">
        <span class="sym">${q.symbol}</span>
        <span class="px">
          ${fmtUSD(q.price)}
          <span class="${signClass(q.change)}">${fmtUSD(q.change)} (${fmtPct(q.change_pct)})</span>
        </span>
      </div>`
      )
      .join("");
  } catch (e) {
    box.innerHTML = `<div class="neg">${e.message}</div>`;
  }
}

document.getElementById("quote-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = document.getElementById("quote-input").value.trim();
  if (v) lookupQuotes(v);
});

// ── trading ───────────────────────────────────────────────────────────────
let tradingMode = "read_only";
let currentSide = "buy";
let pendingOrder = null; // live order awaiting confirmation
let killed = false;

async function loadPaper() {
  let p;
  try {
    p = await getJSON("/api/paper");
  } catch (e) {
    return; // surfaced elsewhere
  }
  document.getElementById("p-equity").textContent = fmtUSD(p.total_equity);
  document.getElementById("p-cash").textContent = fmtUSD(p.cash);

  const pnl = document.getElementById("p-pnl");
  pnl.textContent = `${fmtUSD(p.total_pnl)} (${fmtPct(p.total_pnl_pct)})`;
  pnl.className = "value " + signClass(p.total_pnl);

  const real = document.getElementById("p-realized");
  real.textContent = fmtUSD(p.realized_pnl);
  real.className = "value " + signClass(p.realized_pnl);

  const posBody = document.querySelector("#p-positions tbody");
  posBody.innerHTML = p.positions.length
    ? p.positions
        .map(
          (h) => `
      <tr>
        <td><strong>${h.symbol}</strong></td>
        <td>${h.quantity.toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
        <td class="num">${fmtUSD(h.avg_cost)}</td>
        <td class="num">${fmtUSD(h.price)}</td>
        <td class="num">${fmtUSD(h.market_value)}</td>
        <td class="num ${signClass(h.unrealized_pnl)}">${fmtUSD(h.unrealized_pnl)} (${fmtPct(h.unrealized_pct)})</td>
      </tr>`
        )
        .join("")
    : `<tr><td colspan="6" class="muted">No paper positions.</td></tr>`;

  const trBody = document.querySelector("#p-trades tbody");
  trBody.innerHTML = p.trades.length
    ? p.trades
        .map(
          (t) => `
      <tr>
        <td class="muted">${new Date(t.time).toLocaleString()}</td>
        <td class="${t.side === "buy" ? "pos" : "neg"}">${t.side.toUpperCase()}</td>
        <td><strong>${t.symbol}</strong></td>
        <td class="num">${t.quantity}</td>
        <td class="num">${fmtUSD(t.price)}</td>
        <td class="num">${fmtUSD(t.notional)}</td>
      </tr>`
        )
        .join("")
    : `<tr><td colspan="6" class="muted">No trades yet.</td></tr>`;
}

function submitLabel() {
  const verb = currentSide === "buy" ? "Buy" : "Sell";
  return tradingMode === "live" ? `Review ${verb} order` : `${verb} (paper)`;
}

async function postOrder(body) {
  const r = await fetch("/api/order", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function showConfirm(preview, body) {
  pendingOrder = { ...body, confirm: true };
  const rows = [
    ["Action", `${preview.side.toUpperCase()} ${preview.symbol}`],
    ["Quantity", preview.quantity],
    ["Order type", preview.type === "limit" ? `Limit @ ${fmtUSD(preview.limit_price)}` : "Market"],
    ["Last price", fmtUSD(preview.last_price)],
    ["Est. cost", fmtUSD(preview.est_notional)],
  ];
  document.getElementById("confirm-details").innerHTML = rows
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`)
    .join("");
  document.getElementById("confirm-modal").hidden = false;
}

function hideConfirm() {
  document.getElementById("confirm-modal").hidden = true;
  pendingOrder = null;
}

function setupTradePanel() {
  // Buy/Sell segmented control
  document.querySelectorAll("#side-seg button").forEach((b) => {
    b.addEventListener("click", () => {
      currentSide = b.dataset.side;
      document.querySelectorAll("#side-seg button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      document.getElementById("t-submit").textContent = submitLabel();
    });
  });
  document.getElementById("t-submit").textContent = submitLabel();

  // Show limit price field only for limit orders
  document.getElementById("t-type").addEventListener("change", (e) => {
    document.getElementById("t-limit").hidden = e.target.value !== "limit";
  });

  // Submit
  document.getElementById("trade-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = document.getElementById("trade-msg");
    const symbol = document.getElementById("t-symbol").value.trim().toUpperCase();
    const quantity = parseFloat(document.getElementById("t-qty").value);
    const type = document.getElementById("t-type").value;
    const limit_price = parseFloat(document.getElementById("t-limit").value);

    if (!symbol || !(quantity > 0)) {
      msg.textContent = "Enter a symbol and a quantity.";
      msg.className = "trade-msg err";
      return;
    }
    const body = { symbol, side: currentSide, quantity, type };
    if (type === "limit") body.limit_price = limit_price;

    msg.textContent = tradingMode === "live" ? "Getting preview…" : "Placing…";
    msg.className = "trade-msg";
    try {
      const data = await postOrder(body); // no confirm flag
      if (tradingMode === "live" && data.preview && !data.ok) {
        // Live: surface the confirmation modal instead of filling.
        showConfirm(data.preview, body);
        msg.textContent = "";
        return;
      }
      // Paper: filled immediately.
      const t = data.trade;
      msg.textContent = `✓ ${t.side.toUpperCase()} ${t.quantity} ${t.symbol} @ ${fmtUSD(t.price)} (paper)`;
      msg.className = "trade-msg ok";
      document.getElementById("t-qty").value = "";
      loadPaper();
    } catch (err) {
      msg.textContent = "✗ " + err.message;
      msg.className = "trade-msg err";
    }
  });

  // Confirm modal (live)
  document.getElementById("confirm-cancel").addEventListener("click", hideConfirm);
  document.getElementById("confirm-place").addEventListener("click", async () => {
    if (!pendingOrder) return;
    const msg = document.getElementById("trade-msg");
    const btn = document.getElementById("confirm-place");
    btn.disabled = true;
    btn.textContent = "Placing…";
    try {
      const data = await postOrder(pendingOrder);
      const o = data.order;
      msg.textContent = `✓ LIVE ${o.side.toUpperCase()} ${o.quantity} ${pendingOrder.symbol} submitted (state: ${o.state || "pending"}).`;
      msg.className = "trade-msg ok";
      document.getElementById("t-qty").value = "";
      loadPortfolio();
    } catch (err) {
      msg.textContent = "✗ " + err.message;
      msg.className = "trade-msg err";
    } finally {
      btn.disabled = false;
      btn.textContent = "Place real order";
      hideConfirm();
    }
  });

  // Paper reset (only present in paper mode)
  const resetBtn = document.getElementById("p-reset");
  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      if (!confirm("Reset paper account to its starting cash? This clears all paper positions and trades.")) return;
      await fetch("/api/paper/reset", { method: "POST" });
      loadPaper();
      const msg = document.getElementById("trade-msg");
      msg.textContent = "Paper account reset.";
      msg.className = "trade-msg ok";
    });
  }
}

function setupKillSwitch(status) {
  const banner = document.getElementById("live-banner");
  const cap = document.getElementById("lb-cap");
  let capText = fmtUSD(status.max_order_usd);
  if (status.max_order_shares) capText += ` / ${status.max_order_shares} shares`;
  cap.textContent = capText;

  const applyKill = (k) => {
    killed = k;
    const btn = document.getElementById("kill-btn");
    const submit = document.getElementById("t-submit");
    banner.classList.toggle("killed", k);
    btn.classList.toggle("engaged", k);
    btn.textContent = k ? "Disengage kill switch" : "Engage kill switch";
    if (submit) submit.disabled = k;
  };
  applyKill(status.killed);

  document.getElementById("kill-btn").addEventListener("click", async () => {
    const r = await fetch("/api/kill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !killed }),
    });
    const data = await r.json();
    applyKill(data.killed);
  });
}

// ── live orders ─────────────────────────────────────────────────────────────
async function loadOrders() {
  const tbody = document.querySelector("#orders-table tbody");
  let orders;
  try {
    ({ orders } = await getJSON("/api/orders"));
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" class="neg">${e.message}</td></tr>`;
    return;
  }
  if (!orders.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">No recent orders.</td></tr>`;
    return;
  }
  const cancellable = ["queued", "confirmed", "unconfirmed", "partially_filled"];
  tbody.innerHTML = orders
    .map((o) => {
      const canCancel = cancellable.includes((o.state || "").toLowerCase());
      return `<tr>
        <td class="muted">${o.created_at ? new Date(o.created_at).toLocaleString() : "—"}</td>
        <td><strong>${o.symbol}</strong></td>
        <td class="${o.side === "buy" ? "pos" : "neg"}">${(o.side || "").toUpperCase()}</td>
        <td>${o.type || ""}</td>
        <td class="num">${o.quantity}</td>
        <td class="num">${o.filled}</td>
        <td>${o.state || ""}</td>
        <td class="num">${canCancel ? `<button class="cancel-btn" data-cancel="${o.id}">cancel</button>` : ""}</td>
      </tr>`;
    })
    .join("");
}

function setupOrders() {
  document.getElementById("orders-refresh").addEventListener("click", loadOrders);
  document.querySelector("#orders-table tbody").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-cancel]");
    if (!btn) return;
    if (!confirm("Cancel this live order?")) return;
    btn.disabled = true;
    try {
      const r = await fetch("/api/orders/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: btn.dataset.cancel }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.statusText);
    } catch (err) {
      alert("Cancel failed: " + err.message);
    }
    loadOrders();
  });
}

// ── strategies ──────────────────────────────────────────────────────────────
const INTERVALS = { 3600: "hourly", 86400: "daily", 604800: "weekly" };
const intervalLabel = (n) => INTERVALS[n] || `${n}s`;

function strategyDetails(s) {
  if (s.type === "dca") {
    const amt = s.amount_usd > 0 ? fmtUSD(s.amount_usd) : `${s.shares} sh`;
    return `${(s.side || "buy").toUpperCase()} ${amt} of <strong>${s.symbol}</strong>`;
  }
  if (s.type === "rebalance") {
    const t = Object.entries(s.targets || {})
      .map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`)
      .join(" · ");
    return `${t} <span class="muted">(min ${fmtUSD(s.threshold_usd)})</span>`;
  }
  return s.type;
}

async function loadStrategies() {
  const tbody = document.querySelector("#strat-table tbody");
  let list;
  try {
    ({ strategies: list } = await getJSON("/api/strategies"));
  } catch (e) {
    return;
  }
  if (!list.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted">No strategies yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = list
    .map((s) => {
      const res = s.last_result;
      const status = res
        ? `<span class="${res.ok ? "pos" : "neg"}">${res.ok ? "✓" : "✗"} ${res.detail || ""}</span>`
        : '<span class="muted">not run yet</span>';
      const badge = s.enabled ? '<span class="badge on">ON</span>' : '<span class="badge off">off</span>';
      return `<tr>
        <td>${s.type} ${badge}</td>
        <td>${strategyDetails(s)}</td>
        <td>${intervalLabel(s.interval_seconds)}</td>
        <td class="muted">${s.last_run ? new Date(s.last_run).toLocaleString() : "—"}</td>
        <td>${status}</td>
        <td class="row-actions">
          <button class="toggle-btn" data-toggle="${s.id}" data-enabled="${s.enabled}">${s.enabled ? "disable" : "enable"}</button>
          <button class="toggle-btn" data-run="${s.id}">run now</button>
          <button class="del-btn" data-del="${s.id}">×</button>
        </td>
      </tr>`;
    })
    .join("");
}

function setupStrategies(mode) {
  document.getElementById("strat-mode").textContent = `· runs in ${mode} mode`;

  // Show fields for the selected strategy type
  const typeSel = document.getElementById("s-type");
  const syncFields = () => {
    document.querySelectorAll(".s-fields").forEach((el) => {
      el.hidden = el.dataset.for !== typeSel.value;
    });
  };
  typeSel.addEventListener("change", syncFields);
  syncFields();

  // Add strategy
  document.getElementById("strat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = document.getElementById("strat-msg");
    const type = typeSel.value;
    const interval_seconds = parseInt(document.getElementById("s-interval").value, 10);
    let body = { type, interval_seconds };

    if (type === "dca") {
      body.side = document.getElementById("s-side").value;
      body.symbol = document.getElementById("s-symbol").value.trim().toUpperCase();
      body.amount_usd = parseFloat(document.getElementById("s-amount").value) || 0;
      body.shares = parseFloat(document.getElementById("s-shares").value) || 0;
    } else {
      const targets = {};
      document.getElementById("s-targets").value.split(",").forEach((pair) => {
        const [sym, w] = pair.split(":").map((x) => (x || "").trim());
        if (sym && w) targets[sym.toUpperCase()] = parseFloat(w);
      });
      body.targets = targets;
      body.threshold_usd = parseFloat(document.getElementById("s-threshold").value) || 25;
    }

    try {
      const r = await fetch("/api/strategies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.statusText);
      msg.textContent = "✓ Strategy added (disabled). Enable it to start trading.";
      msg.className = "trade-msg ok";
      e.target.reset();
      syncFields();
      loadStrategies();
    } catch (err) {
      msg.textContent = "✗ " + err.message;
      msg.className = "trade-msg err";
    }
  });

  // Row actions (toggle / run / delete)
  document.querySelector("#strat-table tbody").addEventListener("click", async (e) => {
    const t = e.target;
    if (t.dataset.toggle) {
      const enable = t.dataset.enabled !== "true";
      if (enable && !confirm("Enable this strategy? It will place real-mode orders automatically.")) return;
      await fetch(`/api/strategies/${t.dataset.toggle}/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: enable }),
      });
      loadStrategies();
    } else if (t.dataset.run) {
      t.disabled = true;
      await fetch(`/api/strategies/${t.dataset.run}/run`, { method: "POST" });
      loadStrategies();
    } else if (t.dataset.del) {
      if (!confirm("Delete this strategy?")) return;
      await fetch(`/api/strategies/${t.dataset.del}`, { method: "DELETE" });
      loadStrategies();
    }
  });
}

(async function main() {
  const s = await loadStatus();
  if (!s || !s.logged_in) return;

  tradingMode = s.trading_mode;
  const isPaper = tradingMode === "paper";
  const isLive = tradingMode === "live";
  const tradingOn = isPaper || isLive;

  document.getElementById("trade-panel").hidden = !tradingOn;
  document.getElementById("paper-portfolio").hidden = !isPaper;
  document.getElementById("live-banner").hidden = !isLive;
  document.getElementById("trading-disabled").hidden = tradingOn;
  document.getElementById("orders-panel").hidden = !isLive;
  document.getElementById("strategies-panel").hidden = !tradingOn;
  document.getElementById("trade-title").textContent = isLive
    ? "⚠️ Live Trade — real money"
    : "📝 Paper Trade — simulated";

  loadPortfolio();
  if (tradingOn) setupTradePanel();
  if (isPaper) loadPaper();
  if (isLive) {
    setupKillSwitch(s);
    setupOrders();
    loadOrders();
  }
  if (tradingOn) {
    setupStrategies(tradingMode);
    loadStrategies();
  }

  setInterval(async () => {
    const st = await loadStatus();
    if (st && st.logged_in) {
      loadPortfolio();
      if (isPaper) loadPaper();
      if (isLive) loadOrders();
      if (tradingOn) loadStrategies();
    }
  }, REFRESH_MS);
})();
