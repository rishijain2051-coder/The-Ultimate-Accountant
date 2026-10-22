// The admin console: devices (approve with this device's signature, revoke, rename),
// people and their access, and the audit log. Needs an approved device and the admin tick.
import { AREAS, LIST, PRESETS, access, can, deviceCan, expand } from "/caps.js";
import { api, deviceKeys, el, fingerprint, session, sign, signOut, when } from "/app.js";

const KINDS = { viewer: "phone or browser", office_server: "the office server", office_client: "office PC" };
const msg = document.getElementById("msg");
let me;

function say(text, kind = "good") { msg.replaceChildren(text ? el("p", { class: `note ${kind}` }, text) : ""); }

async function act(fn, done) {
  try { await fn(); say(done); } catch (e) { say(e.message, "bad"); }
  await load();
}

// --- devices ----------------------------------------------------------------

async function approve(d) {
  // The digits come from the keys this device is about to sign for, worked out here,
  // so a server that swapped the keys would show digits the new device doesn't.
  const fp = await fingerprint(d.sign_pub, d.wrap_pub);
  if (!confirm(`Approve ${d.name || "this device"} for ${d.email}?\n\nAccess: ${access(d.caps)}\n\nOnly if its screen shows:\n\n${fp}`)) return;
  const at = Math.floor(Date.now() / 1000);
  const text = ["ua-approve:v1", d.id, d.email, [...d.caps].sort().join(","), d.sign_pub, d.wrap_pub, String(at)].join("\n");
  const signature = await sign(await deviceKeys(), text);
  await act(() => api(`/api/admin/devices/${d.id}/approve`, { method: "POST", body: { at, signature } }),
    `Approved ${d.name || "the device"} for ${d.email}.`);
}

function rename(d) {
  const name = prompt("Name this device", d.name);
  if (name != null) act(() => api(`/api/admin/devices/${d.id}`, { method: "PATCH", body: { name } }), "Renamed.");
}

function revoke(d) {
  if (confirm(`Revoke ${d.name || "this device"} (${d.email})? It is signed out at once and can't sign in again as this device.`))
    act(() => api(`/api/admin/devices/${d.id}/revoke`, { method: "POST" }), `Revoked ${d.name || "the device"}.`);
}

async function devices() {
  const { devices: list, this_device } = await api("/api/admin/devices");
  const pending = list.filter((d) => d.status === "pending");
  const cards = await Promise.all(pending.map(async (d) => el("div", { class: "card" },
    el("p", { class: "label" }, "Waiting for approval"),
    el("p", {}, el("strong", {}, d.owner || d.email), ` (${d.email}), ${d.name || "unnamed device"}, ${KINDS[d.kind] || d.kind}`),
    el("p", {}, `Access: ${access(d.caps)}`),
    el("p", { class: "fingerprint" }, await fingerprint(d.sign_pub, d.wrap_pub)),
    el("p", { class: "hint" }, `First seen ${when(d.first_seen)} from ${d.last_ip || "?"}. Approve only if the device shows these same 24 digits (an older app shows only the first 12).`),
    el("div", { class: "actions" },
      el("button", { class: "primary", onclick: () => approve(d) }, "Approve"),
      el("button", { class: "link danger", onclick: () => revoke(d) }, "Refuse")),
  )));
  const rows = list.filter((d) => d.status !== "pending").map((d) => el("tr", {},
    el("td", {}, el("strong", {}, d.name || "unnamed"),
      el("div", { class: "hint" }, [KINDS[d.kind] || d.kind, d.id === this_device ? "this device" : null].filter(Boolean).join(", "))),
    el("td", {}, d.owner || "", el("div", { class: "hint" }, d.email)),
    el("td", {}, d.os || "", el("div", { class: "hint" }, d.app_version || "")),
    el("td", { class: "num" }, when(d.last_seen), el("div", { class: "hint" }, d.last_ip || "")),
    el("td", { class: "num" }, when(d.first_seen)),
    el("td", { class: `status ${d.status}` }, d.status),
    el("td", { class: "num" },
      el("button", { class: "link", onclick: () => rename(d) }, "Rename"), " ",
      d.status === "approved" && d.id !== this_device ? el("button", { class: "link danger", onclick: () => revoke(d) }, "Revoke") : null),
  ));
  document.getElementById("devices").replaceChildren(
    ...(pending.length ? cards : [el("p", { class: "hint" }, "No device is waiting for approval.")]),
    el("h2", {}, "Every device"),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ...["Device", "Owner", "System", "Last seen", "First seen", "State", ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows))),
  );
}

// --- people -----------------------------------------------------------------

function ticks(prefix, chosen = []) {
  const on = expand(chosen);     // an old broad key shows as all its detailed ticks
  const preset = (e) => {
    const box = e.target.closest(".ticks");
    for (const i of box.querySelectorAll("input[type=checkbox]")) i.checked = PRESETS[e.target.value]?.includes(i.value) ?? i.checked;
    e.target.value = "";
  };
  return el("span", { class: "ticks" },
    el("label", {}, "Start from ", el("select", { onchange: preset }, el("option", { value: "" }, "a set..."),
      ...Object.keys(PRESETS).map((p) => el("option", { value: p }, p)))),
    ...AREAS.map((area) => el("fieldset", {}, el("legend", { class: "label" }, area),
      ...LIST.filter((c) => c.area === area).map(({ key, label, desc }) => el("div", { class: "tick" },
        el("label", {}, el("input", { type: "checkbox", name: `${prefix}-${key}`, value: key, checked: on.has(key) }), label),
        el("div", { class: "hint" }, desc))))),
    el("p", { class: "hint" }, "Balances and Sales only hide screens in the books viewer. Every approved books device holds the one data key, so they are not a lock."));
}

const chosenIn = (root) => [...root.querySelectorAll("input[type=checkbox]:checked")].map((i) => i.value);

async function people() {
  const { users } = await api("/api/admin/users");
  const { devices: all } = await api("/api/admin/devices");
  const form = el("form", { class: "card" },
    el("p", { class: "label" }, "Add a person"),
    el("div", { class: "row" },
      el("label", {}, el("span", { class: "label" }, "Google email"), el("input", { type: "email", name: "email", required: true })),
      el("label", {}, el("span", { class: "label" }, "Name"), el("input", { type: "text", name: "name" }))),
    el("p", { class: "label" }, "Access"),
    ticks("new"),
    el("div", { class: "actions" }, el("button", { class: "primary", type: "submit" }, "Add")),
  );
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = new FormData(form);
    act(() => api("/api/admin/users", { method: "POST", body: { email: f.get("email"), name: f.get("name"), caps: chosenIn(form) } }),
      `Added ${f.get("email")}. They can sign in now.`);
  });
  const rows = users.map((u) => {
    const tr = el("tr", {},
      el("td", {}, el("strong", {}, u.name || ""), el("div", { class: "hint" }, u.email),
        el("p", { class: "label" }, "What they can do"),
        el("ul", { class: "can" }, ...can(u.caps).map((t) => el("li", {}, t)),
          ...all.filter((d) => d.email === u.email).map((d) => el("li", {},
            `${d.name || "Unnamed device"} (${d.status}): ${deviceCan(d.kind, u.caps)}`)))),
      el("td", {}, ticks(`u${u.id}`, u.caps)),
      el("td", { class: "num" }, `${u.devices} approved`, el("div", { class: "hint" }, `last seen ${when(u.last_seen)}`)),
      el("td", { class: `status ${u.status}` }, u.status),
      el("td", { class: "num" },
        el("button", { class: "small", onclick: () => act(() => api(`/api/admin/users/${u.id}`,
          { method: "PATCH", body: { caps: chosenIn(tr) } }), `Saved ${u.email}'s access.`) }, "Save access"), " ",
        u.id === me.user.id ? null : el("button", { class: `link${u.status === "active" ? " danger" : ""}`, onclick: () =>
          act(() => api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { status: u.status === "active" ? "disabled" : "active" } }),
            u.status === "active" ? `Disabled ${u.email} and signed them out everywhere.` : `Enabled ${u.email}.`) },
          u.status === "active" ? "Disable" : "Enable")),
    );
    return tr;
  });
  document.getElementById("people").replaceChildren(form, el("h2", {}, "Everyone on the list"),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ...["Person", "Access", "Devices", "State", ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows))));
}

// --- audit ------------------------------------------------------------------

async function audit(before) {
  const { audit: rows } = await api(`/api/admin/audit${before ? `?before=${before}` : ""}`);
  const lines = rows.map((a) => el("tr", {},
    el("td", { class: "num" }, when(a.at)),
    el("td", {}, a.action),
    el("td", {}, a.email || "", a.device ? el("div", { class: "hint" }, a.device) : null),
    el("td", {}, Object.entries(a.detail || {}).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join("; ")),
    el("td", { class: "num" }, a.ip || "")));
  const panel = document.getElementById("audit");
  const more = rows.length === 100 ? el("button", { class: "small", onclick: () => audit(rows[rows.length - 1].id) }, "Older") : null;
  panel.replaceChildren(el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...["When", "What", "Who", "Detail", "From"].map((h) => el("th", {}, h)))),
    el("tbody", {}, lines))), ...(more ? [more] : []));
}

// --- the page ---------------------------------------------------------------

async function load() {
  await Promise.all([devices(), people(), audit()]);
}

for (const b of document.querySelectorAll(".tabs button")) {
  b.addEventListener("click", () => {
    for (const x of document.querySelectorAll(".tabs button")) x.setAttribute("aria-selected", String(x === b));
    for (const p of document.querySelectorAll("[role=tabpanel]")) p.hidden = p.id !== b.dataset.tab;
  });
}
document.getElementById("sign-out").addEventListener("click", async () => { await signOut(); location.href = "/"; });

me = await session();
if (!me || me.device.status !== "approved" || !me.user.caps.includes("admin")) location.replace("/");
else load().catch((e) => say(e.message, "bad"));
