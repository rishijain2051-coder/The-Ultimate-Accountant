// Shared by every page: this device's keys, the API, fingerprints, and building elements.
//
// The device's two P-256 keys are made here once and kept in IndexedDB as non-extractable
// WebCrypto keys: scripts on this page can use them, nobody can read them out. Their public
// halves are the device's identity; cp/devices.py checks every signature made here.

const DB_NAME = "ua-device", STORE = "keys";

function idb() {
  return new Promise((ok, no) => {
    const r = indexedDB.open(DB_NAME, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE);
    r.onsuccess = () => ok(r.result);
    r.onerror = () => no(r.error);
  });
}

async function idbDo(mode, fn) {
  const db = await idb();
  return new Promise((ok, no) => {
    const t = db.transaction(STORE, mode), req = fn(t.objectStore(STORE));
    t.oncomplete = () => ok(req.result);
    t.onerror = () => no(t.error);
  });
}

const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

export async function deviceKeys() {
  let k = await idbDo("readonly", (s) => s.get("me"));
  if (k) return k;
  const sign = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
  const wrap = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, false, ["deriveKey", "deriveBits"]);
  k = {
    sign, wrap,
    sign_pub: b64(await crypto.subtle.exportKey("spki", sign.publicKey)),
    wrap_pub: b64(await crypto.subtle.exportKey("spki", wrap.publicKey)),
  };
  await idbDo("readwrite", (s) => s.put(k, "me"));
  return k;
}

export const forgetDevice = () => idbDo("readwrite", (s) => s.delete("me"));

export async function sign(k, text) {
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, k.sign.privateKey, new TextEncoder().encode(text));
  return b64(sig);   // WebCrypto's raw r||s, as cp/devices.py expects
}

// 24 digits from SHA-256(sign_pub || wrap_pub): the same sum as cp/devices.py fingerprint().
export async function fingerprint(signPub, wrapPub) {
  const h = new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array([...unb64(signPub), ...unb64(wrapPub)])));
  const part = (a, b) => (h.slice(a, b).reduce((n, x) => (n << 8n) | BigInt(x), 0n) % 10n ** 12n).toString().padStart(12, "0");
  const d = part(0, 8) + part(8, 16);   // the first 12 are the old 12-digit code
  return [0, 6, 12, 18].map((i) => d.slice(i, i + 6)).join(" ");
}

export async function api(path, { method = "GET", body } = {}) {
  const r = await fetch(path, {
    method, credentials: "same-origin",
    headers: { "X-UA-Request": "1", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(typeof data.detail === "string" ? data.detail : `Something went wrong (${r.status}).`);
    e.status = r.status;
    throw e;
  }
  return data;
}

function deviceName() {
  const ua = navigator.userAgent;
  const os = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android"
    : /Windows/.test(ua) ? "Windows" : /Mac OS/.test(ua) ? "Mac" : "Device";
  const browser = /Edg\//.test(ua) ? "Edge" : /CriOS|Chrome\//.test(ua) ? "Chrome" : /FxiOS|Firefox/.test(ua) ? "Firefox"
    : /Safari/.test(ua) ? "Safari" : "Browser";
  return { name: `${os}, ${browser}`, os };
}

// The signed-in session with this device attached to it, or null when nobody is signed in.
export async function session() {
  let s;
  try {
    s = await api("/api/session");
  } catch (e) {
    if (e.status === 401) return null;
    throw e;
  }
  if (!s.device) {
    const k = await deviceKeys();
    const { name, os } = deviceName();
    const signature = await sign(k, ["ua-attach:v1", s.challenge, k.sign_pub, k.wrap_pub].join("\n"));
    try {
      s.device = (await api("/api/devices/attach", {
        method: "POST", body: { sign_pub: k.sign_pub, wrap_pub: k.wrap_pub, signature, name, os, app_version: "web-1" },
      })).device;
    } catch (e) {
      if (e.status !== 410 && e.status !== 409) throw e;
      await purge();
      await forgetDevice();          // revoked, or another person's keys: start again as a new device, waiting for approval
      return session();
    }
  }
  return s;
}

// Forget what this browser holds for the books: the data keys, the kept copy, the push
// subscription. For sign-out, revoke, and a person who isn't allowed any more.
export async function purge() {
  const each = (fn) => Promise.resolve().then(fn).catch(() => {});
  await Promise.all([
    each(() => idbDo("readwrite", (s) => s.delete(IDBKeyRange.bound("dek:", "dek:\uffff")))),
    each(() => caches.delete("ua-books")),
    each(async () => (await (await navigator.serviceWorker.getRegistration())?.pushManager.getSubscription())?.unsubscribe()),
  ]);
}

export const signOut = async () => { await purge(); return api("/api/auth/sign-out", { method: "POST" }); };

// The Keeper's data keys wrapped for this device: each unwrapped once into a non-extractable
// key and kept in IndexedDB as "dek:<kid>". Returns {current, kids} this device now holds.
export async function fetchKeys() {
  const { unwrapKey } = await import("/keeper-crypto.js");
  const k = await deviceKeys();
  const r = await api("/api/keys/me");
  const kids = [];
  for (const w of r.keys) {
    if (!(await idbDo("readonly", (s) => s.get(`dek:${w.kid}`)))) {
      const dek = await unwrapKey(w, w.kid, r.device, k.wrap.privateKey);
      await idbDo("readwrite", (s) => s.put(dek, `dek:${w.kid}`));
    }
    kids.push(w.kid);
  }
  return { current: r.current, kids };
}

export const dataKey = (kid) => idbDo("readonly", (s) => s.get(`dek:${kid}`));

// el("p", {class: "x", onclick: f}, "text", child): text is always set as text, never as HTML.
export function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) e.append(kid instanceof Node ? kid : String(kid));
  return e;
}

const IST = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short",
  hour: "2-digit", minute: "2-digit" });
export const when = (iso) => (iso ? IST.format(new Date(iso)) : "never");
