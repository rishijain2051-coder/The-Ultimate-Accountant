"""The Keeper's page on the office server. Kept as Python, not a .html file: the packaged
app ships app/ as modules, and build.spec copies only the .html files it names."""

HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Keeper</title>
<link rel="icon" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNTYgMjU2Ij48dGl0bGU+S2VlcGVyPC90aXRsZT48cmVjdCB3aWR0aD0iMjU2IiBoZWlnaHQ9IjI1NiIgcng9IjU4IiBmaWxsPSIjMTExMTExIi8+PGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMTIuOCAxNikgc2NhbGUoLjkpIj48cGF0aCBmaWxsPSJub25lIiBzdHJva2U9IiNCM0IzQjMiIHN0cm9rZS13aWR0aD0iMzQiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgZD0iTTEwNCAxMzAgMTUwIDg0Ii8+PGNpcmNsZSBmaWxsPSIjQjNCM0IzIiBjeD0iMTcwIiBjeT0iNjQiIHI9IjMzIi8+PGcgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjZmZmZmZmIiBzdHJva2Utd2lkdGg9IjQwIiBzdHJva2UtbGluZWNhcD0icm91bmQiPjxwYXRoIGQ9Ik04MCA1OHYxNDIiLz48cGF0aCBkPSJNMTIyIDE0Mmw1NiA1OCIvPjwvZz48L2c+PC9zdmc+" type="image/svg+xml">
<style>
  /* Keeper (brand/keeper): black, white and grey, and Tally's yellow bar on the row you are on. */
  :root { --bg:#f4f4f5; --card:#fff; --ink:#111; --ink-2:#3f3f46; --ink-3:#66666e; --line:#e4e4e7; --bar:#ffe27a;
          --bad:#c62828; --bad-soft:#fdecea; color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:400 15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
         font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased; }
  ::selection { background:var(--bar); }
  :focus-visible { outline:2px solid var(--ink); outline-offset:2px; }
  .cover { position:sticky; top:0; z-index:5; display:flex; align-items:center; justify-content:space-between; gap:12px;
           padding:10px 20px; background:#fff; border-bottom:1px solid var(--line); }
  .wordmark { display:flex; align-items:center; gap:8px; font-weight:700; font-size:17px; letter-spacing:-.01em; color:var(--ink); }
  .wordmark img { width:28px; height:28px; border-radius:7px; }
  .cover a { color:var(--ink-2); font-size:.875rem; text-decoration:none; padding:6px 12px; border-radius:999px; background:var(--bg); }
  main { max-width:760px; margin:0 auto; padding:28px 20px 56px; }
  a { color:var(--ink); }
  h1 { font-size:1.75rem; line-height:1.2; letter-spacing:-.015em; margin:0 0 6px; display:flex; align-items:center; gap:12px; }
  .lede { font-size:1rem; color:var(--ink-2); margin:0 0 4px; }
  .quiet { color:var(--ink-3); font-size:.8125rem; max-width:70ch; }
  h2 { font-size:1rem; margin:30px 2px 10px; display:flex; align-items:center; gap:10px; }
  .dot { width:11px; height:11px; border-radius:50%; background:var(--ink); flex:none; }
  .dot.busy { background:var(--ink-3); animation:pulse 1.2s ease-in-out infinite; }
  .dot.warn { background:transparent; box-shadow:inset 0 0 0 2px var(--ink-3); } .dot.bad { background:var(--bad); }
  @keyframes pulse { 50% { opacity:.35; } }
  .acts { display:flex; flex-wrap:wrap; gap:10px; margin:18px 0 8px; }
  button { font:inherit; font-weight:600; padding:11px 18px; border-radius:12px; border:1px solid var(--line); cursor:pointer; background:#fff; color:var(--ink); }
  button:hover { background:var(--bg); }
  button.primary { background:var(--ink); border-color:var(--ink); color:#fff; }
  button.primary:hover { background:#000; }
  button:active { transform:scale(.98); } button:disabled { opacity:.45; cursor:not-allowed; }
  details { margin-top:14px; } summary { cursor:pointer; font-weight:600; }
  details pre { white-space:pre-wrap; font:13px/1.45 ui-monospace,Consolas,monospace; background:#fff; border:1px solid var(--line); border-radius:10px; padding:10px 12px; color:var(--ink-2); }
  .rows { background:#fff; border:1px solid var(--line); border-radius:16px; overflow:hidden; }
  .row { display:flex; gap:14px; align-items:center; padding:12px 14px; position:relative; }
  .row + .row::before { content:""; position:absolute; top:0; left:14px; right:0; height:1px; background:var(--line); }
  
  .row .main { flex:1; min-width:0; } .row .name { font-weight:600; }
  .row .meta { font-size:.8125rem; color:var(--ink-3); }
  .row .side { text-align:right; font-size:.8125rem; color:var(--ink-3); white-space:nowrap; }
  label { display:block; font-size:.8125rem; font-weight:600; color:var(--ink-3); margin:14px 0 4px; }
  input { font:inherit; width:100%; max-width:480px; padding:11px 14px; border-radius:12px; border:1px solid var(--line); background:#fff; color:var(--ink); }
  input:focus { outline:2px solid var(--ink); border-color:transparent; }
  .meta.warn { color:var(--bad); }
  .msg { margin-top:8px; } .msg.ok { color:var(--ink); } .msg.bad { color:var(--bad); }
  @media (max-width: 700px) { main { padding:20px 16px 48px; } .cover { padding:10px 16px; } h1 { font-size:1.5rem; } }
  @media (prefers-reduced-motion: reduce) { .dot.busy { animation:none; } button:active { transform:none; } }
</style></head>
<body>
<header class="cover"><span class="wordmark"><img src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNTYgMjU2Ij48dGl0bGU+S2VlcGVyPC90aXRsZT48cmVjdCB3aWR0aD0iMjU2IiBoZWlnaHQ9IjI1NiIgcng9IjU4IiBmaWxsPSIjMTExMTExIi8+PGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMTIuOCAxNikgc2NhbGUoLjkpIj48cGF0aCBmaWxsPSJub25lIiBzdHJva2U9IiNCM0IzQjMiIHN0cm9rZS13aWR0aD0iMzQiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgZD0iTTEwNCAxMzAgMTUwIDg0Ii8+PGNpcmNsZSBmaWxsPSIjQjNCM0IzIiBjeD0iMTcwIiBjeT0iNjQiIHI9IjMzIi8+PGcgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjZmZmZmZmIiBzdHJva2Utd2lkdGg9IjQwIiBzdHJva2UtbGluZWNhcD0icm91bmQiPjxwYXRoIGQ9Ik04MCA1OHYxNDIiLz48cGF0aCBkPSJNMTIyIDE0Mmw1NiA1OCIvPjwvZz48L2c+PC9zdmc+" alt="">Keeper</span><a href="/">Back to Ultimate Accountant</a></header>
<main>
<h1><span class="dot" id="dot"></span><span id="headline">Looking…</span></h1>
<p class="lede" id="sub"></p>
<p class="quiet">The office's books, read from Tally and sent, locked, to the phones and laptops you have approved. Tally is only ever read, never changed.</p>
<div>
  <details id="why" hidden><summary>Details</summary><pre id="detail"></pre></details>
  <div class="acts">
    <button id="now" class="primary">Check for changes</button>
    <button id="full">Read everything again</button>
  </div>
  <p class="quiet">Check for changes takes seconds: it asks Tally only for what has changed. Read everything again goes through every year,
    and notices deleted entries; it also happens by itself, every
    <select id="every" aria-label="How often"><option value="1">hour</option><option value="2">2 hours</option><option value="4">4 hours</option>
      <option value="6">6 hours</option><option value="12">12 hours</option></select>, even while the app is closed.</p>
  <p class="msg" id="enote"></p>
</div>

<h2>Companies</h2>
<div class="rows" id="rows"></div>

<h2>Who has access</h2>
<div class="rows" id="people"></div>

<h2><span class="dot" id="bdot"></span><span>Tally's backups: <span id="bhead">looking…</span></span></h2>
<p class="lede" id="bsub"></p>
<div>
  <details id="where"><summary>Where Tally keeps its backups</summary>
    <p class="quiet">In Tally, it's the Backup Data Path, under Data Path Configuration (Alt+Y). In TallyPrime 7, turn on a daily
      scheduled backup there too, so there's always a fresh one; the Keeper sends each new one, locked, and keeps it for 400 days.</p>
    <label for="bfolder">Folder on this PC</label>
    <input id="bfolder" placeholder="C:\Users\Public\TallyPrime\Backup">
    <div class="acts"><button id="bsave" class="primary">Save the folder</button></div>
    <p class="msg" id="bnote"></p>
  </details>
  <details id="restore"><summary>Restore a backup…</summary>
    <p class="quiet">Choose a backup, and an empty folder for it. It is downloaded, unlocked and checked; then in Tally choose
      Data &gt; Restore and pick that folder. Nothing is ever restored over Tally's own data.</p>
    <div class="rows" id="brows"></div>
    <div id="rbox" hidden>
      <label for="rfolder">Empty folder for <b id="rname"></b></label>
      <input id="rfolder" placeholder="D:\Restored">
      <div class="acts"><button id="rgo" class="primary">Restore it here</button></div>
      <p class="msg" id="rnote"></p>
    </div>
  </details>
</div>
</main>
<script>
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
function ago(t) {
  if (!t) return "never";
  const m = Math.round((Date.now() - (typeof t === "number" ? t * 1000 : Date.parse(t))) / 60000);
  if (m < 1) return "just now"; if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60); if (h < 24) return `${h} hour${h > 1 ? "s" : ""} ago`;
  return new Date(typeof t === "number" ? t * 1000 : t).toLocaleString("en-IN", {day: "numeric", month: "short", hour: "numeric", minute: "2-digit"});
}
const MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
// "INDIGO: vouchers 202504" -> "INDIGO: April 2025's transactions", and so on: what it is doing, in words.
function doing(step) {
  if (!step || step === "starting") return "Starting";
  if (step === "the list of companies") return "Asking Tally which companies are open";
  const [co, ...rest] = step.split(": "), what = rest.join(": ");
  const m = what.match(/^vouchers (\d{4})(\d\d)$/);
  if (m) return `${co}: ${MONTHS[+m[2] - 1]} ${m[1]}'s transactions`;
  if (what.startsWith("vouchers changed")) return `${co}: what changed${what.replace(/^vouchers changed since the last read/, "")}`;
  return co + ": " + ({ledgers: "accounts", groups: "groups", stock: "stock"}[what] || what);
}
function plainError(e) {
  if (/TallyUnavailable/.test(e)) return "Tally isn't answering. Is it open, with its connection (port 9000) turned on?";
  if (/Company not open|isn't open|not loaded/i.test(e)) return "A company closed in Tally while it was being read. Open it again and check for changes.";
  if (/Tally answered with an error/.test(e)) return "Tally refused one of the reads. The books keep their last good copy.";
  if (/HTTP|Connect|Timeout|getaddrinfo/i.test(e)) return "The books couldn't be sent: is the internet on? They go at the next check.";
  return "Something stopped the last check. The books keep their last good copy.";
}
async function load() {
  const r = await (await fetch("/keeper/status")).json(), s = r.status || {};
  const dot = $("dot"), stopped = !r.busy && s.step && !s.finished;
  $("now").disabled = $("full").disabled = r.busy;
  $("why").hidden = !(s.error || stopped);
  $("detail").textContent = [s.error, s.step ? `while reading ${s.step}` : ""].filter(Boolean).join("\n");
  if (r.busy) { dot.className = "dot busy"; $("headline").textContent = "Checking Tally"; $("sub").textContent = doing(s.step) + "…"; }
  else if (s.error || stopped) { dot.className = "dot bad"; $("headline").textContent = "Not up to date";
    $("sub").textContent = s.error ? plainError(s.error) : "The last check stopped part-way, perhaps when the app was closed. Check for changes."; }
  else if (!s.finished) { dot.className = "dot warn"; $("headline").textContent = "Not checked yet"; $("sub").textContent = "Choose Check for changes to send the books for the first time."; }
  else { dot.className = "dot"; $("headline").textContent = "Up to date";
    $("sub").textContent = `Checked ${ago(s.finished)}` + (r.waiting ? ` · ${r.waiting} files still to send` : "") + (r.auto ? ` · checks by itself every ${r.every === 1 ? "hour" : r.every + " hours"}` : ""); }
  if (document.activeElement !== $("every")) $("every").value = String(r.every);
  $("rows").replaceChildren(...(s.companies || []).map(c => {
    const row = el("div", "row"), main = el("div", "main");
    main.append(el("div", "name", c.name), el("div", "meta", `${(c.vouchers || 0).toLocaleString("en-IN")} transactions · ${c.bills || 0} open bills`));
    if (c.how) main.append(el("div", "meta", c.how.charAt(0).toUpperCase() + c.how.slice(1)));
    row.append(main, el("div", "side", ago(c.read_at)));
    return row;
  }));
  if (!(s.companies || []).length) $("rows").replaceChildren(Object.assign(el("div", "row"), {textContent: "No company has been sent yet. Open the companies in Tally, then check for changes."}));
}
// Same plain words as the control plane's caps.js.
const ACCESS = /*ACCESS*/{};
const CAN = {admin: ACCESS.admin, ...Object.fromEntries(Object.entries(ACCESS.legacy))};
const canWords = caps => caps.length ? caps.map(c => CAN[c] || (ACCESS.keys[c] ? `${ACCESS.keys[c][0]}: ${ACCESS.keys[c][1]}` : c)) : ["Can sign in, nothing else."];
function deviceCan(kind, caps) {
  if (kind === "office_server") return "Is the office server itself.";
  if (kind === "office_client") return "Opens the office server, for the parts of it their access allows.";
  return caps.some(c => c === "books" || c === "books.view") ? "Phone or laptop for the books." : "Phone or laptop; can't open the books without the Books access.";
}
async function loadPeople() {
  const r = await (await fetch("/keeper/people")).json(), box = $("people");
  if (!r.known || !r.people.length) { box.replaceChildren(Object.assign(el("div", "row"), {textContent: "The list of people hasn't arrived from the control plane yet."})); return; }
  box.replaceChildren(...r.people.map(p => {
    const row = el("div", "row"), main = el("div", "main");
    main.append(el("div", "name", p.name || p.email), el("div", "meta", p.email));
    for (const t of canWords(p.caps)) main.append(el("div", "meta", t));
    for (const d of p.devices) {
      main.append(el("div", "meta", `${d.name || "Device"} (${d.status}): ${deviceCan(d.kind, p.caps)}`));
      if (d.key) main.append(el("div", d.key.startsWith("Has") ? "meta" : "meta warn", `Books key: ${d.key}`));
    }
    if (!p.devices.length) main.append(el("div", "meta", "No devices yet."));
    row.append(main, el("div", "side", p.status === "active" ? "Active" : p.status));
    return row;
  }));
}
for (const [id, q] of [["now", ""], ["full", "?full=true"]]) $(id).onclick = async () => {
  $("now").disabled = $("full").disabled = true;
  await fetch("/keeper/sync" + q, {method: "POST", headers: {"X-UA-Request": "1"}});
  setTimeout(load, 600);
};
const mb = n => n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
let restoring = null;
async function loadBackups() {
  const r = await (await fetch("/keeper/backups")).json(), st = r.state;
  if (!$("bfolder").value) $("bfolder").value = st.folder || st.guess || "";
  const d = $("bdot");
  if (!st.folder) { d.className = "dot warn"; $("bhead").textContent = "not set up"; $("bsub").textContent = "Tell the Keeper where Tally keeps its backups, below."; $("where").open = true; }
  else if (!st.folder_ok) { d.className = "dot bad"; $("bhead").textContent = "folder not found"; $("bsub").textContent = `${st.folder} isn't on this PC any more.`; $("where").open = true; }
  else if (st.old) { d.className = "dot warn"; $("bhead").textContent = st.newest ? "the newest is old" : "none yet";
    $("bsub").textContent = st.newest ? `The newest was taken ${ago(st.newest)}. Take one in Tally, or turn on its daily backup.` : "Take one in Tally (Data > Backup), or turn on its daily backup."; }
  else { d.className = "dot"; $("bhead").textContent = "kept safe"; $("bsub").textContent = `${st.sent} backup${st.sent === 1 ? "" : "s"} kept · the newest taken ${ago(st.newest)}`; }
  $("brows").replaceChildren(...r.sent.map(b => {
    const row = el("div", "row"), main = el("div", "main"), go = el("button", "", "Restore…");
    main.append(el("div", "name", new Date(b.taken).toLocaleString("en-IN", {day: "numeric", month: "long", year: "numeric", hour: "numeric", minute: "2-digit"})),
      el("div", "meta", `${b.name} · ${mb(b.size)}`));
    go.onclick = () => { restoring = b.path; $("rname").textContent = main.firstChild.textContent; $("rbox").hidden = false; $("rnote").textContent = ""; $("rfolder").focus(); };
    row.append(main, go);
    return row;
  }));
  if (!r.sent.length) $("brows").replaceChildren(Object.assign(el("div", "row"), {textContent: "No backups kept yet."}));
}
const post = (url, body) => fetch(url, {method: "POST", headers: {"X-UA-Request": "1", "Content-Type": "application/json"}, body: JSON.stringify(body)})
  .then(async r => ({ok: r.ok, body: await r.json().catch(() => ({}))}));
$("bsave").onclick = async () => {
  const r = await post("/keeper/backup-folder", {folder: $("bfolder").value});
  $("bnote").className = r.ok ? "msg ok" : "msg bad";
  $("bnote").textContent = r.ok ? `Saved. ${r.body.found} backup${r.body.found === 1 ? "" : "s"} there; the next check sends ${r.body.found === 1 ? "it" : "them"}.` : r.body.detail;
  loadBackups();
};
$("every").onchange = async () => {
  const r = await post("/keeper/every", {hours: +$("every").value});
  $("enote").className = r.ok ? "msg ok" : "msg bad";
  $("enote").textContent = r.ok ? "Saved." : r.body.detail;
  load();
};
$("rgo").onclick = async () => {
  $("rgo").disabled = true; $("rnote").className = "msg"; $("rnote").textContent = "Downloading and checking…";
  const r = await post("/keeper/restore", {path: restoring, folder: $("rfolder").value});
  $("rgo").disabled = false; $("rnote").className = r.ok ? "msg ok" : "msg bad";
  $("rnote").textContent = r.ok ? `Done, and checked: ${r.body.file}. Now in Tally choose Data > Restore, and pick that folder.` : r.body.detail;
};
load(); loadBackups(); loadPeople(); setInterval(load, 4000); setInterval(loadBackups, 30000); setInterval(loadPeople, 60000);
</script>
</body></html>"""


def _fill() -> str:
    import json

    from app.office.caps import KEYS, LEGACY_LABELS
    legacy = {"console": "Everything in the console: " + "; ".join(v[0] for k, v in KEYS.items() if k.startswith("con.")) + ".",
              "tib": "Everything in the Bridge: " + "; ".join(v[0] for k, v in KEYS.items() if k.startswith("tib.")) + ".",
              "books": "Everything in the books: " + "; ".join(v[0] for k, v in KEYS.items() if k.startswith("books.")) + "."}
    admin = ("Can approve people and devices, run the office server (update, shut down, data folder), "
             "use the Keeper page, and gets admin alerts.")
    return json.dumps({"keys": KEYS, "legacy": legacy, "admin": admin, "labels": LEGACY_LABELS})


HTML = HTML.replace("/*ACCESS*/{}", _fill())
