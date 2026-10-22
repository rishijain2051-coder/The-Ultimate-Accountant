/* core - formatting, the api, and toasts. Nothing here knows about a view. */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ── numbers and dates ───────────────────────────────────────────────── */

const MINUS = '−';
const DASH = '—';
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** ₹14,28,288 - Indian grouping, paise only when there are some. */
export function inr(v, { sign = false } = {}) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return DASH;
  const n = Number(v);
  const a = Math.abs(n);
  const fixed = a.toFixed(2);
  let [whole, paise] = fixed.split('.');
  if (whole.length > 3) {
    whole = whole.slice(0, -3).replace(/(\d)(?=(\d\d)+$)/g, '$1,') + ',' + whole.slice(-3);
  }
  const s = '₹' + whole + (paise === '00' ? '' : '.' + paise);
  return (sign && n < 0 ? MINUS : '') + s;
}

export function num(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return DASH;
  return Number(v).toLocaleString('en-IN');
}

export function plural(n, one, many) {
  return `${num(n)} ${n === 1 ? one : (many || one + 's')}`;
}

function parts(v) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(v || ''));
  return m ? [+m[1], +m[2], +m[3]] : null;
}

/** 12 Apr 2025 */
export function day(v) {
  const p = parts(v);
  return p ? `${p[2]} ${MON[p[1] - 1]} ${p[0]}` : DASH;
}

/** 12 Apr 25 - for the narrow date column. */
export function dayShort(v) {
  const p = parts(v);
  return p ? `${p[2]} ${MON[p[1] - 1]} ${String(p[0]).slice(2)}` : '';
}

export function when(iso) {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`;
  return `${d.getDate()} ${MON[d.getMonth()]} ${d.getFullYear()}`;
}

/* ── the two drawn marks ─────────────────────────────────────────────── */

export const TICK = `<svg class="tick" viewBox="0 0 24 24" aria-hidden="true">
  <path d="M4.5 12.5l4.8 4.8L19.5 6.8"/></svg>`;
export const CHEVRON = `<svg class="chev" viewBox="0 0 24 24" aria-hidden="true">
  <path d="M6.5 9.5l5.5 5.5 5.5-5.5"/></svg>`;

/* ── dom ─────────────────────────────────────────────────────────────── */

export function on(root, event, selector, handler) {
  root.addEventListener(event, ev => {
    const target = ev.target.closest(selector);
    if (target && root.contains(target)) handler(ev, target);
  });
}

export function debounce(fn, ms = 180) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/* ── api ─────────────────────────────────────────────────────────────── */

async function handle(res) {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j && j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
    } catch { /* the body was not json */ }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  get: path => fetch(path).then(handle),
  post: (path, body) => fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  }).then(handle),
  del: path => fetch(path, { method: 'DELETE' }).then(handle),
  form: (path, formData) => fetch(path, { method: 'POST', body: formData }).then(handle),
};

async function fetchFile(path) {
  const res = await fetch(path);
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
  }
  const cd = res.headers.get('content-disposition') || '';
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd), plain = /filename="([^"]+)"/.exec(cd);
  return { blob: await res.blob(), name: star ? decodeURIComponent(star[1]) : plain ? plain[1] : 'download' };
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

/** Download through an anchor so the browser keeps the server's filename. */
export async function download(path) {
  const { blob, name } = await fetchFile(path);
  saveBlob(blob, name);
  return name;
}

/**
 * Hand a file to Windows' share window (mail, WhatsApp, nearby sharing).
 * Browsers share only some kinds of file - PDFs and pictures, not Excel - so
 * anything else is saved as a copy instead. Says which happened.
 */
export async function share(path) {
  const { blob, name } = await fetchFile(path);
  const file = new File([blob], name, { type: blob.type });
  if (navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: name });
      return { how: 'shared', name };
    } catch (err) {
      if (err.name === 'AbortError') return { how: 'cancelled', name };
    }
  }
  saveBlob(blob, name);
  return { how: 'saved', name };
}

export async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // The clipboard API needs a secure context; fall back to a selection copy.
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand?.('copy');
    ta.remove();
    return !!ok;
  }
}

/* ── toasts ──────────────────────────────────────────────────────────── */

export function toast(title, body, tone = 'info', ms = 5200) {
  const host = $('#toasts');
  if (!host) return null;
  const node = document.createElement('div');
  node.className = 'toast';
  node.dataset.tone = tone;
  node.setAttribute('role', tone === 'serious' ? 'alert' : 'status');
  node.innerHTML = `<div><b>${esc(title)}</b>${body ? `<p>${esc(body)}</p>` : ''}</div>
    <button type="button" class="link">Dismiss</button>`;
  const kill = () => { node.dataset.gone = '1'; setTimeout(() => node.remove(), 160); };
  node.querySelector('button').addEventListener('click', kill);
  host.appendChild(node);
  if (ms) setTimeout(kill, ms);
  return node;
}
