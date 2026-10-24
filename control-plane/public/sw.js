// The service worker: the books' pages open without the internet (the last copy of each),
// and push notifications are shown here. Overdue and big-transaction notices arrive sealed
// with the books' data key; they are opened here with this device's copy of it, in the same
// IndexedDB the pages use, so the control plane never sees what they say.

const SHELL = "ua-shell-v7";
// Every file the books page loads or imports: one missing here would come from a different deploy.
const PAGES = ["/books", "/books.js", "/books.css", "/app.js", "/caps.js", "/keeper-crypto.js", "/manifest.json", "/favicon.svg",
  "/favicon.ico", "/apple-touch-icon.png", "/icon-192.png", "/"];

self.addEventListener("install", (e) => {
  // Each page on its own: one that can't be fetched now mustn't stop notifications working.
  e.waitUntil(caches.open(SHELL).then((c) => Promise.all(PAGES.map((p) => c.add(p).catch(() => {}))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((names) => Promise.all(names.filter((n) => n.startsWith("ua-shell-") && n !== SHELL).map((n) => caches.delete(n))))
    .then(() => self.clients.claim()));
});

// The pages themselves: the network first, so every file is from the same deploy (an old
// app.js beside a new books.js breaks the page); the kept copy only when the network is
// gone or hasn't answered in 2.5 seconds. Nothing under /api.
const WAIT = 2500;
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  if (!PAGES.includes(url.pathname)) return;
  e.respondWith(caches.open(SHELL).then(async (c) => {
    const net = fetch(e.request, { cache: "no-cache" }).then((r) => { if (r.ok) c.put(url.pathname, r.clone()); return r; });
    e.waitUntil(net.catch(() => {}));
    const late = new Promise((ok) => setTimeout(ok, WAIT));
    const r = await Promise.race([net.catch(() => null), late.then(() => null)]);
    if (r?.ok) return r;
    return (await c.match(url.pathname)) || r || net;     // nothing kept: whatever the network gives
  }));
});

function dataKey(kid) {
  return new Promise((ok, no) => {
    const r = indexedDB.open("ua-device", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("keys");
    r.onerror = () => no(r.error);
    r.onsuccess = () => {
      const t = r.result.transaction("keys", "readonly"), q = t.objectStore("keys").get(`dek:${kid}`);
      q.onsuccess = () => ok(q.result || null);
      q.onerror = () => no(q.error);
    };
  });
}

const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

async function opened(sealed, keyFor = dataKey) {
  const key = await keyFor(sealed.kid);
  if (!key) return null;
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: unb64(sealed.nonce), additionalData: new TextEncoder().encode(`ua-push:v1:${sealed.kid}`) },
    key, unb64(sealed.ct));
  return JSON.parse(new TextDecoder().decode(plain));
}

self.addEventListener("push", (e) => {
  e.waitUntil((async () => {
    let m = {};
    try { m = e.data ? e.data.json() : {}; } catch { m = {}; }
    if (m.sealed) {
      try {
        m = (await opened(m.sealed)) || { title: "Something new in the books", body: "Open the books to see it.", url: "/books" };
      } catch {
        m = { title: "Something new in the books", body: "Open the books to see it.", url: "/books" };
      }
    }
    await self.registration.showNotification(m.title || "Keeper", {
      body: m.body || "", data: { url: m.url || "/books", cid: m.cid || null }, tag: m.cid ? `${m.cid}:${m.title}` : undefined,
    });
  })());
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const { url, cid } = e.notification.data || {};
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    let target = new URL(url || "/books", location.origin);
    if (target.origin !== location.origin) target = new URL("/books", location.origin);
    if (cid) target.searchParams.set("company", cid);
    for (const w of all) if (new URL(w.url).pathname === target.pathname && "focus" in w) { await w.navigate(target.href); return w.focus(); }
    return self.clients.openWindow(target.href);
  })());
});
