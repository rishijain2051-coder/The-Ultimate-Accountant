// The books, read-only: sealed snapshots from the office server, opened on this device with
// its own copy of the data key. The sealed files are kept in this browser's cache, so the last
// books still open offline. Amounts are paise, debit positive. Every year Tally holds is here.
import { api, dataKey, el, fetchKeys, purge, session, when } from "/app.js";
import { openUkeep } from "/keeper-crypto.js";
import { has } from "/caps.js";

const $ = (id) => document.getElementById(id);
const INR = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const money = (p) => (p ? `${INR.format(Math.abs(p) / 100)} ${p > 0 ? "Dr" : "Cr"}` : "–");
const enc = encodeURIComponent;

let index = null, books = null, offline = false, me = null;

// Which screens need which permission (any of the list). Only hides screens: the data key is one for all.
export const NEEDS = { dues: ["books.balances"], accounts: ["books.balances"], ledger: ["books.balances"],
  activity: ["books.sales"], voucher: ["books.sales"], alerts: ["books.alerts", "admin"],
  stock: ["books.balances", "books.sales"], item: ["books.sales"] };
export const allowed = (screen, caps) => !NEEDS[screen] || NEEDS[screen].some((k) => has(caps, k));
// Offline there is no session: the caps last seen on this device apply.
let seen = null;
const myCaps = () => {
  if (me?.user) return me.user.caps;
  if (!seen) { try { seen = JSON.parse(localStorage.getItem("ua-caps")); } catch {} }
  return seen || ["books.view", "books.balances", "books.sales", "books.alerts"];
};
const ok = (screen) => allowed(screen, myCaps());

// Sealed files: the copy kept in this browser opens at once; the fresh one from R2 follows,
// and replaces what is on screen only when it differs.
const INDEX = "office/index.ukeep", current = (cid) => `office/current/${cid}.ukeep`;
async function kept(path) {
  try {
    const r = await (await caches.open("ua-books")).match(`/sealed/${path}`);
    return r ? new Uint8Array(await r.arrayBuffer()) : null;
  } catch { return null; }
}
async function fresh(path) {
  const { url } = await api(`/api/books/url?path=${enc(path)}`);
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`R2 said ${r.status}`);
  const bytes = new Uint8Array(await r.arrayBuffer());
  await (await caches.open("ua-books")).put(`/sealed/${path}`, new Response(bytes));
  return bytes;
}
async function unseal(bytes) {
  const { plain } = await openUkeep(bytes, dataKey);
  const text = new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream("gzip")));
  return JSON.parse(await text.text());
}
const same = (a, b) => !!a && !!b && a.length === b.length && a.every((x, k) => x === b[k]);

// --- what the screens are built from ---

export function natureOf(b) {
  const g = Object.fromEntries(b.groups.map((x) => [x.name, x]));
  return (name) => { for (let n = name, i = 0; n && i < 20; n = g[n]?.parent, i++) if (g[n]) return g[n].nature; return "other"; };
}

// The financial year (April-March) a date falls in, by its first day.
export const fyOf = (iso) => { const y = +iso.slice(0, 4), m = +iso.slice(5, 7); return `${m >= 4 ? y : y - 1}-04-01`; };
const fyEnd = (fy) => `${+fy.slice(0, 4) + 1}-03-31`;
const fyLabel = (fy) => `${fy.slice(0, 4)}-${String((+fy.slice(2, 4) + 1) % 100).padStart(2, "0")}`;
export function years(b) {
  const out = [];
  for (let fy = fyOf(b.company.books_from || b.company.fy_from); fy <= b.company.fy_from; fy = `${+fy.slice(0, 4) + 1}-04-01`) out.push(fy);
  return out.reverse();
}
const revenue = (b) => { const n = natureOf(b); return (ledger) => ["income", "expenses"].includes(n(b.ledgers.find((l) => l.name === ledger)?.group)); };
export const total = (v) => v.lines.reduce((t, l) => t + (l.amount > 0 ? l.amount : 0), 0);

// A ledger's lines, date order.
function moves(b, ledger) {
  const rows = [];
  for (const v of b.vouchers) {
    const amount = v.lines.filter((l) => l.ledger === ledger).reduce((t, l) => t + l.amount, 0);
    if (amount) rows.push({ v, amount });
  }
  return rows.sort((a, c) => (a.v.date < c.v.date ? -1 : a.v.date > c.v.date ? 1 : 0));
}

// Balance at the end of a date. Income and expense ledgers start each year at nothing (the
// year's result goes to profit and loss); the rest carry on from the books' opening.
// Optional vouchers are left out, as Tally leaves them out of its balances.
export function balanceAt(b, ledger, date, rows = moves(b, ledger)) {
  const l = b.ledgers.find((x) => x.name === ledger);
  const rev = revenue(b)(ledger), from = rev ? fyOf(date) : "";
  let bal = !rev || from <= (b.company.books_from || "") ? l?.opening ?? 0 : 0;
  for (const r of rows) if (!r.v.optional && r.v.date <= date && r.v.date >= from) bal += r.amount;
  return bal;
}

export function statement(b, ledger, from, to) {
  const rows = moves(b, ledger), rev = revenue(b)(ledger);
  const before = new Date(Date.parse(from) - 864e5).toISOString().slice(0, 10);
  let bal = rev && fyOf(before) !== fyOf(from) ? 0 : balanceAt(b, ledger, before, rows), fy = fyOf(from);
  const opening = bal, out = [];
  for (const r of rows) {
    if (r.v.date < from || r.v.date > to) continue;
    if (rev && fyOf(r.v.date) !== fy) { fy = fyOf(r.v.date); bal = 0; }
    out.push({ ...r, balance: r.v.optional ? null : (bal += r.amount) });
  }
  return { opening, rows: out, closing: bal };
}

export const BUCKETS = ["Not due", "1–30 days", "31–60 days", "61–90 days", "Over 90 days"];
export function ageing(bills, today = new Date()) {
  const t = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  const out = { receivable: new Map(), payable: new Map() };
  for (const b of bills) {
    const late = b.due ? Math.floor((t - Date.parse(b.due)) / 864e5) : 0;
    const i = late <= 0 ? 0 : late <= 30 ? 1 : late <= 60 ? 2 : late <= 90 ? 3 : 4;
    const side = out[b.amount > 0 ? "receivable" : "payable"];
    const row = side.get(b.ledger) || side.set(b.ledger, { ledger: b.ledger, buckets: [0, 0, 0, 0, 0], bills: [] }).get(b.ledger);
    row.buckets[i] += b.amount;
    row.bills.push({ ...b, late });
  }
  return out;
}

// Vouchers by the filters: dates, type, a party or ledger, amount between (rupees), text.
export function filterVouchers(b, f) {
  const text = (f.q || "").trim().toLowerCase(), who = (f.party || "").trim().toLowerCase();
  const item = (f.item || "").trim().toLowerCase();
  const min = f.min ? Math.round(+f.min * 100) : null, max = f.max ? Math.round(+f.max * 100) : null;
  return b.vouchers.filter((v) => (!f.from || v.date >= f.from) && (!f.to || v.date <= f.to)
    && (!f.type || v.type === f.type)
    && (!who || (v.party || "").toLowerCase().includes(who) || v.lines.some((l) => l.ledger.toLowerCase().includes(who)))
    && (min == null || total(v) >= min) && (max == null || total(v) <= max)
    && (!item || (v.items || []).some((i) => (i.item || "").toLowerCase() === item))
    && (!text || `${v.number} ${v.narration} ${v.party} ${(v.items || []).map((i) => i.item).join(" ")}`.toLowerCase().includes(text)));
}

// Stock items by name (all when no text).
export const stockMatching = (b, q) => (b.stock || []).filter((s) => !q || (s.name || "").toLowerCase().includes(q.trim().toLowerCase()));

// One stock item's lines in every voucher, every year, newest first. n is the quantity as a
// number (Tally sends "10 Nos"); amount < 0 is going out (sold), > 0 coming in (bought).
export function itemMoves(b, name) {
  const out = [];
  for (const v of b.vouchers) for (const i of v.items || []) if (i.item === name) out.push({ v, ...i, n: parseFloat(i.qty) || 0 });
  return out.sort((a, c) => (a.v.date < c.v.date ? 1 : a.v.date > c.v.date ? -1 : c.v.alter - a.v.alter));
}

// Search everything, every year: accounts and parties by name, stock items, and entries.
export function findAll(b, q) {
  const t = (q || "").trim().toLowerCase();
  if (!t) return { ledgers: [], stock: [], vouchers: [] };
  return { ledgers: b.ledgers.filter((l) => l.name.toLowerCase().includes(t)).sort((x, y) => Math.abs(y.closing) - Math.abs(x.closing)),
    stock: stockMatching(b, t),
    vouchers: filterVouchers(b, { q: t }).sort((x, y) => (x.date < y.date ? 1 : x.date > y.date ? -1 : y.alter - x.alter)) };
}

// --- words and numbers, as people say them ---

// ₹11,800 or ₹1,234.50: whole rupees unless there are paise.
export const rupees = (p) => {
  const r = Math.abs(p) / 100;
  return "₹" + new Intl.NumberFormat("en-IN", { minimumFractionDigits: r % 1 ? 2 : 0, maximumFractionDigits: 2 }).format(r);
};
const todayIso = () => new Date(Date.now() - new Date().getTimezoneOffset() * 6e4).toISOString().slice(0, 10);   // this device's day, not UTC's
// 21 Oct this year; 21 Oct 2024 for any other year, so lists across years can't be misread.
export const shortDay = (iso, today = todayIso()) => (iso ? new Date(iso + "T00:00:00").toLocaleDateString("en-IN",
  { day: "numeric", month: "short", year: iso.slice(0, 4) === today.slice(0, 4) ? undefined : "numeric" }) : "");
export function ago(iso, now = Date.now()) {
  const m = Math.round((now - Date.parse(iso)) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} hour${h > 1 ? "s" : ""} ago`;
  return `on ${new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}`;
}
const plural = (n, one, many = one + "s") => `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;

// The chain of groups a ledger sits in, by name: Tally's own names tell banks, parties, sales.
export function chainOf(b) {
  const g = Object.fromEntries(b.groups.map((x) => [x.name, x]));
  return (group) => { const out = []; for (let n = group, i = 0; n && i < 20; n = g[n]?.parent, i++) out.push(n); return out; };
}
const ledgerOf = (b, name) => b.ledgers.find((l) => l.name === name);
const isParty = (b, name) => chainOf(b)(ledgerOf(b, name)?.group).some((n) => n === "Sundry Debtors" || n === "Sundry Creditors");
const inGroups = (b, names) => { const ch = chainOf(b); return (l) => ch(l.group).some((n) => names.includes(n)); };

// --- small pieces, from the bahi-khata page (DESIGN.md) ---

function chev() {
  const t = document.createElement("template");   // one constant drawing, no data in it
  t.innerHTML = '<svg viewBox="0 0 7 12" class="chev" aria-hidden="true"><path d="M1 1l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  return t.content.firstChild;
}

// The pen's mark for a party: a ring when nothing is due, red when overdue.
const markFor = (late) => (late > 90 ? "serious" : late > 0 ? "look" : "ring");
const initials = (name) => (name || "").replace(/[^A-Za-z0-9 ]/g, " ").trim().split(/\s+/).filter(Boolean)
  .slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "·";
const mark = (kind, label, name = "") => el("span", { class: `mark ${kind}`, role: "img", "aria-label": label }, initials(name));
const MARK_WORDS = { ring: "Not due yet", look: "Overdue", serious: "Over 90 days late" };

// An entry line: mark, what it is (up to two lines), a quiet line beneath (date · detail), a
// second one when there is more (more), the amount, a chevron. late: the quiet line in red.
function entry({ href, markKind, date, what, detail, more: also, amount, note, cls = "", late = false }) {
  const sub = [date, detail].filter(Boolean).join(" · ");
  return el(href ? "a" : "div", { class: ["entry", markKind ? "" : "nomark", cls].filter(Boolean).join(" "), href: href ? `#${href}` : null },
    markKind ? mark(markKind, MARK_WORDS[markKind] || "", what) : null,
    el("span", { class: "what" }, el("b", {}, what), sub ? el("small", { class: late ? "late" : null }, sub) : null, also ? el("small", { class: "also" }, also) : null),
    amount != null ? el("span", { class: "amount" }, amount, note ? el("small", {}, note) : null) : el("span"),
    href ? chev() : el("span"));
}
const entries = (rows, none) => (rows.length ? el("div", { class: "entries" }, rows) : el("p", { class: "empty" }, none));
const line = ({ href, text, money, note, cls = "" }) => el(href ? "a" : "div", { class: `line ${cls}`, href: href ? `#${href}` : null },
  el("span", {}, text), el("span", { class: "money" }, money, note ? el("small", {}, note) : null));
const verdict = (text, lede) => [el("h1", { class: "verdict" }, text), lede ? el("p", { class: "lede" }, ...[].concat(lede)) : null];
const headline = (text) => el("h2", { class: "headline" }, text);
const whole = (p) => rupees(Math.round(p / 100) * 100);      // big figures: whole rupees, as payments apps show them
const dc = (p) => (p ? (p > 0 ? " Dr" : " Cr") : "");

// The address of a screen with some filters changed, and text sub-filters that set them.
const hashWith = (screen, f, change) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...f, ...change })) if (v) q.set(k, v);
  return `${screen}${q.size ? "?" + q : ""}`;
};
const filtersRow = (label, items) => el("nav", { class: "filters", "aria-label": label },
  items.filter(Boolean).map(([text, href, on]) => el("a", { href: `#${href}`, "aria-current": on ? "true" : null }, text)));
// Filters the person chose, each a chip that takes it off.
const inUse = (screen, f, list) => {
  const on = list.filter(([k]) => f[k]);
  return on.length ? el("nav", { class: "filters chosen", "aria-label": "Filters in use" },
    on.map(([k, say]) => el("a", { href: `#${hashWith(screen, f, { [k]: "", n: "" })}`, "aria-label": `Remove ${say(f[k])}` }, say(f[k]), el("span", { "aria-hidden": "true" }, "×")))) : null;
};

function find(screen, f, placeholder) {
  const input = el("input", { type: "search", value: f.q || "", placeholder, "aria-label": placeholder, enterkeyhint: "search", autocomplete: "off" });
  let t = null;
  input.addEventListener("input", () => {                       // as you type
    clearTimeout(t);
    t = setTimeout(() => { history.replaceState(null, "", `#${hashWith(screen, f, { q: input.value.trim(), n: "" })}`); route(true); }, 250);
  });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); });   // the keyboard goes; the list shows
  return el("div", { class: "find" }, input);
}
const more = (screen, f, shown, all) => (all > shown ? el("p", { class: "more-rows" }, el("span", { class: "t-quiet" }, `${shown} of ${all} shown. `),
  el("a", { class: "button", href: `#${hashWith(screen, f, { n: String((+f.n || 1) + 1) })}` }, "Show more")) : null);
const PAGE = 150;

// Periods people think in. The financial year runs April to March. All time comes first and is the default.
function periods() {
  const t = todayIso(), y = +t.slice(0, 4), m = +t.slice(5, 7);
  const lm = new Date(Date.UTC(y, m - 2, 1)), lmEnd = new Date(Date.UTC(y, m - 1, 0));
  const fy = fyOf(t), lfy = `${+fy.slice(0, 4) - 1}-04-01`;
  return [["All time", "", ""], ["This month", t.slice(0, 8) + "01", t], ["Last month", lm.toISOString().slice(0, 10), lmEnd.toISOString().slice(0, 10)],
    [`This year, ${fyLabel(fy)}`, fy, fyEnd(fy)], [`Last year, ${fyLabel(lfy)}`, lfy, fyEnd(lfy)]];
}
const daysLate = (due, t = todayIso()) => (due ? Math.round((Date.parse(t) - Date.parse(due)) / 864e5) : 0);
const newestFirst = (a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.alter - a.alter);

// --- screens ---

function summary() {
  const t = todayIso(), month = t.slice(0, 7);
  const cash = books.ledgers.filter(inGroups(books, ["Bank Accounts", "Cash-in-Hand", "Bank OD A/c"])).filter((l) => l.closing);
  const cashTotal = cash.reduce((s, l) => s + l.closing, 0);
  const sum = (xs) => xs.reduce((s, x) => s + x.amount, 0);
  const recv = books.bills.filter((x) => x.amount > 0), pay = books.bills.filter((x) => x.amount < 0);
  const lateRecv = recv.filter((x) => daysLate(x.due, t) > 0), latePay = pay.filter((x) => daysLate(x.due, t) > 0);
  const sales = new Set(books.ledgers.filter(inGroups(books, ["Sales Accounts"])).map((l) => l.name));
  const buys = new Set(books.ledgers.filter(inGroups(books, ["Purchase Accounts"])).map((l) => l.name));
  let sold = 0, bought = 0;
  const kinds = { s: new Map(), p: new Map() };                 // the voucher types that carry sales and purchases
  for (const v of books.vouchers) {
    if (v.optional) continue;
    const s = v.lines.some((l) => sales.has(l.ledger)), p = v.lines.some((l) => buys.has(l.ledger));
    if (s) kinds.s.set(v.type, (kinds.s.get(v.type) || 0) + 1);
    if (p) kinds.p.set(v.type, (kinds.p.get(v.type) || 0) + 1);
    if (!v.date?.startsWith(month)) continue;
    for (const l of v.lines) { if (sales.has(l.ledger)) sold -= l.amount; if (buys.has(l.ledger)) bought += l.amount; }
  }
  const top = (m) => [...m].sort((a, b) => b[1] - a[1])[0]?.[0] || "";
  const monthName = new Date(t + "T00:00:00").toLocaleDateString("en-IN", { month: "long" });
  const newest = books.vouchers.filter((v) => !v.optional).sort(newestFirst).slice(0, 5);
  const stock = books.stock || [];
  const half = (href, k, v, late) => el("a", { href: `#${href}` }, el("div", { class: "k" }, k), el("div", { class: "v" }, v),
    el("div", { class: `n${late ? " late" : ""}` }, late ? `${late} overdue` : "nothing overdue"));
  const tile = (href, k, v, n) => el("a", { class: "tile", href: `#${href}` }, el("div", { class: "k" }, k), el("div", { class: "v" }, v), n ? el("div", { class: "n" }, n) : null);
  const bal = ok("dues"), tx = ok("activity");      // balances and sales/purchases are separate permissions
  const thisMonth = { from: `${month}-01`, to: t };
  return [el("h1", { class: "verdict" }, books.company.name),
    el("a", { class: "search-link", href: "#find" }, searchIcon(), "Search parties, items, bills, entries"),
    bal ? el("div", { class: "balance" },
      half("dues", "You'll get", whole(sum(recv)), lateRecv.length ? whole(sum(lateRecv)) : null),
      half("dues?side=pay", "You'll give", whole(sum(pay)), latePay.length ? whole(sum(latePay)) : null)) : null,
    bal || tx ? el("div", { class: "tiles" },
      bal ? tile("accounts?group=cash", "Cash and bank", whole(cashTotal)) : null,
      stock.length && ok("stock") ? tile("stock", "Stock in hand", whole(stock.reduce((s, x) => s + x.closing_value, 0)), plural(stock.length, "item")) : null,
      tx ? tile(hashWith("activity", thisMonth, { type: top(kinds.s) }), `Sales, ${monthName}`, whole(sold)) : null,
      tx ? tile(hashWith("activity", thisMonth, { type: top(kinds.p) }), `Purchases, ${monthName}`, whole(bought)) : null) : null,
    tx ? [headline("Latest"),
      entries(newest.map((v) => txEntry(v)), "No entries yet."),
      el("p", {}, el("a", { class: "button wide", href: "#activity" }, "All transactions"))] : null];
}

// q: the search, so an entry found by one of its items says which.
function txEntry(v, q = "") {
  const named = v.party || v.narration, t = q.trim().toLowerCase();
  const hit = t && (v.items || []).find((i) => (i.item || "").toLowerCase().includes(t));
  return entry({ href: `voucher/${enc(v.id)}`, date: shortDay(v.date), what: named || `${v.type} ${v.number}`,
    detail: [named ? `${v.type} ${v.number}` : null, v.optional ? "optional, not counted" : null].filter(Boolean).join(" · "),
    more: hit ? `${hit.item}, ${hit.qty}` : null, amount: rupees(total(v)) });
}

// Outstanding: the parties, first and plainly. The ageing breakdown waits behind "By age".
function dues(_, f) {
  const side = f.side === "pay" ? "pay" : "receive", t = todayIso();
  const who = (f.q || "").trim().toLowerCase();
  const a = ageing(books.bills.filter((b) => (!who || b.ledger.toLowerCase().includes(who)) && (f.late !== "yes" || daysLate(b.due, t) > 0)));
  const parties = [...a[side === "pay" ? "payable" : "receivable"].values()];
  const sum = (r) => r.buckets.reduce((s, x) => s + x, 0);
  parties.sort((x, y) => Math.abs(sum(y)) - Math.abs(sum(x)));
  const totals = [0, 0, 0, 0, 0];
  for (const r of parties) r.buckets.forEach((x, i) => (totals[i] += x));
  const all = totals.reduce((s, x) => s + x, 0);
  return [...verdict(`${side === "pay" ? "You'll give" : "You'll get"} ${whole(all)}`,
    `${plural(parties.length, "party", "parties")}${f.late === "yes" ? " with overdue bills" : ""}${who ? ` matching “${f.q}”` : ""}, biggest first`),
    filtersRow("Show", [["To receive", hashWith("dues", f, { side: "" }), side === "receive"], ["To pay", hashWith("dues", f, { side: "pay" }), side === "pay"],
      ["Overdue only", hashWith("dues", f, { late: f.late === "yes" ? "" : "yes" }), f.late === "yes"]]),
    find("dues", f, "Find a party"),
    entries(parties.map((r) => {
      const late = Math.max(0, ...r.bills.map((b) => b.late));
      return entry({ href: `ledger/${enc(r.ledger)}`, markKind: markFor(late), what: r.ledger, amount: rupees(sum(r)),
        detail: late > 0 ? `${plural(late, "day")} late` : "not due yet", late: late > 0 });
    }), who ? "No one matches." : f.late === "yes" ? "Nothing is overdue." : side === "pay" ? "You owe nothing on bills." : "Nobody owes you on bills."),
    parties.length ? el("details", { class: "more" }, el("summary", {}, "By age"),
      el("div", { class: "ledger" },
        BUCKETS.map((b, i) => totals[i] ? line({ text: b, money: rupees(totals[i]), cls: i === 4 ? "t-serious" : i ? "t-look" : "" }) : null),
        line({ text: "In all", money: rupees(all), cls: "total" }))) : null];
}

// A party or an account: what it stands at, its open bills, then every entry, newest first.
// All time unless the person picks a year or dates.
function ledger(name, f) {
  const l = ledgerOf(books, name), party = isParty(books, name);
  const start = books.company.books_from || books.company.fy_from;
  const from = f.from || start, to = f.to || books.company.fy_to;
  const now = l ? l.closing : 0;
  const t = todayIso(), here = `ledger/${enc(name)}`;
  const bills = books.bills.filter((b) => b.ledger === name);
  const byNumber = new Map(books.vouchers.filter((v) => v.lines.some((x) => x.ledger === name)).map((v) => [v.number, v.id]));
  const s = statement(books, name, from, to);
  const check = to >= books.company.fy_to && from <= books.company.fy_from && l && s.closing !== l.closing
    ? el("p", { class: "notice serious" }, `Tally's balance is ${money(l.closing)}, but the entries here add up to ${money(s.closing)}. A full read on the office server will put this right.`) : null;
  const rows = [...s.rows].reverse(), shown = rows.slice(0, PAGE * (+f.n || 1));
  const ys = years(books);
  const late = bills.filter((b) => daysLate(b.due, t) > 0);
  return [el("p", { class: "eyebrow" }, l ? l.group : "Account"),
    ...verdict(name, null),
    el("div", { class: "vhead" }, el("div", { class: "k" }, party ? (now > 0 ? "Owes you" : now < 0 ? "You owe" : "Settled") : "Balance now"),
      el("div", { class: "v" }, `${rupees(now)}${party ? "" : dc(now)}`),
      bills.length ? el("div", { class: `k${late.length ? " late" : ""}` }, `${plural(bills.length, "open bill")}${late.length ? `, ${rupees(late.reduce((x, b) => x + b.amount, 0))} overdue` : ""}`) : null),
    bills.length ? [headline(`Open bills (${bills.length})`), entries(bills.map((b) => {
      const late = daysLate(b.due, t), id = byNumber.get(b.name);
      return entry({ href: id ? `voucher/${enc(id)}` : null, what: b.name, amount: rupees(b.amount), late: late > 0,
        detail: b.due ? (late > 0 ? `${plural(late, "day")} late, due ${shortDay(b.due)}` : `due ${shortDay(b.due)}`) : `raised ${shortDay(b.date)}` });
    }), "")] : null,
    headline("Entries, newest first"),
    check,
    filtersRow("Period", [["All time", here, !f.from && !f.to], ...ys.map((y) => [fyLabel(y), hashWith(here, {}, { from: y, to: fyEnd(y) }), f.from === y && f.to === fyEnd(y)])]),
    otherDates(here, f),
    el("div", { class: "entries" },
      entry({ what: f.to ? `Balance on ${shortDay(to)}` : "Balance now", amount: `${rupees(s.closing)}${dc(s.closing)}`, cls: "total" }),
      shown.map((r) => entry({ href: `voucher/${enc(r.v.id)}`, date: shortDay(r.v.date), what: r.v.party && r.v.party !== name ? r.v.party : `${r.v.type} ${r.v.number}`,
        detail: [r.v.party && r.v.party !== name ? `${r.v.type} ${r.v.number}` : null, r.v.narration, r.v.optional ? "optional, not counted" : null].filter(Boolean).join(" · "),
        amount: `${rupees(r.amount)}${dc(r.amount)}`, note: r.balance == null ? null : `bal ${rupees(r.balance)}${dc(r.balance)}` })),
      shown.length === rows.length ? entry({ what: `Opening balance, ${shortDay(from)}`, amount: `${rupees(s.opening)}${dc(s.opening)}`, cls: "total" }) : null),
    more(here, f, shown.length, rows.length)];
}

function otherDates(screen, f) {
  const picked = f.from && !years(books).includes(f.from);
  return el("details", { class: "more", open: picked || null }, el("summary", {}, picked ? `${shortDay(f.from)} to ${shortDay(f.to || books.company.fy_to)}` : "Other dates"),
    el("form", { class: "fields", onsubmit: (e) => { e.preventDefault(); const d = new FormData(e.target); location.hash = hashWith(screen, f, { from: d.get("from"), to: d.get("to"), n: "" }); } },
      el("label", { class: "label" }, "From", el("input", { type: "date", name: "from", value: f.from || "" })),
      el("label", { class: "label" }, "To", el("input", { type: "date", name: "to", value: f.to || "" })),
      el("div", { class: "acts" }, el("button", { class: "button primary" }, "Show these dates"))));
}

// A voucher as an invoice reads: who and how much, the items, then the accounts.
function voucher(id) {
  const v = books.vouchers.find((x) => x.id === id);
  if (!v) return verdict("That entry isn't in these books.", "It may have been deleted in Tally since.");
  const fy = fyOf(v.date), items = v.items || [];
  const dr = v.lines.filter((l) => l.amount > 0).reduce((s, l) => s + l.amount, 0), cr = -v.lines.filter((l) => l.amount < 0).reduce((s, l) => s + l.amount, 0);
  const rate = (r) => (/^[\d.]/.test(r || "") ? `₹${r}` : r);
  return [el("p", { class: "eyebrow" }, `${v.type} ${v.number}`),
    ...verdict(v.party || v.narration || `${v.type} ${v.number}`, ""),
    el("div", { class: "vhead" }, el("div", { class: "v" }, rupees(total(v))),
      el("div", { class: "k" }, new Date(v.date + "T00:00:00").toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" })),
      v.optional ? el("div", { class: "k" }, "Optional, so not counted in any balance") : null),
    v.narration && v.party ? el("p", { class: "notice" }, v.narration) : null,
    items.length ? [headline(`Items (${items.length})`), el("div", { class: "entries" }, items.map((i) => entry({
      href: ok("item") ? `item/${enc(i.item)}` : null, what: i.item, detail: [i.qty, i.rate ? `@ ${rate(i.rate)}` : null].filter(Boolean).join(" "),
      amount: rupees(i.amount) })))] : null,
    headline("Accounts"),
    el("div", { class: "entries" }, v.lines.map((l) => entry({ href: ok("ledger") ? `ledger/${enc(l.ledger)}?from=${fy}&to=${fyEnd(fy)}` : null,
      what: l.ledger, amount: `${rupees(l.amount)}${dc(l.amount)}` })),
    entry({ what: "Total", amount: `${rupees(dr)} Dr`, note: `${rupees(cr)} Cr`, cls: "total" }))];
}

// Transactions: every year unless a period is picked. Search, kind and period are each one tap.
function activity(_, f) {
  const vs = filterVouchers(books, f).sort(newestFirst);
  const counts = new Map();
  for (const v of books.vouchers) counts.set(v.type, (counts.get(v.type) || 0) + 1);
  const types = [...counts].sort((a, b) => b[1] - a[1]).map(([t]) => t);
  const kinds = [...new Set([...types.slice(0, 5), f.type].filter(Boolean))];
  const shown = vs.slice(0, PAGE * (+f.n || 1)), ps = periods();
  const p = ps.find(([, a, b]) => (f.from || "") === a && (f.to || "") === b);
  const when_ = p ? p[0] : `${shortDay(f.from) || "the start"} to ${shortDay(f.to) || "today"}`;
  const sum = vs.reduce((s, v) => s + total(v), 0);
  return [...verdict(f.type ? `${f.type}: ${vs.length ? whole(sum) : "nothing"}` : plural(vs.length, "entry", "entries"),
    `${f.type ? plural(vs.length, "entry", "entries") + ", " : ""}${f.type ? when_.toLowerCase() : when_}${f.q ? `, matching “${f.q}”` : ""}`),
    find("activity", f, "Search party, item, number, narration"),
    filtersRow("Period", [...ps.map(([text, a, b]) => [text, hashWith("activity", f, { from: a, to: b, n: "" }), p?.[0] === text]),
      p ? null : [`${shortDay(f.from) || "Start"} – ${shortDay(f.to) || "today"}`, hashWith("activity", f, {}), true]]),
    filtersRow("Kind", [["All kinds", hashWith("activity", f, { type: "", n: "" }), !f.type], ...kinds.map((t) => [t, hashWith("activity", f, { type: t, n: "" }), f.type === t])]),
    inUse("activity", f, [["item", (x) => `Item: ${x}`], ["party", (x) => `Party: ${x}`], ["min", (x) => `At least ₹${x}`], ["max", (x) => `At most ₹${x}`]]),
    el("details", { class: "more" }, el("summary", {}, "More filters"),
      el("form", { class: "fields", onsubmit: (e) => { e.preventDefault(); location.hash = hashWith("activity", f, { ...Object.fromEntries(new FormData(e.target)), n: "" }); } },
        el("label", { class: "label wide" }, "Party or account", el("input", { type: "text", name: "party", value: f.party || "",
          list: "parties", autocomplete: "off", placeholder: "Start typing a name" })),
        el("datalist", { id: "parties" }, ...books.ledgers.map((l) => el("option", { value: l.name }))),
        el("label", { class: "label" }, "At least ₹", el("input", { type: "number", name: "min", inputmode: "decimal", step: "0.01", value: f.min || "" })),
        el("label", { class: "label" }, "At most ₹", el("input", { type: "number", name: "max", inputmode: "decimal", step: "0.01", value: f.max || "" })),
        el("label", { class: "label" }, "From", el("input", { type: "date", name: "from", value: f.from || "" })),
        el("label", { class: "label" }, "To", el("input", { type: "date", name: "to", value: f.to || "" })),
        el("div", { class: "acts" }, el("button", { class: "button primary" }, "Show"),
          el("a", { class: "button", href: `#${hashWith("activity", f, { party: "", min: "", max: "", from: "", to: "", n: "" })}` }, "Clear")))),
    entries(shown.map((v) => txEntry(v, f.q)), Object.keys(f).length ? "Nothing matches. Take a filter off, or pick All time." : "No entries yet."),
    more("activity", f, shown.length, vs.length)];
}

// Stock: every item, biggest value first, and a search.
function stock(_, f) {
  const all = books.stock || [], found = stockMatching(books, f.q || "").sort((a, b) => b.closing_value - a.closing_value);
  return [...verdict(`Stock in hand: ${whole(all.reduce((s, x) => s + x.closing_value, 0))}`,
    f.q ? `${plural(found.length, "item")} matching “${f.q}”` : `${plural(all.length, "item")}, biggest value first`),
    find("stock", f, "Find a stock item"),
    entries(found.map((s) => entry({ href: ok("item") ? `item/${enc(s.name)}` : null, what: s.name,
      detail: [s.closing_qty || `0 ${s.unit}`, s.group].filter(Boolean).join(" · "), amount: rupees(s.closing_value) })),
    all.length ? "No stock item matches." : "These books keep no stock items.")];
}

// One stock item: what is in hand, and every line of it in every year, newest first.
function item(name, f) {
  const s = (books.stock || []).find((x) => x.name === name);
  const rows = itemMoves(books, name), sold = rows.filter((r) => r.amount < 0), bought = rows.filter((r) => r.amount > 0);
  const show = f.side === "sold" ? sold : f.side === "bought" ? bought : rows, shown = show.slice(0, PAGE * (+f.n || 1));
  const unit = s?.unit || (rows[0]?.qty || "").replace(/^[\d.,\s-]+/, "");
  const qty = (xs) => `${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 }).format(xs.reduce((t, r) => t + Math.abs(r.n), 0))} ${unit}`.trim();
  const amt = (xs) => whole(xs.reduce((t, r) => t + Math.abs(r.amount), 0));
  const here = `item/${enc(name)}`;
  const tile = (k, v, n, href) => el(href ? "a" : "div", { class: "tile", href: href ? `#${href}` : null }, el("div", { class: "k" }, k), el("div", { class: "v" }, v), n ? el("div", { class: "n" }, n) : null);
  return [el("p", { class: "eyebrow" }, s?.group ? `Stock item · ${s.group}` : "Stock item"),
    ...verdict(name, null),
    el("div", { class: "tiles" },
      tile("In stock", s ? s.closing_qty || `0 ${unit}` : "–", s ? `worth ${whole(s.closing_value)}` : "not in Tally's list now"),
      tile("Sold, all years", qty(sold), `${amt(sold)} in ${plural(sold.length, "entry", "entries")}`, hashWith(here, {}, { side: "sold" })),
      tile("Bought, all years", qty(bought), `${amt(bought)} in ${plural(bought.length, "entry", "entries")}`, hashWith(here, {}, { side: "bought" }))),
    headline("Entries, newest first"),
    filtersRow("Show", [["All", here, !f.side], ["Sold", hashWith(here, {}, { side: "sold" }), f.side === "sold"], ["Bought", hashWith(here, {}, { side: "bought" }), f.side === "bought"]]),
    entries(shown.map((r) => entry({ href: `voucher/${enc(r.v.id)}`, date: shortDay(r.v.date), what: r.v.party || `${r.v.type} ${r.v.number}`,
      detail: `${r.v.type} ${r.v.number}${r.v.optional ? " · optional" : ""}`, more: `${r.qty}${r.rate ? ` @ ${/^[\d.]/.test(r.rate) ? "₹" : ""}${r.rate}` : ""}`,
      amount: rupees(r.amount), note: r.amount < 0 ? "sold" : "bought" })), "No entries for this item."),
    more(here, f, shown.length, show.length)];
}

// One search for everything, every year.
function findScreen(_, f) {
  const r = findAll(books, f.q), cut = 6;
  const section = (title, list, all, href) => (list.length ? [headline(`${title} (${all})`), el("div", { class: "entries" }, list),
    all > list.length ? el("p", {}, el("a", { class: "button wide", href: `#${href}` }, `See all ${all}`)) : null] : null);
  const ledgers = ok("ledger") ? r.ledgers : [], items = ok("stock") ? r.stock : [], vs = ok("activity") ? r.vouchers : [];
  return [...verdict("Search", f.q ? null : "Part of a name or number is enough. Every year is searched."),
    find("find", f, "Party, item, account, bill or entry no."),
    f.q && !ledgers.length && !items.length && !vs.length ? el("p", { class: "empty" }, `Nothing matches “${f.q}”.`) : null,
    section("Parties and accounts", ledgers.slice(0, cut).map((l) => entry({ href: `ledger/${enc(l.name)}`, what: l.name, detail: l.group,
      amount: `${rupees(l.closing)}${dc(l.closing)}` })), ledgers.length, hashWith("accounts", {}, { q: f.q })),
    section("Stock items", items.slice(0, cut).map((s) => entry({ href: ok("item") ? `item/${enc(s.name)}` : null, what: s.name, detail: s.closing_qty, amount: rupees(s.closing_value) })),
      items.length, hashWith("stock", {}, { q: f.q })),
    section("Entries", vs.slice(0, cut).map((v) => txEntry(v, f.q)), vs.length, hashWith("activity", {}, { q: f.q }))];
}

const SIDES = [["assets", "What you own"], ["liabilities", "What you owe"], ["income", "Income"], ["expenses", "Expenses"], ["other", "Other accounts"]];
const NATURAL = { assets: 1, expenses: 1, liabilities: -1, income: -1, other: 1 };

// Accounts: the list itself. A sentence about the books only when they don't add up.
function accounts(_, f) {
  const fy = f.year || books.company.fy_from, current = fy === books.company.fy_from, end = fyEnd(fy);
  const nature = natureOf(books), q = (f.q || "").trim().toLowerCase();
  if (f.stock) return stock(_, { q: f.q });                  // older address of the stock list
  const cashGroup = f.group === "cash", inCash = inGroups(books, ["Bank Accounts", "Cash-in-Hand", "Bank OD A/c"]);
  const all = books.ledgers.map((l) => ({ ...l, closing: current ? l.closing : balanceAt(books, l.name, end) })).filter((l) => l.closing);
  const ledgers = all.filter((l) => (!q || l.name.toLowerCase().includes(q)) && (!f.group || (cashGroup ? inCash(l) : l.group === f.group)));
  const dcNote = (l, n) => (Math.sign(l.closing) !== NATURAL[n] ? (l.closing > 0 ? "Dr" : "Cr") : null);
  const lineFor = (l) => line({ href: current ? `ledger/${enc(l.name)}` : `ledger/${enc(l.name)}?from=${fy}&to=${end}`, text: l.name, money: rupees(l.closing), note: dcNote(l, nature(l.group)) });
  const stockLines = (list) => list.map((s) => line({ href: ok("item") ? `item/${enc(s.name)}` : null, text: s.name, money: rupees(s.closing_value), note: s.closing_qty || s.unit }));
  const yearsRow = years(books).length > 1 ? filtersRow("Balances at the end of", years(books).map((y) => [y === books.company.fy_from ? `Now, ${fyLabel(y)}` : `End of ${fyLabel(y)}`,
    hashWith("accounts", f, { year: y === books.company.fy_from ? "" : y }), y === fy])) : null;
  const group = f.group ? inUse("accounts", f, [["group", (g) => (g === "cash" ? "Cash and bank" : g)]]) : null;
  if (f.group || q) {
    const sum = ledgers.reduce((s, l) => s + l.closing, 0);
    const st = q ? stockMatching(books, q) : [];
    return [...verdict(f.group ? `${cashGroup ? "Cash and bank" : f.group}: ${rupees(sum)}` : `Accounts matching “${f.q}”`, current ? null : `At the end of ${fyLabel(fy)}`),
      find("accounts", f, "Find an account"), group, yearsRow,
      ledgers.length ? el("div", { class: "ledger" }, ledgers.sort((a, b) => Math.abs(b.closing) - Math.abs(a.closing)).map(lineFor)) : st.length ? null : el("p", { class: "empty" }, "No account matches."),
      st.length ? [headline("Stock items"), el("div", { class: "ledger" }, stockLines(st))] : null];
  }
  let dr = 0, cr = 0;
  for (const l of all) l.closing > 0 ? (dr += l.closing) : (cr += l.closing);
  const out = [];
  for (const [n, label] of SIDES) {
    const mine = ledgers.filter((l) => nature(l.group) === n);
    if (!mine.length) continue;
    const groups = new Map();
    for (const l of mine) groups.set(l.group, [...(groups.get(l.group) || []), l]);
    const totalOf = (ls) => ls.reduce((s, l) => s + l.closing, 0);
    out.push(headline(label), el("div", { class: "ledger" },
      [...groups].sort((a, b) => Math.abs(totalOf(b[1])) - Math.abs(totalOf(a[1])))
        .map(([g, ls]) => ls.length === 1 ? lineFor(ls[0]) : line({ href: hashWith("accounts", f, { group: g }), text: g, money: rupees(totalOf(ls)), note: plural(ls.length, "account") })),
      line({ text: "In all", money: rupees(totalOf(mine)), cls: "total" })));
  }
  const st = books.stock || [];
  if (st.length && ok("stock")) out.push(headline("Stock"), el("div", { class: "ledger" },
    line({ href: "stock", text: "Stock items", money: rupees(st.reduce((s, x) => s + x.closing_value, 0)), note: plural(st.length, "item") })));
  return [...verdict(current ? "Accounts" : `Accounts at the end of ${fyLabel(fy)}`, null),
    dr + cr !== 0 ? el("p", { class: "notice" }, `Debits and credits differ by ${rupees(dr + cr)}. A full read on the office server usually settles it.`) : null,
    find("accounts", f, "Find an account"), yearsRow, ...out];
}

// Notifications on this device. Admins also set the big-transaction amount here.
function alerts() {
  const box = el("div", {}, el("p", { class: "lede" }, "Looking…"));
  (async () => {
    const caps = myCaps();
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      box.replaceChildren(...verdict(/iPhone|iPad/.test(navigator.userAgent) ? "Add Keeper to your home screen first." : "This browser can't show alerts.",
        /iPhone|iPad/.test(navigator.userAgent) ? "On iPhone and iPad, alerts work only from the home screen: tap Share, then Add to Home Screen, and open Keeper from there." : null));
      return;
    }
    const reg = await Promise.race([navigator.serviceWorker.ready,
      new Promise((_, no) => setTimeout(() => no(new Error("The page's background helper didn't start. Reload the page and try again.")), 8000))]);
    let sub = await reg.pushManager.getSubscription();
    const say = el("p", { class: "lede", role: "status" });
    const choice = (name, what, detail) => el("label", { class: "choice" }, el("input", { type: "checkbox", name, checked: true }),
      el("span", {}, el("b", {}, what), el("small", {}, detail)));
    const form = el("form", { class: "entries" },
      has(caps, "books.alerts") ? choice("overdue", "Overdue bills", "When a bill passes its due date. Once for each bill.") : null,
      has(caps, "books.alerts") ? choice("big", "Big transactions", has(caps, "admin") ? "A new entry at or above the amount below." : "A new entry at or above the amount the admin set.") : null,
      has(caps, "admin") ? choice("admin", "For admins", "A device waiting for approval, a sync that stopped, an old Tally backup, the office server gone quiet.") : null);
    const save = async () => {
      try {
        if ((await Notification.requestPermission()) !== "granted") { say.textContent = "Alerts are blocked for this site in the browser's settings. Allow them there, then try again."; return; }
        const { key } = await api("/api/push/key");
        sub = sub || await reg.pushManager.subscribe({ userVisibleOnly: true,
          applicationServerKey: Uint8Array.from(atob(key.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0)) });
        const j = sub.toJSON(), wants = Object.fromEntries([...form.querySelectorAll("input")].map((i) => [i.name, i.checked]));
        await api("/api/push/subscribe", { method: "POST", body: { endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth, wants } });
        paint("Saved. This device will be told.");
      } catch (e) { say.textContent = `Alerts couldn't be turned on: ${e.message}`; }
    };
    form.addEventListener("change", () => { if (sub) save(); });
    let threshold = null;
    if (has(caps, "admin")) {
      const st = await api("/api/admin/settings").catch(() => ({}));
      const amt = el("input", { type: "number", min: "1", step: "1", inputmode: "numeric", value: Math.round((st.big_threshold_paise || 10000000) / 100) });
      const ok = el("p", { class: "t-quiet" });
      threshold = [headline("What counts as big"),
        el("form", { class: "fields", onsubmit: async (e) => {
          e.preventDefault();
          try { await api("/api/admin/settings", { method: "PUT", body: { big_threshold_paise: Math.round(+amt.value * 100) } }); ok.textContent = "Saved. It applies from the office server's next sync."; }
          catch (err) { ok.textContent = err.message; }
        } }, el("label", { class: "label" }, "An entry of at least ₹", amt), el("div", { class: "acts" }, el("button", { class: "button" }, "Save the amount"))), ok];
    }
    const paint = (message) => {
      say.textContent = message || "";
      box.replaceChildren(...verdict(sub ? "Alerts are on for this device." : "Alerts are off for this device.",
        "Keeper can tell this device about these, even when it is closed."),
        form,
        el("div", { class: "acts-row" },
          el("button", { class: "button primary", onclick: save }, sub ? "Save these choices" : "Turn alerts on"),
          sub ? el("button", { class: "button", onclick: async () => {
            try { await api("/api/push/test", { method: "POST" }); say.textContent = "Sent. It should appear in a moment."; } catch (e) { say.textContent = e.message; }
          } }, "Send a test") : null,
          sub ? el("button", { class: "button", onclick: async () => {
            await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } }).catch(() => {});
            await sub.unsubscribe(); sub = null; paint("Alerts are off. Turn them on again any time.");
          } }, "Turn alerts off") : null),
        say, ...(threshold || []));
    };
    paint();
  })().catch((e) => box.replaceChildren(el("p", { class: "notice serious" }, e.message)));
  return [box];
}

const TABS = [["summary", "Summary"], ["dues", "Dues"], ["activity", "Transactions"], ["stock", "Stock"], ["accounts", "Accounts"], ["alerts", "Alerts"]];
const SCREENS = { summary, dues, activity, stock, item, find: findScreen, accounts, ledger, voucher, alerts };
// Older addresses (notifications, saved links) still land on the right screen.
const ALIASES = { tb: "accounts", outstanding: "dues", vouchers: "activity", daybook: "activity", search: "find", notify: "alerts", items: "stock" };
const PARENT = { ledger: "accounts", voucher: "activity", item: "stock", find: "summary" };

const ICONS = {
  summary: '<path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5h-5v5H5a1 1 0 0 1-1-1z"/>',
  dues: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  activity: '<path d="M5 7h14M5 12h14M5 17h9"/>',
  stock: '<path d="M12 3.5 20 8v8l-8 4.5L4 16V8z"/><path d="M4 8l8 4.5L20 8M12 12.5v8"/>',
  accounts: '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 10h16M10 10v9"/>',
  alerts: '<path d="M6.5 16V11a5.5 5.5 0 0 1 11 0v5l1.5 2h-14zM10 20.5h4"/>',
  find: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/>',
};
function icon(name) {
  const t = document.createElement("template");    // our own constant drawings
  t.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
  return t.content.firstChild;
}
const searchIcon = () => icon("find");
function tabs() {
  const t = todayIso(), late = books ? books.bills.filter((b) => b.amount > 0 && daysLate(b.due, t) > 0) : [];
  const parties = new Set(late.map((b) => b.ledger)).size;
  $("tabs").replaceChildren(...TABS.filter(([to]) => ok(to)).map(([to, text]) => el("a", { href: `#${to}`, "data-to": to }, icon(to), el("span", {}, text),
    to === "dues" && parties ? el("span", { class: "count", "aria-label": `${parties} overdue` }, String(parties)) : null)));
  const bell = $("bell");                         // on a phone, Alerts sits in the top bar
  if (bell) { bell.hidden = !ok("alerts"); if (!bell.firstChild) bell.append(icon("alerts")); }
}

// Tally's way, on a desktop: the yellow bar sits on a row and moves with the arrows; Enter opens
// it, Esc goes back; a letter goes to a part; F2 the period, F3 the company. The same keys are
// listed down the right, as Tally lists its own.
const KEYS = [["S", "Summary", "summary"], ["D", "Dues", "dues"], ["T", "Transactions", "activity"], ["I", "Stock items", "stock"],
  ["A", "Accounts", "accounts"], ["L", "Alerts", "alerts"]];
function keysPanel() {
  const go = (fn) => (e) => { e.preventDefault(); fn(); };
  $("keys").replaceChildren(el("h2", {}, "Keys"),
    ...KEYS.filter(([, , to]) => ok(to)).map(([k, text, to]) => el("button", { onclick: go(() => (location.hash = to)) }, el("kbd", {}, k), text)),
    el("button", { onclick: go(period) }, el("kbd", {}, "F2"), "Period"),
    el("button", { onclick: go(() => $("company").focus()) }, el("kbd", {}, "F3"), "Company"),
    el("button", { onclick: go(findKey) }, el("kbd", {}, "/"), "Search"),
    el("button", { onclick: go(() => history.back()) }, el("kbd", {}, "Esc"), "Back"),
    el("p", {}, "↑ ↓ move the yellow bar, Enter opens."));
}
function findKey() {
  const i = document.querySelector(".find input");
  if (i) i.focus(); else location.hash = "find";
}
function period() {
  const chips = document.querySelector(".filters[aria-label=Period] a");
  if (chips) { chips.focus(); return; }
  const more = document.querySelector("details.more");
  if (more) { more.open = true; more.querySelector("input[type=date], select, input")?.focus(); }
}
const rows = () => [...$("view").querySelectorAll("a.entry, .ledger a.line, a.tile, .balance a")];
function moveBar(step) {
  const list = rows();
  if (!list.length) return;
  const at = list.findIndex((r) => r.classList.contains("on"));
  const next = list[Math.max(0, Math.min(list.length - 1, at < 0 ? (step > 0 ? 0 : list.length - 1) : at + step))];
  list.forEach((r) => r.classList.toggle("on", r === next));
  next.focus({ preventScroll: true });
  next.scrollIntoView({ block: "nearest" });
}
function onKey(e) {
  if (e.altKey || e.ctrlKey || e.metaKey) return;
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || "");
  if (e.key === "Escape") { if (typing) document.activeElement.blur(); else if (location.hash.replace("#", "").split("?")[0] !== "summary") history.back(); e.preventDefault(); return; }
  if (e.key === "F2") { e.preventDefault(); period(); return; }
  if (e.key === "F3") { e.preventDefault(); $("company").focus(); return; }
  if (typing) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); moveBar(e.key === "ArrowDown" ? 1 : -1); return; }
  if (e.key === "Enter") { const on = $("view").querySelector(".on"); if (on) { e.preventDefault(); on.click(); } return; }
  if (e.key === "/") { e.preventDefault(); findKey(); return; }
  const k = KEYS.find(([key, , to]) => key.toLowerCase() === e.key.toLowerCase() && ok(to));
  if (k) { e.preventDefault(); location.hash = k[2]; }
}

// Where each screen was scrolled to, so the back gesture returns to the same row. A tap on a
// link starts the screen it opens at the top.
const scrolled = new Map();
let tapped = false;
let moved = false;                  // gone somewhere inside Keeper, so Back has a page of ours to return to
function route(keepScroll = false) {
  if (!books) return;
  const [path, query = ""] = location.hash.slice(1).split("?");
  let [name, arg] = (path || "summary").split("/");
  name = ALIASES[name] || name;
  if (!ok(name)) name = "summary";
  const screen = SCREENS[name] || summary;
  const tab = SCREENS[name] ? PARENT[name] || name : "summary";
  for (const a of $("tabs").children) a.setAttribute("aria-current", a.dataset.to === tab ? "true" : "false");
  $("bell")?.setAttribute("aria-current", tab === "alerts" ? "true" : "false");
  $("back").hidden = !PARENT[name] && !query;      // a detail screen, or a filtered list
  $("back").onclick = () => (moved ? history.back() : (location.hash = PARENT[name] || name));
  const f = Object.fromEntries(new URLSearchParams(query));
  const caret = document.activeElement?.closest?.(".find") ? document.activeElement.selectionStart : null;
  const view = $("view");
  view.replaceChildren(...screen(arg ? decodeURIComponent(arg) : undefined, f).flat(3).filter(Boolean));
  if (caret != null || (name === "find" && !f.q && !keepScroll)) { const i = view.querySelector(".find input"); i?.focus(); if (caret != null) i?.setSelectionRange(caret, caret); }
  if (!keepScroll) window.scrollTo(0, !tapped && scrolled.get(location.hash) || 0);
  tapped = false;
}

function freshness() {
  const s = $("state");
  if (!books) return;
  // Unchanged books aren't sent again; the index says when Tally was last checked.
  const row = index?.companies?.find((c) => c.cid === books.company.cid);
  const at = row?.read_at && Date.parse(row.read_at) > Date.parse(books.read_at) ? row.read_at : books.read_at;
  const mins = (Date.now() - Date.parse(at)) / 60000;
  s.className = "fresh" + (offline || mins > 180 ? " stale" : "");
  s.textContent = offline ? `Offline: the copy read from Tally ${ago(at)}.` : `Read from Tally ${ago(at)}.`;
}

let onScreen = null;                // the sealed bytes of the books shown, to skip a re-draw
async function show(bytes, again = false) {
  books = await unseal(bytes);
  onScreen = bytes;
  $("company-name").textContent = books.company.name;
  document.title = `${books.company.name} · Keeper`;
  tabs();
  freshness();
  route(again);
}

function companies(list, cid) {
  $("company").replaceChildren(...list.map((c) => el("option", { value: c.cid, selected: c.cid === cid }, c.name)));
  $("picker").hidden = false;
}

function choose() {
  let saved = null;
  try { saved = localStorage.getItem("ua-company"); } catch {}
  const asked = new URLSearchParams(location.search).get("company");        // from a notification
  return index.companies.find((c) => c.cid === asked) || index.companies.find((c) => c.cid === saved) || index.companies[0];
}

async function pick(cid) {
  try { localStorage.setItem("ua-company", cid); } catch {}
  const here = await kept(current(cid));
  if (here) await show(here).catch(() => {});
  else { $("state").className = "fresh"; $("state").textContent = "Opening the books…"; }
  try {
    const bytes = await fresh(current(cid));
    offline = false;
    if (!same(bytes, onScreen)) await show(bytes, !!here);
    else freshness();
  } catch (e) {
    if (!here) throw e;
    offline = true;
    freshness();
  }
}

async function start() {
  try {
    let saved = null;
    try { saved = localStorage.getItem("ua-company"); } catch {}
    // All at once: who this is, and the newest files. Meanwhile the kept copy goes on screen.
    // undefined: offline (no status), open the kept copy; an HTTP error is "not allowed"
    const asking = session().catch((e) => (typeof e.status === "number" ? { denied: true } : undefined));
    const freshIndex = fresh(INDEX).catch(() => null);
    const freshBooks = saved ? fresh(current(saved)).catch(() => null) : Promise.resolve(null);
    const keptIndex = await kept(INDEX);
    if (keptIndex) {
      try {
        index = await unseal(keptIndex);
        const c = choose();
        const b = await kept(current(c.cid));
        if (b) { companies(index.companies, c.cid); await show(b); $("state").textContent = "Checking for newer books…"; }
      } catch { index = null; }
    }
    const s = await asking;
    if (s === null || s?.denied) await purge();
    if (s === null || s?.denied) { location.href = "/"; return; }
    if (s && (!s.user || !has(s.user.caps, "books.view") || s.device?.status !== "approved")) { location.href = "/"; return; }
    me = s;
    if (s?.user) { try { localStorage.setItem("ua-caps", JSON.stringify(s.user.caps)); } catch {} }
    if (s) await fetchKeys().catch(() => {});
    const iB = await freshIndex;
    if (!iB && !index) throw new Error("the books aren't on this device yet, and the internet can't be reached");
    if (iB) index = await unseal(iB);
    if (!index.companies.length) { fail(new Error("the office server hasn't sent any company yet")); return; }
    const c = choose();
    companies(index.companies, c.cid);
    $("company").onchange = (e) => {
      // a party, entry or item belongs to the company it came from: the other one opens at its summary
      if (/^#(ledger|voucher|item)\//.test(location.hash)) history.replaceState(null, "", "#summary");
      scrolled.clear(); tapped = true;             // another company's books start at the top
      pick(e.target.value).catch(fail);
    };
    const bB = c.cid === saved ? await freshBooks : null;
    if (bB) {
      offline = false;
      try { localStorage.setItem("ua-company", c.cid); } catch {}
      if (!same(bB, onScreen)) await show(bB, !!onScreen);
      else freshness();
    } else await pick(c.cid);
    if (!iB) { offline = true; freshness(); }
    setInterval(freshness, 60000);
  } catch (e) { fail(e); }
}

const fail = (e) => {
  $("state").textContent = "";
  $("view").replaceChildren(...verdict("The books couldn't be opened.", `${e.message.charAt(0).toUpperCase()}${e.message.slice(1)}. Check the connection and reload; the last copy opens offline once it has opened here once.`));
};

if (typeof navigator !== "undefined" && "serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

if (typeof document !== "undefined" && $("tabs")) {
  tabs();
  keysPanel();
  addEventListener("keydown", onKey);
  addEventListener("hashchange", () => { moved = true; route(); });
  addEventListener("click", (e) => { if (e.target.closest?.("a[href^='#']")) tapped = true; }, true);
  let st = null;
  addEventListener("scroll", () => { clearTimeout(st); st = setTimeout(() => scrolled.set(location.hash, scrollY), 120); }, { passive: true });
  start();
}
