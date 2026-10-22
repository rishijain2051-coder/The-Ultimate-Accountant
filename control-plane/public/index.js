// The first page: sign in, register this device, wait for approval. The books come here in Phase F.
import { can, has } from "/caps.js";
import { deviceKeys, el, fetchKeys, fingerprint, session, signOut } from "/app.js";

const view = document.getElementById("view");
const SIGNIN = {
  refused: "That Google account isn't on the list. Ask your admin to add it, then sign in again.",
  failed: "Google's sign-in didn't complete. Try again.",
  expired: "That sign-in took too long or was opened in another tab. Try again.",
  cancelled: "Sign-in was cancelled.",
};

function show(...kids) { view.replaceChildren(...kids.filter((k) => k != null)); }

async function render() {
  const why = new URLSearchParams(location.search).get("signin");
  let s;
  try {
    s = await session();
  } catch (e) {
    show(el("h1", {}, "Something went wrong."), el("p", { class: "lede" }, e.message),
      el("button", { onclick: render }, "Try again"));
    return;
  }
  if (!s) {
    document.getElementById("nav").hidden = true;
    show(
      why && SIGNIN[why] ? el("p", { class: "note bad" }, SIGNIN[why]) : null,
      el("h1", {}, "Sign in to see the books."),
      el("p", { class: "lede" }, "Use the Google account your admin added. This device then waits for their approval once."),
      el("a", { class: "button primary", href: "/api/auth/google/start?return_to=/" }, "Sign in with Google"),
    );
    return;
  }
  history.replaceState(null, "", "/");
  document.getElementById("nav").hidden = false;
  const d = s.device;
  document.getElementById("to-admin").hidden = !(d.status === "approved" && s.user.caps.includes("admin"));

  if (d.status === "pending") {
    const k = await deviceKeys();   // the digits come from this device's own keys, not from the server
    show(
      el("h1", {}, "This device is waiting to be approved."),
      el("p", { class: "lede" }, `Signed in as ${s.user.email}. Show these 24 digits to your admin. They approve this device only if theirs shows the same.`),
      el("p", { class: "label" }, "This device"),
      el("p", { class: "fingerprint" }, await fingerprint(k.sign_pub, k.wrap_pub)),
      el("p", { class: "hint" }, `${d.name}. This page moves on by itself once it's approved.`),
    );
    setTimeout(render, 8000);
    return;
  }
  const k = await deviceKeys();
  let keys = null;
  if (has(s.user.caps, "books.view")) {
    try { keys = await fetchKeys(); } catch (e) { keys = { error: e.message }; }
  }
  show(
    el("h1", {}, "This device is approved."),
    el("p", { class: "lede" }, `Signed in as ${s.user.email}.`),
    keys?.kids?.length ? el("a", { class: "button primary", href: "/books" }, "Open the books") : null,
    keys == null ? null : el("p", { class: `note ${keys.kids?.length ? "good" : ""}` },
      keys.error ? `The books' key couldn't be fetched: ${keys.error}`
        : keys.kids.length ? `This device holds the books' key (${keys.kids.join(", ")}).`
          : "Waiting for the office server to send this device the books' key."),
    s.user.caps.includes("admin") ? el("a", { class: "button", href: "/admin" }, "Open the admin console") : null,
    el("p", { class: "label" }, "What you can do"),
    el("ul", { class: "can" }, ...can(s.user.caps).map((t) => el("li", {}, t))),
    el("p", { class: "label" }, "This device's 24 digits"),
    el("p", { class: "hint" }, `${await fingerprint(k.sign_pub, k.wrap_pub)}. The office server asks you for them when it is set up.`),
  );
}

document.getElementById("sign-out").addEventListener("click", async () => { await signOut(); render(); });
render();
