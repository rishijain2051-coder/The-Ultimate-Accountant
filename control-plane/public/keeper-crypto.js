// The Keeper's keys and files in the browser (PLAN-office.md section 4, PLAN-keeper.md 5.2).
//
// The office server wraps the data key for this device:
//   ECDH(its fresh key, this device's wrap key) -> HKDF-SHA256(salt "", info) -> AES-256-GCM, aad info
//   info = "keeper-dek:<kid>:<device id>"
// Here it is unwrapped straight into a non-extractable AES key and kept in IndexedDB, so
// scripts can decrypt with it but nothing can read it out. app/office/keys.py and
// app/keeper/ukeep.py are the other halves; tests check they agree.

const enc = new TextEncoder();
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

export async function unwrapKey(w, kid, deviceId, wrapPrivateKey) {
  const info = enc.encode(`keeper-dek:${kid}:${deviceId}`);
  const epk = await crypto.subtle.importKey("spki", unb64(w.epk), { name: "ECDH", namedCurve: "P-256" }, false, []);
  const shared = await crypto.subtle.deriveBits({ name: "ECDH", public: epk }, wrapPrivateKey, 256);
  const hkdf = await crypto.subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
  const kek = await crypto.subtle.deriveKey({ name: "HKDF", hash: "SHA-256", salt: new Uint8Array(), info }, hkdf,
    { name: "AES-GCM", length: 256 }, false, ["unwrapKey"]);
  return crypto.subtle.unwrapKey("raw", unb64(w.ct), kek, { name: "AES-GCM", iv: unb64(w.nonce), additionalData: info },
    { name: "AES-GCM" }, false, ["decrypt"]);
}

const MAGIC = enc.encode("UKEEP1\n");

// A .ukeep file (Uint8Array) -> {header, plain}. keyFor(kid) gives the CryptoKey or null.
export async function openUkeep(bytes, keyFor) {
  if (bytes.length < 11 || !MAGIC.every((b, i) => bytes[i] === b)) throw new Error("Not a .ukeep file.");
  const n = new DataView(bytes.buffer, bytes.byteOffset + 7, 4).getUint32(0);
  const hdr = bytes.subarray(11, 11 + n);
  const header = JSON.parse(new TextDecoder().decode(hdr));
  if (header.v !== 1) throw new Error("A newer kind of file than this page reads.");
  const key = await keyFor(header.kid);
  if (!key) throw new Error(`This device doesn't have key ${header.kid} yet.`);
  const h = new Uint8Array(await crypto.subtle.digest("SHA-256", hdr));
  const np = unb64(header.np), size = header.chunk + 16, parts = [];
  for (let off = 11 + n, i = 0; ; i++) {
    const block = bytes.subarray(off, off + size);
    off += size;
    const last = off >= bytes.length;
    const iv = new Uint8Array(12), aad = new Uint8Array(37);
    iv.set(np);
    new DataView(iv.buffer).setUint32(8, i);
    aad.set(h);
    new DataView(aad.buffer).setUint32(32, i);
    aad[36] = last ? 1 : 0;
    try {
      parts.push(new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv, additionalData: aad }, key, block)));
    } catch {
      throw new Error("This file was changed, cut short, or isn't for this key.");
    }
    if (last) break;
  }
  const plain = new Uint8Array(parts.reduce((t, p) => t + p.length, 0));
  let at = 0;
  for (const p of parts) { plain.set(p, at); at += p.length; }
  return { header, plain };
}
