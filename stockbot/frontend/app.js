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

// ── paper trading ───────────────────────────────────────────────────────────
let paperEnabled = false;
let currentSide = "buy";

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

function setupTradePanel() {
  // Buy/Sell segmented control
  document.querySelectorAll("#side-seg button").forEach((b) => {
    b.addEventListener("click", () => {
      currentSide = b.dataset.side;
      document.querySelectorAll("#side-seg button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      document.getElementById("t-submit").textContent =
        (currentSide === "buy" ? "Buy" : "Sell") + " (paper)";
    });
  });

  // Show limit price field only for limit orders
  document.getElementById("t-type").addEventListener("change", (e) => {
    document.getElementById("t-limit").hidden = e.target.value !== "limit";
  });

  // Submit order
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

    const btn = document.getElementById("t-submit");
    btn.disabled = true;
    msg.textContent = "Placing…";
    msg.className = "trade-msg";
    try {
      const r = await fetch("/api/order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || r.statusText);
      const t = data.trade;
      msg.textContent = `✓ ${t.side.toUpperCase()} ${t.quantity} ${t.symbol} @ ${fmtUSD(t.price)} (paper)`;
      msg.className = "trade-msg ok";
      document.getElementById("t-qty").value = "";
      loadPaper();
    } catch (err) {
      msg.textContent = "✗ " + err.message;
      msg.className = "trade-msg err";
    } finally {
      btn.disabled = false;
    }
  });

  // Reset
  document.getElementById("p-reset").addEventListener("click", async () => {
    if (!confirm("Reset paper account to its starting cash? This clears all paper positions and trades.")) return;
    await fetch("/api/paper/reset", { method: "POST" });
    loadPaper();
    const msg = document.getElementById("trade-msg");
    msg.textContent = "Paper account reset.";
    msg.className = "trade-msg ok";
  });
}

(async function main() {
  const s = await loadStatus();
  if (!s || !s.logged_in) return;

  paperEnabled = s.trading_mode === "paper";
  document.getElementById("paper-panel").hidden = !paperEnabled;
  document.getElementById("paper-disabled").hidden = paperEnabled;

  loadPortfolio();
  if (paperEnabled) {
    setupTradePanel();
    loadPaper();
  }

  setInterval(async () => {
    const st = await loadStatus();
    if (st && st.logged_in) {
      loadPortfolio();
      if (paperEnabled) loadPaper();
    }
  }, REFRESH_MS);
})();
