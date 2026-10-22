// Linking an office PC to the person signed in here. Ultimate Accountant on the PC opens this
// page with its public keys and a signature in the address's #fragment (never sent to the
// server in the address); the person confirms, the keys are posted, and the browser goes
// back to the PC's own loopback address with the new device's id.
import { api, el, fingerprint } from "/app.js";

const view = document.getElementById("view");
const KINDS = { office_server: "the office server", office_client: "an office PC" };

function show(...kids) { view.replaceChildren(...kids.filter((k) => k != null)); }

// The fragment survives Google's sign-in only in sessionStorage.
if (location.hash.length > 1) {
  sessionStorage.setItem("ua-link", location.hash.slice(1));
  history.replaceState(null, "", "/link");
}
const p = new URLSearchParams(sessionStorage.getItem("ua-link") || "");
const port = Number(p.get("port"));

async function render() {
  if (!p.get("sign_pub") || !KINDS[p.get("kind")] || !(port > 1024 && port < 65536)) {
    show(el("h1", {}, "Nothing to link."), el("p", { class: "lede" }, "Start linking from Ultimate Accountant on the PC."));
    return;
  }
  let s;
  try { s = await api("/api/session"); } catch (e) {
    if (e.status !== 401) { show(el("h1", {}, "Something went wrong."), el("p", { class: "lede" }, e.message)); return; }
  }
  if (!s) {
    show(el("h1", {}, "Sign in to link this PC."),
      el("p", { class: "lede" }, "Sign in with the Google account of the person who uses this PC."),
      el("a", { class: "button primary", href: "/api/auth/google/start?return_to=/link" }, "Sign in with Google"));
    return;
  }
  const fp = await fingerprint(p.get("sign_pub"), p.get("wrap_pub"));
  show(
    el("h1", {}, `Link this PC as ${KINDS[p.get("kind")]}?`),
    el("p", { class: "lede" }, `It will belong to ${s.user.email}, as “${p.get("name") || "this PC"}”. Ultimate Accountant on the PC shows the same 24 digits (an older app shows only the first 12):`),
    el("p", { class: "fingerprint" }, fp),
    el("p", { class: "hint" }, "Only go on if this window was opened by Ultimate Accountant just now. An admin then approves the PC."),
    el("div", { class: "actions" },
      el("button", { class: "primary", onclick: go }, "Link this PC"),
      el("button", { class: "link", onclick: () => { sessionStorage.removeItem("ua-link"); show(el("h1", {}, "Not linked.")); } }, "Cancel")),
  );
}

async function go() {
  try {
    const r = await api("/api/devices/link", { method: "POST", body: {
      kind: p.get("kind"), name: p.get("name") || "", os: p.get("os") || "", app_version: p.get("app_version") || "",
      sign_pub: p.get("sign_pub"), wrap_pub: p.get("wrap_pub"), nonce: p.get("nonce"), signature: p.get("sig") } });
    sessionStorage.removeItem("ua-link");
    location.href = `http://127.0.0.1:${port}/done?device=${encodeURIComponent(r.device.id)}&nonce=${encodeURIComponent(p.get("nonce"))}`;
  } catch (e) {
    show(el("p", { class: "note bad" }, e.message), el("h1", {}, "The PC was not linked."));
  }
}

render();
