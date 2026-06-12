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
    return s.logged_in;
  } catch (e) {
    setStatus(e.message, "bad");
    return false;
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

(async function main() {
  const ok = await loadStatus();
  if (ok) {
    loadPortfolio();
    setInterval(async () => {
      if (await loadStatus()) loadPortfolio();
    }, REFRESH_MS);
  }
})();
