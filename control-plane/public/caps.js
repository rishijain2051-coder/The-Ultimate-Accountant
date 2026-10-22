// The one list of permissions (docs/permissions.md): keys, labels, descriptions, presets.
// Display only: what a key allows is enforced by the server (books and admin here, console and
// Bridge on the office server). The old broad keys console, tib and books still work: each
// implies every detailed key of its area.
export const AREAS = ["Console", "Bridge", "Books", "Admin"];
const P = (key, area, label, short, desc) => ({ key, area, label, short, desc });
export const LIST = [
  P("con.recon", "Console", "Reconciliation", "reconcile GST 2B/Tally, bank and party statements",
    "GST 2B/Tally, bank and party-statement reconciliation: runs, items, decisions, suppliers, workspaces"),
  P("con.exports", "Console", "Exports register", "the exports register",
    "The export documents register: add, correct, settle, mark, read with AI"),
  P("con.reports", "Console", "Reports and downloads", "download reports, packs and kept files",
    "Download Excel/PDF/CSV reports, follow-up packs, kept files and zips"),
  P("con.settings", "Console", "Console settings", "change the console's settings",
    "Change the console's settings (tolerance, financial year, letter details, update checks)"),
  P("con.ai", "Console", "AI checker", "manage the AI checker",
    "Get or remove the AI runtime, turn the AI second reading on or off"),
  P("tib.upload", "Bridge", "Upload bills", "upload bills", "Upload bills, use the phone code"),
  P("tib.review", "Bridge", "Review entries", "review entries", "See, edit, reject, rematch and check entries"),
  P("tib.post", "Bridge", "Post to Tally", "post entries to Tally",
    "Approve entries into Tally (post or draft), create masters"),
  P("tib.bank", "Bridge", "Bank statements", "bank statements",
    "Upload bank statements, check and post bank rows"),
  P("tib.sync", "Bridge", "Sync from Tally", "sync from Tally",
    "Read companies, ledgers, stock and voucher types from Tally"),
  P("books.view", "Books", "See the books", "open the books",
    "Open the Keeper's books on approved phones and laptops (gets the data key)"),
  P("books.alerts", "Books", "Alerts", "overdue and big-payment alerts", "Overdue and big-payment notifications"),
  P("books.balances", "Books", "Balances", "cash and bank, accounts and party balances",
    "Cash and bank, accounts and party balances screens. Only hides screens: every approved books device holds the one data key"),
  P("books.sales", "Books", "Sales and purchases", "sales/purchase figures and transactions",
    "Sales/purchase figures and the transactions list. Only hides screens: every approved books device holds the one data key"),
  P("admin", "Admin", "Admin", "people, devices, office server, Keeper page, backups, connections",
    "People, devices, office server controls, Keeper page, Tally backups, Tally/AI connection settings"),
];
export const CAPS = Object.fromEntries(LIST.map((c) => [c.key, c]));
export const PRESETS = {
  Owner: LIST.map((c) => c.key),
  Accountant: LIST.filter((c) => c.area !== "Admin").map((c) => c.key),
  "Data entry": ["tib.upload", "tib.review"],
  Viewer: ["books.view", "books.balances"],
};

// The broad key that implies each area's detailed keys.
const BROAD = { console: "con.", tib: "tib.", books: "books." };

// The detailed keys a person's stored caps give (old broad keys expanded). Unknown keys are dropped.
export const expand = (caps = []) => new Set(LIST.map((c) => c.key).filter((k) =>
  caps.includes(k) || Object.entries(BROAD).some(([b, p]) => k.startsWith(p) && caps.includes(b))));
export const has = (caps, key) => expand(caps).has(key);

// The "Access: ..." line shown when approving a device.
export const access = (caps) => {
  const on = expand(caps);
  return AREAS.map((a) => { const l = LIST.filter((c) => c.area === a && on.has(c.key)).map((c) => c.label);
    return l.length ? `${a}: ${l.join(", ")}` : null; }).filter(Boolean).join("; ") || "none (can sign in, nothing else)";
};

// A person's caps as sentences.
export const can = (caps) => {
  const on = expand(caps);
  const l = LIST.filter((c) => on.has(c.key)).map((c) => `${c.area}, ${c.label}: ${c.desc}.`);
  return l.length ? l : ["Can sign in, nothing else."];
};

// What a device can do for its owner, given the owner's caps.
export function deviceCan(kind, caps) {
  if (kind === "office_server") return "Is the office server itself.";
  if (kind === "office_client") return "Opens the office server, for the parts of it their access allows.";
  return has(caps, "books.view") ? "Phone or laptop for the books." : "Phone or laptop; can't open the books without the See the books permission.";
}
