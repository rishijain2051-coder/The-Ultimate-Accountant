/* app - state, routing and every event. The views only draw. */

import { $, $$, api, copy, debounce, download, esc, num, on, plural, share, toast } from './core.js';
import * as V from './views.js';
import * as X from './exports.js';

const st = {
  settings: {},
  workspaces: [],
  lastWs: null,
  // A run is one client and one period; everything on screen is from one run.
  wsId: null,
  ws: null,
  data: { purchase: null, bank: null, statement: null },
  runIds: { purchase: null, bank: null, statement: null },
  decisions: {},
  decisionsWs: null,             // whose decisions st.decisions holds
  route: { page: 'home', part: null, fp: null },
  module: 'reconcile',
  lastIn: {},                    // where each module was last left, for its tab

  // the Exports module: one register (a workspace of documents) at a time
  ex: { wsId: null, data: null, inv: { filter: 'all', q: '', fy: '' }, openInv: new Set(), docType: '',
        ask: [],                     // payments waiting to be asked about, one settle window each
        downloads: [],               // lists in the Downloads folder this register could take
        form: null,                  // the document open in the correcting window
        histLimit: 25 },

  // the start screen
  mode: 'gst',
  modeChosen: false,
  files: { tally: null, gst: [], sales: null, gstr1: [], stmtFile: null, bankBook: null,
           theirs: null, ours: null },
  addingTo: null,
  samples: {},
  showAllRuns: false,

  // the run page
  filter: {},
  limit: {},
  expandAll: {},
  openFps: new Set(),
  justDone: null,
  all: { open: false, q: '', page: 1 },
  letter: null,
};

const MULTI = new Set(['gst', 'gstr1']);
const SHEET_ONLY = new Set(['tally', 'gst', 'sales', 'gstr1']);

/* ── drawing ─────────────────────────────────────────────────────────── */

function render() {
  const page = st.route.page;
  $('#main').innerHTML = page === 'run' ? V.runPage(st)
    : page === 'settings' ? V.settingsPage(st)
    : page === 'exports' ? X.registerPage(st)
    : page === 'exhome' ? X.exportsHome(st) : V.home(st);
  drawCover();
  st.justDone = null;
}

function drawCover() {
  const ex = st.module === 'exports';
  const current = ex ? st.ex.data?.workspace : st.ws;
  const mine = st.workspaces.filter(w => (w.module === 'exports') === ex);
  const sw = $('#runSwitch');
  const name = V.runName(current);
  sw.hidden = !name;
  $('#runName').textContent = name;
  $('#runMenu').innerHTML = `<p class="menu-head">${ex ? 'Switch to another register' : 'Switch to another run'}</p>
    ${mine.slice(0, 10).map(w => `<button type="button" data-open-run="${esc(w.id)}"
      ${w.id === current?.id ? 'aria-current="true"' : ''}>${esc(V.runName(w))}</button>`).join('')}
    <a href="${ex ? '#/exports' : '#/'}" data-act="all-runs-home">${ex ? 'See every register' : 'See every run'}</a>`;
  // Each module's tab returns to where it was left.
  const lastEx = st.workspaces.find(w => w.module === 'exports');
  $$('[data-module]').forEach(a => {
    const m = a.dataset.module;
    a.href = st.lastIn[m] || (m === 'exports' ? (lastEx ? `#/exports/${lastEx.id}` : '#/exports') : '#/');
    if (m === st.module) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  const gold = $('#goldAct');
  gold.dataset.act = ex ? 'add-docs' : 'start-new';
  gold.querySelector('.wide').textContent = ex ? 'Add documents' : 'Start a new check';
  gold.querySelector('.narrow').textContent = ex ? 'Add' : 'New check';
}

/** Re-draw without losing the reader's place, and put focus back. */
function redrawInPlace(focusSel) {
  const y = window.scrollY;
  render();
  window.scrollTo(0, y);
  if (focusSel) $(focusSel)?.focus({ preventScroll: true });
}

const STEPS = ['Reading your files', 'Matching the entries', 'Writing down why each open entry is open'];
const EX_STEPS = ['Reading the documents', 'Filing each one against its invoice', 'Writing down what is still missing'];

function working(on, title, step = 0, steps = STEPS) {
  const w = $('#working');
  w.hidden = !on;
  if (!on) return;
  $('#workingTitle').textContent = title;
  $('#workingSteps').innerHTML = steps.map((s, i) => `<li data-state="${
    i < step ? 'done' : i === step ? 'now' : 'next'}">${esc(s)}</li>`).join('');
}

/* ── routing ─────────────────────────────────────────────────────────── */

function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

async function route() {
  const [page, wsId, part, fp] = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean)
    .map(decodeURIComponent);
  const before = { ...st.route };

  if (page === 'run' && wsId && st.workspaces.find(w => w.id === wsId)?.module === 'exports') {
    location.replace(`#/exports/${wsId}`);
    return;
  }
  if (page === 'run' && wsId) {
    if (wsId !== st.wsId || !Object.values(st.data).some(Boolean) || st.decisionsWs !== wsId) {
      if (!await openRun(wsId)) { location.replace('#/'); return; }
    }
    const first = V.PARTS.find(([, k]) => st.data[k]);
    if (!first) { location.replace('#/'); return; }
    const want = V.KIND_OF[part] && st.data[V.KIND_OF[part]] ? part : first[0];
    if (want !== part) { location.replace(`#/run/${wsId}/${want}`); return; }
    if (before.part !== want || before.page !== 'run') resetPartView();
    st.route = { page: 'run', part: want, fp: fp || null };
    if (fp) {
      // A link to one entry must land on it, whatever the list was filtered to.
      st.filter = {};
      st.limit = { open: 1e9, sales: 1e9 };
      st.openFps.add(fp);
    }
  } else if (page === 'exports' && wsId) {
    if (wsId !== st.ex.wsId || !st.ex.data || st.decisionsWs !== wsId) {
      if (!await openRegister(wsId)) { location.replace('#/exports'); return; }
    }
    if (before.page !== 'exports') resetPartView();
    st.route = { page: 'exports', fp: part || null };
    lookForDownloads();
    if (part) {
      st.filter = {};
      st.limit = { open: 1e9 };
      st.openFps.add(part);
    }
  } else if (page === 'exports') {
    st.route = { page: 'exhome' };
  } else if (page === 'settings') {
    st.route = { page: 'settings' };
    refreshAi().then(watchAi);
  } else {
    st.route = { page: 'home' };
  }
  if (st.route.page !== 'settings') {
    st.module = ['exports', 'exhome'].includes(st.route.page) ? 'exports' : 'reconcile';
    st.lastIn[st.module] = location.hash || '#/';
  }

  render();
  if (st.route.fp) showEntry(st.route.fp);
  else if (before.page !== st.route.page || before.part !== st.route.part) window.scrollTo(0, 0);
}

function resetPartView() {
  st.filter = {};
  st.limit = {};
  st.expandAll = {};
  st.openFps = new Set();
  st.all = { open: false, q: '', page: 1 };
  st.letter = null;
}

function showEntry(fp) {
  const el = document.getElementById(`e-${fp}`);
  if (!el) return;
  el.scrollIntoView({ block: 'center' });
  el.querySelector('.entry-line')?.focus({ preventScroll: true });
}

/* ── runs ────────────────────────────────────────────────────────────── */

async function refreshWorkspaces() {
  try {
    st.workspaces = (await api.get('/api/workspaces')).workspaces || [];
  } catch { /* the list is a nicety */ }
}

/**
 * Load one run with every part it holds. `keep` is a part just produced by a
 * check, used as it came back rather than fetched again.
 */
async function openRun(wsId, keep = null) {
  working(true, 'Opening the run', 2);
  try {
    const ws = await api.get(`/api/workspaces/${encodeURIComponent(wsId)}`);
    const data = { purchase: null, bank: null, statement: null };
    const ids = { purchase: null, bank: null, statement: null };
    if (keep) { data[keep.kind] = keep.data; ids[keep.kind] = keep.data.run_id || null; }
    for (const [kind, part] of Object.entries(ws.parts || {})) {
      if (data[kind] || !(kind in data)) continue;
      try {
        data[kind] = await api.get(`/api/runs/${part.id}`);
        ids[kind] = part.id;
      } catch { /* one part that will not load must not hide the rest */ }
    }
    const { decisions } = await api.get(`/api/decisions?workspace_id=${encodeURIComponent(wsId)}`);
    Object.assign(st, { wsId, ws, data, runIds: ids, decisions: decisions || {}, decisionsWs: wsId });
    resetPartView();
    api.post(`/api/workspaces/${encodeURIComponent(wsId)}/open`).catch(() => {});
    return true;
  } catch (err) {
    toast('That run could not be opened', err.message, 'serious');
    return false;
  } finally {
    working(false);
  }
}

/* ── export registers ────────────────────────────────────────────────── */

function adoptRegister(data) {
  if (data.workspace_id !== st.ex.wsId) {
    st.ex.openInv = new Set();
    st.ex.inv = { filter: 'all', q: '', fy: '' };
    st.ex.docType = '';
    st.ex.downloads = [];
  }
  Object.assign(st.ex, { wsId: data.workspace_id, data });
  st.decisions = data.decisions || {};
  st.decisionsWs = data.workspace_id;
}

async function openRegister(wsId) {
  working(true, 'Opening the register', 2, EX_STEPS);
  try {
    adoptRegister(await api.get(`/api/exports/${encodeURIComponent(wsId)}`));
    api.post(`/api/workspaces/${encodeURIComponent(wsId)}/open`).catch(() => {});
    return true;
  } catch (err) {
    toast('That register could not be opened', err.message, 'serious');
    return false;
  } finally {
    working(false);
  }
}

/**
 * ICEGATE, DGFT and bank lists waiting in the Downloads folder, looked for when
 * a register opens and whenever the app comes back into view - after saving a
 * download in the browser, say. The server only reads that folder.
 */
let lookedAt = 0;
async function lookForDownloads() {
  const ws = st.ex.wsId;
  if (st.route.page !== 'exports' || !ws || Date.now() - lookedAt < 3000) return;
  lookedAt = Date.now();
  try {
    const { files } = await api.get(`/api/exports/${encodeURIComponent(ws)}/downloads`);
    if (ws !== st.ex.wsId) return;
    const before = JSON.stringify(st.ex.downloads || []);
    st.ex.downloads = files || [];
    if (before !== JSON.stringify(st.ex.downloads)) redrawInPlace();
  } catch {
    // An offer, nothing more: when the folder cannot be read there is simply none.
  }
}

/** Read and file documents, then show the register they went into. */
async function fileDocuments(call, title) {
  working(true, title, 0, EX_STEPS);
  const t1 = setTimeout(() => working(true, title, 1, EX_STEPS), 1500);
  const t2 = setTimeout(() => working(true, title, 2, EX_STEPS), 3500);
  try {
    const data = await call();
    adoptRegister(data);
    await refreshWorkspaces();
    go(`#/exports/${data.workspace_id}`);
    const added = data.added?.length || 0, again = data.repeats?.length || 0, unread = data.unread?.length || 0;
    const kept = again ? `${plural(again, 'was', 'were')} already here, so ${again === 1 ? 'it was' : 'they were'} kept once.` : '';
    if (data.pending?.length && added) {
      // Nothing counts until it is checked: take the reader to what was read.
      toast(`Read ${plural(added, 'document')}`, [`Check what was read from ${added === 1 ? 'it' : 'each'}, then post it.`,
        unread ? `${plural(unread, 'was', 'were')} not recognised.` : '', kept].filter(Boolean).join(' '), unread ? 'serious' : 'ok');
      document.getElementById('review')?.scrollIntoView({ block: 'start' });
    } else {
      toast(added ? `Filed ${plural(added, 'document')}` : 'Nothing new to file',
        [kept, unread ? `${plural(unread, 'could not be read', 'could not be read')} – see Needs a look.` : '']
          .filter(Boolean).join(' ') || 'Each one is filed against its invoice.', unread ? 'serious' : 'ok');
    }
  } catch (err) {
    toast('Those documents could not be filed', err.message, 'serious', 0);
  } finally {
    clearTimeout(t1);
    clearTimeout(t2);
    working(false);
  }
}

/* ── setting a payment against invoices ─────────────────────────────── */

const cents = v => Math.round(v * 100) / 100;

function nextSettle() {
  const id = st.ex.ask?.shift();
  if (id) openSettle(id);
}

async function openSettle(remId) {
  const dlg = $('#settle');
  try {
    const o = await api.get(`/api/exports/${encodeURIComponent(st.ex.wsId)}/settle/${encodeURIComponent(remId)}`);
    dlg.innerHTML = X.settleDialog(o);
    Object.assign(dlg.dataset, { free: o.free, cur: o.remittance.currency });
    settleSum();
    if (!dlg.open) dlg.showModal();
    ($('input[data-split]', dlg) || $('[data-settle-close]', dlg))?.focus();
  } catch (err) {
    toast('That payment could not be opened', err.message, 'serious');
    nextSettle();
  }
}

/** The running total under the amounts, and whether they can be saved. */
function settleSum() {
  const dlg = $('#settle');
  const free = Number(dlg.dataset.free) || 0, cur = dlg.dataset.cur;
  let total = 0, bad = false;
  for (const i of $$('input[data-split]', dlg)) {
    const v = i.value.trim() === '' ? 0 : Number(i.value);
    const wrong = !Number.isFinite(v) || v < 0 || v > Number(i.max) + 0.005;
    i.setAttribute('aria-invalid', String(wrong));
    bad ||= wrong;
    if (!wrong) total = cents(total + v);
  }
  const left = cents(free - total), over = left < -0.005;
  const sum = $('[data-settle-sum]', dlg);
  if (sum) {
    sum.innerHTML = bad ? '<span class="over">An amount is more than that invoice has left to come.</span>'
      : over ? `<span class="over">That is ${esc(X.fc(-left, cur))} more than the payment has to place.</span>`
      : `${esc(X.fc(total, cur))} set against invoices${left > 0.005
        ? ` · ${esc(X.fc(left, cur))} stays on the payment, not yet placed` : ' · all of it placed'}.`;
  }
  const save = $('[data-settle-save]', dlg);
  if (save) save.disabled = bad || over;
}

async function saveSettle(body) {
  const dlg = $('#settle');
  const err = $('[data-settle-err]', dlg);
  try {
    adoptRegister(await api.post(`/api/exports/${encodeURIComponent(st.ex.wsId)}/settle`, body));
    redrawInPlace();
    dlg.close();
    toast(body.forget ? 'Back to what the documents say' : 'Payment set against the invoices',
      body.forget ? 'The payment is placed as its documents place it, and suggestions come back.'
        : 'Each invoice shows it under Payment, with a Change to move it.', 'ok', 4200);
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  }
}

/* ── checking, posting and correcting what was read ─────────────────── */

async function postDocs(body) {
  try {
    const view = await api.post(`/api/exports/${encodeURIComponent(st.ex.wsId)}/post`, body);
    adoptRegister(view);
    await refreshWorkspaces();
    redrawInPlace();
    toast(`Posted ${plural(view.posted.length, 'document')}`,
      'Each is filed against its invoice now, and the history says what went in.', 'ok', 3600);
    // A bank advice that names no invoice: ask which ones, one payment at a time.
    st.ex.ask = [...(view.ask_settle || [])];
    nextSettle();
  } catch (err) {
    toast('That could not be posted', err.message, 'serious');
  }
}

async function openDocEdit(id, kind = '') {
  const dlg = $('#docEdit');
  try {
    const o = await api.get(`/api/exports/${encodeURIComponent(st.ex.wsId)}/documents/${encodeURIComponent(id)}/form${
      kind ? `?kind=${encodeURIComponent(kind)}` : ''}`);
    st.ex.form = o;
    dlg.innerHTML = X.docForm(o, st.ex.wsId);
    if (!dlg.open) dlg.showModal();
    $('select[data-doc-kind], input[data-f], select[data-f]', dlg)?.focus();
  } catch (err) {
    toast('That document could not be opened', err.message, 'serious');
  }
}

/** A row's values from its boxes; a row not changed is sent by its place alone, and kept as it was. */
function rowValues(t, tr) {
  if (!tr.dataset.editing) return { _i: Number(tr.dataset.i) };
  const out = tr.dataset.new ? {} : { _i: Number(tr.dataset.i) };
  for (const i of $$('[data-c]', tr)) out[i.dataset.c] = i.type === 'checkbox' ? i.checked : i.value;
  return out;
}

async function saveDocEdit(form) {
  const o = st.ex.form, dlg = $('#docEdit');
  const fields = Object.fromEntries($$('[data-f]', form).map(i => [i.dataset.f, i.type === 'checkbox' ? i.checked : i.value]));
  const tables = {};
  for (const sec of $$('[data-table]', form)) {
    const t = o.tables.find(x => x.key === sec.dataset.table);
    const trs = $$('tbody tr', sec);
    if (!trs.some(tr => tr.dataset.editing || tr.dataset.removed)) continue;       // untouched: not sent
    tables[t.key] = trs.filter(tr => !tr.dataset.removed).map(tr => rowValues(t, tr));
  }
  const err = $('[data-doc-err]', dlg);
  try {
    const view = await api.post(`/api/exports/${encodeURIComponent(st.ex.wsId)}/documents/${encodeURIComponent(o.id)}/correct`,
      { type: o.type, fields, tables });
    adoptRegister(view);
    redrawInPlace();
    dlg.close();
    toast(view.changes ? `Saved ${plural(view.changes, 'correction')}` : 'Nothing was changed',
      view.changes ? 'The register is worked out again with them, and the history keeps what each said before.' : null, 'ok', 3600);
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
    err.scrollIntoView({ block: 'nearest' });
  }
}

/** Read a kept file again with today's reader; it then waits to be checked again. */
async function rereadDoc(b) {
  const o = st.ex.form, dlg = $('#docEdit'), err = $('[data-doc-err]', dlg);
  b.disabled = true;
  b.textContent = 'Reading the file again…';
  try {
    const view = await api.post(`/api/exports/${encodeURIComponent(st.ex.wsId)}/documents/${encodeURIComponent(o.id)}/reread`, {});
    adoptRegister(view);
    redrawInPlace();
    dlg.close();
    if (view.changes) document.getElementById(`p-${o.id}`)?.scrollIntoView({ block: 'start' });
    toast(view.changes ? 'Read again – check it before it goes in' : 'It reads the same as before',
      view.changes ? 'It waits under Check what was read, showing what the new reading found.' : 'Nothing was changed.',
      'ok', 4200);
  } catch (e) {
    b.disabled = false;
    b.textContent = 'Read the file again';
    err.textContent = e.message;
    err.hidden = false;
  }
}

function addDocuments(list) {
  const files = list.filter(x => /\.(pdf|png|jpe?g|tiff?|bmp|webp|xlsx?|csv|json)$/i.test(x.name));
  if (!files.length) {
    if (list.length) toast('That file cannot be read here', 'Use a PDF, a scan or photo, an Excel sheet or the GSTR-1 JSON.', 'serious');
    return;
  }
  const fd = new FormData();
  files.forEach(x => fd.append('files', x));
  if (st.route.page === 'exports' && st.ex.wsId) fd.append('workspace_id', st.ex.wsId);
  fileDocuments(() => api.form('/api/exports/documents', fd), `Reading ${plural(files.length, 'document')}`);
}

async function adopt(kind, data) {
  st.addingTo = null;
  clearFiles();
  await refreshWorkspaces();
  const wsId = data.workspace_id;
  if (!wsId || !await openRun(wsId, { kind, data })) return;
  go(`#/run/${wsId}/${V.PART_OF[kind]}`);
  const n = (data.all_records || []).filter(V.isOpen).length;
  toast('Checked', n ? `${num(n)} ${n === 1 ? 'entry needs' : 'entries need'} a look. Each one says why.`
    : 'Everything agrees.', 'ok');
}

function clearFiles() {
  st.files = { tally: null, gst: [], sales: null, gstr1: [], stmtFile: null, bankBook: null,
               theirs: null, ours: null };
  st.modeChosen = false;
}

/* ── running a check ─────────────────────────────────────────────────── */

const val = (sel, fallback = '') => $(sel)?.value ?? String(fallback);

async function perform(call, kind, scanning = false) {
  working(true, scanning ? 'Reading the scan' : 'Reading your files', 0);
  const t1 = setTimeout(() => working(true, 'Matching the entries', 1), scanning ? 2500 : 700);
  const t2 = setTimeout(() => working(true, 'Writing down why each open entry is open', 2),
    scanning ? 5000 : 1600);
  try {
    const data = await call();
    clearTimeout(t1);
    clearTimeout(t2);
    await adopt(kind, data);
  } catch (err) {
    toast('Those files could not be checked', err.message, 'serious', 0);
  } finally {
    clearTimeout(t1);
    clearTimeout(t2);
    working(false);
  }
}

function runCheck(mode) {
  const f = st.files;
  const fd = new FormData();
  let url, kind;
  if (mode === 'gst') {
    url = '/api/reconcile'; kind = 'purchase';
    fd.append('tally_file', f.tally);
    f.gst.forEach(x => fd.append('gst_files', x));
    if (f.sales) fd.append('sales_file', f.sales);
    f.gstr1.forEach(x => fd.append('gstr1_files', x));
    fd.append('tolerance', val('#inpTolerance', st.settings.tolerance ?? 2));
    fd.append('fy_year', val('#inpFy'));
    fd.append('gstin', val('#inpGstin', st.settings.gstin).trim().toUpperCase());
    fd.append('entity_name', val('#inpEntity', st.settings.entity_name).trim());
    for (const k of ['date_window_days', 'party_threshold', 'probable_threshold', 'value_pct_tolerance']) {
      if (st.settings[k] != null) fd.append(k, st.settings[k]);
    }
  } else if (mode === 'bank') {
    url = '/api/bank/reconcile'; kind = 'bank';
    fd.append('statement_file', f.stmtFile);
    fd.append('book_file', f.bankBook);
    fd.append('force_ocr', $('#inpForceOcr')?.checked ? 'true' : 'false');
    fd.append('amount_tolerance', val('#inpBankTol', 1));
    fd.append('date_window_days', val('#inpBankWindow', 7));
    fd.append('wide_date_window_days', val('#inpBankWide', 45));
  } else {
    url = '/api/statement/reconcile'; kind = 'statement';
    fd.append('their_file', f.theirs);
    fd.append('our_file', f.ours);
    fd.append('relation', val('#inpRelation', 'supplier'));
    fd.append('party_name', val('#inpPartyName').trim());
    fd.append('amount_tolerance', val('#inpStmtTol', 1));
    fd.append('cross_check_gst', $('#inpCrossGst')?.checked === false ? 'false' : 'true');
  }
  if (st.addingTo) fd.append('workspace_id', st.addingTo);
  const scan = [f.stmtFile, f.theirs].some(x => x && /\.(pdf|png|jpe?g|tiff?|bmp|webp)$/i.test(x.name));
  perform(() => api.form(url, fd), kind, scan);
}

function runSample(which) {
  const into = st.addingTo ? `workspace_id=${encodeURIComponent(st.addingTo)}` : '';
  if (which === 'gst') {
    perform(() => api.post(`/api/load-sample?${into}`), 'purchase');
  } else if (which === 'bank' || which === 'bank-scan') {
    const scan = which === 'bank-scan';
    perform(() => api.post(`/api/bank/load-sample?scanned=${scan}&${into}`), 'bank', scan);
  } else {
    perform(() => api.post(`/api/statement/load-sample?${into}`), 'statement');
  }
}

function takeFiles(slot, list) {
  if (slot === 'exdocs') { addDocuments(list); return; }
  const pattern = SHEET_ONLY.has(slot) ? /\.xlsx?$/i
    : /\.(xlsx?|csv|txt|tsv|pdf|png|jpe?g|tiff?|bmp|webp)$/i;
  const files = list.filter(x => pattern.test(x.name));
  if (list.length && !files.length) {
    toast('That file cannot be read here', SHEET_ONLY.has(slot)
      ? 'This one needs an Excel file (.xlsx) exported from Tally or the GST portal.'
      : 'Use an Excel, CSV or PDF file, or a photo of the page.', 'serious');
    return;
  }
  st.files[slot] = MULTI.has(slot) ? files : (files[0] || null);
  st.modeChosen = true;
  // Only the start screen's own blanks re-draw, so typed settings survive.
  const y = window.scrollY;
  const open = $$('details.more').map(d => d.open);
  const inputs = Object.fromEntries($$('.mode-body input:not([type=file]), .mode-body select')
    .map(i => [i.id, i.type === 'checkbox' ? i.checked : i.value]));
  render();
  $$('details.more').forEach((d, i) => { d.open = !!open[i]; });
  for (const [id, v] of Object.entries(inputs)) {
    const i = document.getElementById(id);
    if (!i) continue;
    if (i.type === 'checkbox') i.checked = v; else i.value = v;
  }
  window.scrollTo(0, y);
}

/* ── decisions ───────────────────────────────────────────────────────── */

function findRecord(fp) {
  if (st.route.page === 'exports') return (st.ex.data?.all_records || []).find(r => r.fp === fp) || null;
  for (const d of Object.values(st.data)) {
    const hit = (d?.all_records || []).find(r => r.fp === fp)
      || (d?.sales?.all_records || []).find(r => r.fp === fp);
    if (hit) return hit;
  }
  return null;
}

const DECISION_TOAST = {
  resolved: ['Ticked off', 'It stays ticked off when you open this run again.'],
  ignore: ['Left out', 'It will be treated as settled from now on.'],
  follow_up: ['Marked as being chased', 'It stays on the list until you tick it off.'],
  match: ['Recorded as the same bill', 'The pair will count as matched the next time this run is checked.'],
  clear: ['Undone', 'The entry is back as it was.'],
};

const EX_TOAST = {
  match: ['Payment counted against the invoice', 'Its line in the register now shows it, with an Undo.'],
  ignore: ['Noted', 'That pairing will not be suggested again.'],
};

async function decide(fp, action, did = '') {
  const ex = st.route.page === 'exports';
  const rec = findRecord(fp);
  // An export decision carries what it is about - for a payment, which
  // remittance and which invoice - because the register is worked out afresh;
  // and the button pressed and the entry's title, for the register's history.
  const body = { fp, action, workspace_id: (ex ? st.ex.wsId : st.wsId) || '', note: '',
                 context: ex ? (rec?.counterpart || { status: rec?.status }) : { status: rec?.status, part: st.route.part },
                 ...(ex && rec?.why?.title ? { title: rec.why.title, did } : {}) };
  if (!ex && action === 'match' && rec?.tally_fp) body.counterpart = rec.tally_fp;
  try {
    const { decisions } = await api.post('/api/decisions', body);
    st.decisions = decisions || {};
    if (ex) adoptRegister(await api.get(`/api/exports/${encodeURIComponent(st.ex.wsId)}`));
    if (ex && rec?.status === 'pay_confirm') {
      // The question is answered and leaves the list; show where the money went.
      const f = st.filter.open;
      if (f && !st.ex.data.all_records.some(r => V.groupOf('exports', r) === f.group)) f.group = '';
      if (action === 'match') st.ex.openInv.add(rec.key);
      render();
      document.getElementById(action === 'match' ? `i-${rec.key}` : 'open')?.scrollIntoView({ block: 'start' });
      toast(...EX_TOAST[action], 'ok', 4200);
      return;
    }
    st.justDone = ['resolved', 'ignore', 'match'].includes(action) ? fp : null;
    // If the entry is hidden once ticked, focus moves on to the next one.
    const next = document.getElementById(`e-${fp}`)?.nextElementSibling?.id;
    redrawInPlace();
    const target = document.getElementById(`e-${fp}`) || (next && document.getElementById(next));
    target?.querySelector('.entry-line')?.focus({ preventScroll: true });
    const [title, text] = DECISION_TOAST[action] || ['Saved', ''];
    toast(title, text, 'ok', 3200);
  } catch (err) {
    toast('That could not be saved', err.message, 'serious');
  }
}

/* ── the letter, re-matching, closing ────────────────────────────────── */

async function openLetter() {
  const data = st.data.statement;
  if (!data) return;
  const gstin = data.their_meta?.gstin || '';
  let contact = {};
  try { contact = (await api.get('/api/suppliers')).suppliers?.[gstin] || {}; } catch { /* optional */ }
  let letter = data.letter || {};
  if (st.runIds.statement) {
    try {
      letter = await api.post(`/api/statement/letter/${st.runIds.statement}`,
        { email: contact.email || '', contact: contact.contact || '' });
    } catch { /* the one built with the run will do */ }
  }
  st.letter = { ...letter, contact: { contact: contact.contact || '', email: contact.email || '' } };
  redrawInPlace();
  $('#letter')?.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

async function saveLetterContact() {
  const data = st.data.statement;
  try {
    await api.post('/api/suppliers', { gstin: data.their_meta?.gstin || '',
      name: data.their_meta?.party_name || '', ...st.letter.contact });
    await openLetter();
    toast('Saved', 'The letter now uses these details.', 'ok', 3000);
  } catch (err) {
    toast('Those details could not be saved', err.message, 'serious');
  }
}

async function rematch() {
  const runId = st.runIds.purchase;
  if (!runId) return;
  const patch = {};
  $$('[data-rematch]').forEach(i => { patch[i.dataset.rematch] = Number(i.value); });
  st.addingTo = null;
  await perform(() => api.post(`/api/runs/${runId}/recompute`, patch), 'purchase');
}

async function closeApp() {
  if (!confirm('Close the app now? Everything you have done is already saved.')) return;
  try {
    await api.post('/api/shutdown');
  } catch (err) {
    // A dropped connection is the server going away, which is what was asked.
    if (err?.message && !/fetch|network|load failed/i.test(err.message)) {
      toast('The app could not be closed', err.message, 'serious', 0);
      return;
    }
  }
  $('#working').hidden = true;
  $('#closed').hidden = false;
  document.title = 'Closed - Ultimate Accountant';
}

/* ── updates ─────────────────────────────────────────────────────────── */

function showUpdateNotice() {
  $('#updateReady').hidden = !(st.update?.status === 'ready' && st.update?.can_install);
}

/** Pick up what the background check found; redraw Settings only if it changed. */
async function refreshUpdate() {
  const u = await api.get('/api/update').catch(() => null);
  if (!u) return null;
  const changed = JSON.stringify(u) !== JSON.stringify(st.update);
  st.update = u;
  showUpdateNotice();
  if (changed && st.route.page === 'settings'
      && !document.activeElement?.matches('input[type="text"], input[type="number"]')) redrawInPlace();
  return u;
}

async function checkForUpdate() {
  try {
    await api.post('/api/update/check');
  } catch (err) {
    toast('Could not check for updates', err.message, 'serious');
    return;
  }
  st.update = { ...st.update, status: 'checking' };
  if (st.route.page === 'settings') redrawInPlace();
  for (let i = 0; i < 240; i += 1) {              // up to six minutes, for a slow download
    await new Promise(done => setTimeout(done, 1500));
    const u = await refreshUpdate();
    if (u && !['checking', 'downloading'].includes(u.status)) break;
  }
}

async function installUpdate() {
  const v = st.update?.latest;
  if (!confirm(`Close the app and update it to version ${v}?\n\nEverything you have done is already saved. `
    + 'The app opens again when the update is done.')) return;
  try {
    await api.post('/api/update/install');
  } catch (err) {
    toast('The update could not start', err.message, 'serious', 0);
    await refreshUpdate();
    return;
  }
  working(true, `Updating to version ${v}`, 0, ['Closing the app', 'Installing the new version', 'Opening it again']);
}

/* ── the AI checker ──────────────────────────────────────────────────── */

const aiGetting = a => ['downloading', 'unpacking'].includes(a?.status);

/** Its state; a register on screen is fetched again when a check has finished since. */
async function refreshAi() {
  const a = await api.get('/api/ai').catch(() => null);
  if (!a) return null;
  const before = st.ai;
  st.ai = a;
  if (JSON.stringify(a) === JSON.stringify(before)) return a;
  const typing = document.activeElement?.matches('input[type="text"], input[type="number"], textarea');
  if (st.route.page === 'settings' && !typing) redrawInPlace();
  if (before && JSON.stringify(a.counts) !== JSON.stringify(before.counts) && st.route.page === 'exports'
      && st.ex.wsId && !typing && !$('dialog[open]')) {
    const view = await api.get(`/api/exports/${encodeURIComponent(st.ex.wsId)}`).catch(() => null);
    // Redrawn only when one of this register's own documents has been checked since.
    const states = v => JSON.stringify([...v?.documents || [], ...v?.pending || []].map(x => x.ai?.state || ''));
    if (view && st.route.page === 'exports' && view.workspace_id === st.ex.wsId && states(view) !== states(st.ex.data)) {
      adoptRegister(view);
      redrawInPlace();
    }
  }
  return a;
}

let aiWatching = false;
async function watchAi() {
  if (aiWatching || !aiGetting(st.ai)) return;
  aiWatching = true;
  while (aiGetting(st.ai)) {
    await new Promise(done => setTimeout(done, 1500));
    await refreshAi();
  }
  aiWatching = false;
  if (st.ai?.status === 'ready') toast('The AI checker is ready', 'It starts checking documents once the app has been left alone for a minute.', 'ok', 5000);
}

async function getAi() {
  if (!confirm('Get the AI checker?\n\nAbout 5 GB is downloaded once, and about 4 GB is kept on this computer. '
    + 'You can go on working while it comes down, and remove it again from Settings.')) return;
  try {
    st.ai = await api.post('/api/ai/get');
  } catch (err) {
    toast('The AI checker could not be fetched', err.message, 'serious');
    return;
  }
  redrawInPlace();
  watchAi();
}

async function removeAi() {
  const other = st.ai?.shared || st.ai?.also;      // another app's copy it goes on with
  if (!confirm(other ? `Remove the AI checker’s own copy from this computer?\n\nIt goes on using the one ${other.app} `
    + 'downloaded.' : 'Remove the AI checker from this computer?\n\nWhat it has already read stays with each document. '
    + 'Getting it back downloads it again.')) return;
  try {
    st.ai = await api.post('/api/ai/remove');
    redrawInPlace();
    toast('Removed', other ? `The AI checker goes on using the one ${other.app} downloaded.`
      : 'The AI checker is off this computer.', 'ok', 3000);
  } catch (err) {
    toast('The AI checker could not be removed', err.message, 'serious');
  }
}

/** Put the AI checker's reading in place of the app's: kept as corrections, like any other. */
async function useAiReading(id) {
  try {
    const view = await api.post(`/api/exports/${encodeURIComponent(st.ex.wsId)}/documents/${encodeURIComponent(id)}/ai-use`, {});
    adoptRegister(view);
    redrawInPlace();
    toast(`Saved ${plural(view.changes || 0, 'correction')}`,
      'The AI’s reading is kept as your corrections, each in the history, and can be undone like any other.', 'ok', 4200);
  } catch (err) {
    toast('The AI’s reading could not be used', err.message, 'serious');
  }
}

async function moveData() {
  if (!confirm('Move your data to another folder?\n\nYou choose the folder next. Everything is copied there and '
    + 'checked before the old copy is removed. The app keeps working from the new folder.')) return;
  working(true, 'Moving your data', 0, ['Waiting for you to choose a folder', 'Copying and checking everything']);
  try {
    const d = await api.post('/api/data-folder/move', {});
    st.dataFolder = d;
    if (d.moved) toast('Moved', `Your data is now in ${d.path}.`, 'ok', 0);
    else toast('Nothing moved', 'No folder was chosen.', 'info', 3000);
    redrawInPlace();
  } catch (err) {
    toast('Your data could not be moved', err.message, 'serious', 0);
  } finally {
    working(false);
  }
}

async function saveSetting(key, value) {
  try {
    st.settings = await api.post('/api/settings', { [key]: value });
  } catch (err) {
    toast('That setting could not be saved', err.message, 'serious');
  }
}

/* ── events ──────────────────────────────────────────────────────────── */

function wire() {
  window.addEventListener('hashchange', route);
  const doc = document;

  // cover
  on(doc, 'click', '[data-act="start-new"]', ev => {
    ev.preventDefault();
    st.addingTo = null;
    clearFiles();
    go('#/');
  });
  on(doc, 'click', '[data-open-run]', (ev, b) => {
    $('#runMenu').hidePopover?.();
    go(`#/run/${b.dataset.openRun}`);
  });
  on(doc, 'click', '[data-act="all-runs-home"]', () => {
    $('#runMenu').hidePopover?.();
    st.showAllRuns = true;
    setTimeout(() => $('#runs')?.scrollIntoView({ block: 'start' }), 30);
  });
  on(doc, 'click', '[data-act="add-docs"]', ev => {
    ev.preventDefault();
    const input = $('input[data-file="exdocs"]');
    if (input) input.click(); else go('#/exports');
  });
  $('#runMenu').addEventListener('toggle', ev => {
    if (ev.newState !== 'open') return;
    const r = $('#runSwitch').getBoundingClientRect();
    $('#runMenu').style.left = `${Math.max(8, Math.min(r.left, innerWidth - 460))}px`;
    $('#runMenu').style.top = `${$('.cover').getBoundingClientRect().bottom + 4}px`;
  });

  // the start screen
  on(doc, 'change', 'input[name="mode"]', (ev, input) => {
    st.mode = input.value;
    st.modeChosen = true;
    render();
  });
  on(doc, 'change', 'input[data-file]', (ev, input) => takeFiles(input.dataset.file, [...(input.files || [])]));
  for (const type of ['dragenter', 'dragover']) {
    on(doc, type, '.blank', (ev, b) => { ev.preventDefault(); b.dataset.over = '1'; });
  }
  on(doc, 'dragleave', '.blank', (ev, b) => { delete b.dataset.over; });
  on(doc, 'drop', '.blank', (ev, b) => {
    ev.preventDefault();
    takeFiles(b.dataset.slot, [...(ev.dataTransfer?.files || [])]);
  });
  on(doc, 'click', '[data-run]', (ev, b) => runCheck(b.dataset.run));
  on(doc, 'click', '[data-sample]', (ev, b) => runSample(b.dataset.sample));
  on(doc, 'click', '[data-add-run]', (ev, b) => {
    st.addingTo = st.addingTo === b.dataset.addRun ? null : b.dataset.addRun;
    go('#/');
    window.scrollTo(0, 0);
  });
  on(doc, 'click', '[data-del-run]', async (ev, b) => {
    const ws = st.workspaces.find(w => w.id === b.dataset.delRun);
    if (!confirm(`Delete “${V.runName(ws)}” and every check saved in it? This cannot be undone.`)) return;
    try {
      await api.del(`/api/workspaces/${encodeURIComponent(b.dataset.delRun)}`);
      if (st.wsId === b.dataset.delRun) {
        Object.assign(st, { wsId: null, ws: null, data: { purchase: null, bank: null, statement: null },
                            runIds: { purchase: null, bank: null, statement: null } });
      }
      if (st.ex.wsId === b.dataset.delRun) Object.assign(st.ex, { wsId: null, data: null });
      for (const m of Object.keys(st.lastIn)) if (st.lastIn[m].includes(b.dataset.delRun)) delete st.lastIn[m];
      if (st.addingTo === b.dataset.delRun) st.addingTo = null;
      await refreshWorkspaces();
      render();
      toast('Run deleted', null, 'ok', 2600);
    } catch (err) {
      toast('That run could not be deleted', err.message, 'serious');
    }
  });

  // the run page
  on(doc, 'click', '.entry-line', (ev, b) => {
    const li = b.closest('.entry');
    const fp = li.dataset.fp;
    if (st.openFps.has(fp)) st.openFps.delete(fp); else st.openFps.add(fp);
    const kind = li.dataset.kind;
    const rec = findRecord(fp);
    if (!rec) return;
    li.outerHTML = V.entry(st, kind, rec);
    const fresh = document.getElementById(`e-${fp}`);
    if (fresh && st.openFps.has(fp)) fresh.dataset.opening = '1';
    fresh?.querySelector('.entry-line')?.focus({ preventScroll: true });
  });
  on(doc, 'click', '[data-decide]', (ev, b) => decide(b.dataset.fp, b.dataset.decide, b.textContent.trim()));
  on(doc, 'click', '[data-copy-link]', async (ev, b) => {
    const url = st.route.page === 'exports'
      ? `${location.origin}${location.pathname}#/exports/${st.ex.wsId}/${b.dataset.copyLink}`
      : `${location.origin}${location.pathname}#/run/${st.wsId}/${st.route.part}/${b.dataset.copyLink}`;
    const ok = await copy(url);
    toast(ok ? 'Link copied' : 'The link could not be copied',
      ok ? 'Opening it brings you straight back to this entry, already open.' : url, ok ? 'ok' : 'serious', 3600);
  });
  on(doc, 'click', '[data-copy-text]', async (ev, b) => {
    const ok = await copy(b.dataset.copyText);
    toast(ok ? `Copied ${b.dataset.copyText}` : 'That could not be copied',
      ok ? 'Paste it into the box on ICEGATE.' : b.dataset.copyText, ok ? 'ok' : 'serious', 2400);
  });
  on(doc, 'change', '[data-group-select]', (ev, sel) => {
    const id = sel.dataset.groupSelect;
    const f = st.filter[id];
    if (sel.value === '__fps') return;
    f.group = sel.value;
    f.fps = null;
    st.limit[id] = 150;
    redrawInPlace(`[data-group-select="${id}"]`);
  });
  // The groups listed in the margin filter the list and take you to it.
  on(doc, 'click', '[data-pick]', (ev, b) => {
    const f = st.filter.open || (st.filter.open = { group: '', q: '', hideDone: false, fps: null });
    f.group = f.group === b.dataset.pick ? '' : b.dataset.pick;
    f.fps = null;
    st.limit.open = 150;
    redrawInPlace();
    $('#open')?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  });
  on(doc, 'click', '[data-pick-group]', (ev, b) => {
    const kind = V.KIND_OF[st.route.part];
    const block = kind === 'bank' ? st.data.bank?.brs : st.data.statement?.bridge;
    const group = (block?.groups || []).find(g => g.key === b.dataset.pickGroup);
    if (!group) return;
    st.filter.open = { group: '', q: '', hideDone: false, fps: group.rows.map(r => r.fp) };
    render();
    $('#open')?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  });
  on(doc, 'input', '[data-search]', debounce((ev, input) => {
    const id = input.dataset.search;
    st.filter[id].q = input.value;
    const caret = input.selectionStart;
    redrawInPlace();
    const box = $(`[data-search="${id}"]`);
    box?.focus({ preventScroll: true });
    box?.setSelectionRange(caret, caret);
  }, 200));
  on(doc, 'change', '[data-hide-done]', (ev, input) => {
    st.filter[input.dataset.hideDone].hideDone = input.checked;
    redrawInPlace(`[data-hide-done="${input.dataset.hideDone}"]`);
  });
  on(doc, 'click', '[data-expand]', (ev, b) => {
    const id = b.dataset.expand;
    st.expandAll[id] = !st.expandAll[id];
    const fps = $$(`#${id} .entry`).map(li => li.dataset.fp);
    fps.forEach(fp => (st.expandAll[id] ? st.openFps.add(fp) : st.openFps.delete(fp)));
    redrawInPlace(`[data-expand="${id}"]`);
  });
  on(doc, 'click', '[data-more]', (ev, b) => {
    st.limit[b.dataset.more] = (st.limit[b.dataset.more] || 150) + 150;
    redrawInPlace();
  });
  on(doc, 'click', '[data-jump]', (ev, b) => {
    const target = document.getElementById(b.dataset.jump);
    target?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    target?.querySelector('h1, h2')?.setAttribute('tabindex', '-1');
    target?.querySelector('h1, h2')?.focus({ preventScroll: true });
  });
  on(doc, 'click', '[data-act="add-to-run"]', () => {
    st.addingTo = st.wsId;
    clearFiles();
    go('#/');
  });
  on(doc, 'click', '[data-act="stop-adding"]', () => { st.addingTo = null; render(); });
  on(doc, 'click', '[data-act="all-runs"]', () => { st.showAllRuns = true; redrawInPlace(); });
  on(doc, 'click', '[data-act="toggle-all"]', () => {
    st.all.open = !st.all.open;
    redrawInPlace('[data-act="toggle-all"]');
  });
  on(doc, 'input', '[data-all-search]', debounce((ev, input) => {
    st.all.q = input.value;
    st.all.page = 1;
    const caret = input.selectionStart;
    redrawInPlace();
    const box = $('[data-all-search]');
    box?.focus({ preventScroll: true });
    box?.setSelectionRange(caret, caret);
  }, 200));
  on(doc, 'click', '[data-page]', (ev, b) => {
    st.all.page = Number(b.dataset.page);
    redrawInPlace();
    $('#all')?.scrollIntoView({ block: 'start' });
  });
  on(doc, 'click', 'tr[data-goto-fp]', (ev, tr) => {
    go(`#/run/${st.wsId}/${st.route.part}/${tr.dataset.gotoFp}`);
  });
  on(doc, 'click', '[data-dl]', async (ev, b) => {
    const kind = V.KIND_OF[st.route.part];
    const runId = st.runIds[kind];
    const what = b.dataset.dl;
    const path = what.includes('?') ? what.replace('?', `/${runId}?`) : `${what}/${runId}`;
    const t = toast('Preparing the file', b.textContent.trim(), 'info', 0);
    try {
      const name = await download(`/api/download/${path}`);
      t?.remove();
      toast('Saved to your downloads', name, 'ok');
    } catch (err) {
      t?.remove();
      toast('That file could not be made', err.message, 'serious', 0);
    }
  });
  // the export register
  on(doc, 'click', '[data-act="ex-sample"]', () =>
    fileDocuments(() => api.post('/api/exports/load-sample'), 'Reading the sample documents'));
  on(doc, 'click', '[data-inv]', (ev, b) => {
    const key = b.dataset.inv;
    if (st.ex.openInv.has(key)) st.ex.openInv.delete(key); else st.ex.openInv.add(key);
    redrawInPlace(`[data-inv="${CSS.escape(key)}"]`);
  });
  on(doc, 'change', '[data-inv-filter]', (ev, sel) => {
    st.ex.inv.filter = sel.value;
    redrawInPlace('[data-inv-filter]');
  });
  on(doc, 'change', '[data-inv-fy]', (ev, sel) => {
    st.ex.inv.fy = sel.value;
    redrawInPlace('[data-inv-fy]');
  });
  on(doc, 'input', '[data-inv-search]', debounce((ev, input) => {
    st.ex.inv.q = input.value;
    const caret = input.selectionStart;
    redrawInPlace();
    const box = $('[data-inv-search]');
    box?.focus({ preventScroll: true });
    box?.setSelectionRange(caret, caret);
  }, 200));
  on(doc, 'change', '[data-doc-type]', (ev, sel) => {
    st.ex.docType = sel.value;
    redrawInPlace('[data-doc-type]');
  });
  on(doc, 'click', '[data-goto-entry]', (ev, b) => go(`#/exports/${st.ex.wsId}/${b.dataset.gotoEntry}`));
  // the settle window: which invoices a payment is for
  on(doc, 'click', '[data-settle]', (ev, b) => { st.ex.ask = []; openSettle(b.dataset.settle); });
  on(doc, 'input', 'input[data-split]', () => settleSum());
  on(doc, 'click', '[data-settle-fill]', () => {
    const dlg = $('#settle');
    let left = Number(dlg.dataset.free) || 0;
    for (const i of $$('input[data-split]', dlg)) {
      const take = cents(Math.max(0, Math.min(Number(i.max) || 0, left)));
      i.value = take > 0 ? take.toFixed(2) : '';
      left = cents(left - take);
    }
    settleSum();
  });
  on(doc, 'click', '[data-settle-close]', () => $('#settle').close());
  on(doc, 'click', '[data-settle-forget]', () =>
    saveSettle({ remittance: $('[data-settle-form]').dataset.settleForm, splits: [], forget: true }));
  on(doc, 'submit', '[data-settle-form]', (ev, form) => {
    ev.preventDefault();
    const splits = $$('input[data-split]', form)
      .map(i => ({ invoice: i.dataset.split, amount: i.value.trim() === '' ? 0 : Number(i.value) }))
      .filter(s => s.amount > 0);
    saveSettle({ remittance: form.dataset.settleForm, splits });
  });
  $('#settle').addEventListener('close', () => nextSettle());
  // checking what was read, posting it, and correcting it at any time
  on(doc, 'click', '[data-post]', (ev, b) => postDocs({ ids: [b.dataset.post] }));
  on(doc, 'click', '[data-post-all]', () => postDocs({ all: true }));
  on(doc, 'click', '[data-correct]', (ev, b) => openDocEdit(b.dataset.correct));
  on(doc, 'change', '[data-doc-kind]', (ev, sel) => openDocEdit(st.ex.form.id, sel.value));
  on(doc, 'click', '[data-doc-close]', () => $('#docEdit').close());
  on(doc, 'click', '[data-doc-reread]', (ev, b) => rereadDoc(b));
  // lists picked up from the Downloads folder
  on(doc, 'click', '[data-dl-add]', async (ev, b) => {
    const ws = encodeURIComponent(st.ex.wsId);
    await fileDocuments(() => api.post(`/api/exports/${ws}/downloads`, { add: b.dataset.dlAdd }),
      'Reading the file from your Downloads folder');
    lookedAt = 0;
    lookForDownloads();
  });
  on(doc, 'click', '[data-dl-skip]', async (ev, b) => {
    try {
      const { files } = await api.post(`/api/exports/${encodeURIComponent(st.ex.wsId)}/downloads`, { skip: b.dataset.dlSkip });
      st.ex.downloads = files || [];
      redrawInPlace();
      toast('It will not be offered again', 'The file stays in your Downloads folder; drop it here yourself if you want it.', 'ok', 3200);
    } catch (err) {
      toast('That could not be done', err.message, 'serious');
    }
  });
  window.addEventListener('focus', () => lookForDownloads());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) lookForDownloads(); });
  on(doc, 'submit', '[data-doc-form]', (ev, form) => { ev.preventDefault(); saveDocEdit(form); });
  const tableOf = el => st.ex.form.tables.find(x => x.key === el.closest('[data-table]').dataset.table);
  on(doc, 'click', '[data-row-edit]', (ev, b) => {
    const tr = b.closest('tr'), t = tableOf(b);
    tr.outerHTML = X.rowEdit(t, t.rows.find(x => String(x._i) === tr.dataset.i));
  });
  on(doc, 'click', '[data-row-remove]', (ev, b) => {
    const tr = b.closest('tr');
    if (tr.dataset.new) { tr.remove(); return; }
    const gone = !tr.dataset.removed;
    if (gone) tr.dataset.removed = '1'; else delete tr.dataset.removed;
    b.textContent = gone ? 'Keep it' : 'Remove';
  });
  on(doc, 'click', '[data-row-add]', (ev, b) => {
    const sec = b.closest('[data-table]');
    $('tbody', sec).insertAdjacentHTML('beforeend', X.rowEdit(tableOf(b), {}, true));
    $('tbody tr:last-child [data-c]', sec)?.focus();
  });
  on(doc, 'input', '[data-row-filter]', (ev, input) => {
    const q = input.value.trim().toLowerCase();
    for (const tr of $$('tbody tr', input.closest('[data-table]'))) {
      tr.hidden = !!q && !tr.dataset.editing && !tr.textContent.toLowerCase().includes(q);
    }
  });
  on(doc, 'click', '[data-hist-more]', () => {
    st.ex.histLimit = (st.ex.histLimit || 25) + 25;
    redrawInPlace();
  });
  // the user's own yes or no on an invoice's GSTR-1, IGST refund and drawback
  on(doc, 'click', '[data-mark]', async (ev, b) => {
    const { mark: what, markInv: invoice, val } = b.dataset;
    try {
      adoptRegister(await api.post(`/api/exports/${encodeURIComponent(st.ex.wsId)}/mark`,
        { invoice, what, value: val || null }));
      redrawInPlace(`[data-mark="${what}"][data-mark-inv="${CSS.escape(invoice)}"][data-val="${val || 'yes'}"]`);
      toast(val ? 'Your answer is saved' : 'Back to what the documents say',
        val ? 'It counts over the documents until you undo it.' : null, 'ok', 3200);
    } catch (err) {
      toast('That could not be saved', err.message, 'serious');
    }
  });
  on(doc, 'click', '[data-del-doc]', async (ev, b) => {
    const doc_ = [...(st.ex.data?.documents || []), ...(st.ex.data?.pending || [])].find(x => x.id === b.dataset.delDoc);
    if (!confirm(`Remove “${doc_?.name || 'this document'}” from the register? The copy kept here is deleted too.`)) return;
    try {
      adoptRegister(await api.del(`/api/exports/${encodeURIComponent(st.ex.wsId)}/documents/${encodeURIComponent(b.dataset.delDoc)}`));
      await refreshWorkspaces();
      redrawInPlace();
      toast('Removed', 'The register has been worked out again without it.', 'ok', 3000);
    } catch (err) {
      toast('That document could not be removed', err.message, 'serious');
    }
  });
  // kept files, in either module
  on(doc, 'click', '[data-kept]', async (ev, b) => {
    const { kept: act, ws, id = '', ids = '', name: zipName = '' } = b.dataset;
    const base = `/api/files/${encodeURIComponent(ws)}`, one = `${base}/${encodeURIComponent(id)}?download=1`;
    try {
      if (act === 'reveal') {
        await api.post(`${base}/reveal${id ? `?doc=${encodeURIComponent(id)}` : ''}`);
        toast('Opened in File Explorer', id ? 'The file is picked out, ready to drag into an email.' : null, 'ok', 3000);
      } else if (act === 'share') {
        const got = await share(one);
        if (got.how === 'saved') {
          toast('Saved to your downloads', `${got.name}. This kind of file cannot go to the share window, so a copy was saved to hand over instead.`, 'ok');
        }
      } else {
        const t = toast('Preparing the file', null, 'info', 0);
        let name;
        try {
          name = await download(act === 'zip' ? `${base}/zip?ids=${encodeURIComponent(ids)}&name=${encodeURIComponent(zipName)}` : one);
        } finally {
          t?.remove();
        }
        toast('Saved to your downloads', name, 'ok');
      }
    } catch (err) {
      toast('That file could not be handed over', err.message, 'serious');
    }
  });
  on(doc, 'click', '[data-ex-dl]', async (ev, b) => {
    const t = toast('Preparing the file', b.textContent.trim(), 'info', 0);
    try {
      const name = await download(`/api/exports/${encodeURIComponent(st.ex.wsId)}/download/${b.dataset.exDl}`);
      t?.remove();
      toast('Saved to your downloads', name, 'ok');
    } catch (err) {
      t?.remove();
      toast('That file could not be made', err.message, 'serious', 0);
    }
  });

  on(doc, 'click', '[data-act="letter"]', openLetter);
  on(doc, 'click', '[data-act="letter-close"]', () => { st.letter = null; redrawInPlace(); });
  on(doc, 'click', '[data-act="letter-copy"]', async () => {
    toast(await copy(st.letter?.body || '') ? 'Letter copied' : 'The letter could not be copied',
      null, 'ok', 2400);
  });
  on(doc, 'input', '[data-letter]', (ev, input) => { st.letter.contact[input.dataset.letter] = input.value.trim(); });
  on(doc, 'click', '[data-act="letter-save"]', saveLetterContact);
  on(doc, 'click', '[data-act="rematch"]', rematch);
  on(doc, 'click', '[data-act="close-app"]', closeApp);
  on(doc, 'click', '[data-act="data-move"]', moveData);
  on(doc, 'click', '[data-act="update-check"]', checkForUpdate);
  on(doc, 'click', '[data-act="update-install"]', installUpdate);
  on(doc, 'click', '[data-act="ai-get"]', getAi);
  on(doc, 'click', '[data-act="ai-remove"]', removeAi);
  on(doc, 'click', '[data-ai-use]', (ev, b) => useAiReading(b.dataset.aiUse));
  on(doc, 'click', '[data-act="data-reveal"]', () => api.post('/api/data-folder/reveal')
    .catch(err => toast('The folder could not be opened', err.message, 'serious')));

  // settings
  on(doc, 'change', '[data-setting]', async (ev, input) => {
    const v = input.type === 'checkbox' ? input.checked
      : input.type === 'number' ? Number(input.value) : input.value.trim();
    await saveSetting(input.dataset.setting, input.dataset.setting === 'gstin' ? String(v).toUpperCase() : v);
    // A register works its deadlines out from these, so it is worked out again.
    if (input.dataset.setting.startsWith('exim_')) st.ex.data = null;
    if (input.dataset.setting === 'check_updates') {
      if (v && st.update?.automatic) checkForUpdate(); else redrawInPlace();
    }
    if (input.dataset.setting === 'ai_checks') refreshAi();
  });
}

/* ── start ───────────────────────────────────────────────────────────── */

async function boot() {
  try {
    const b = await api.get('/api/bootstrap');
    st.settings = b.settings || {};
    st.workspaces = b.workspaces || [];
    st.lastWs = b.workspace_id || null;
    st.samples = {
      gst: !!b.sample_available,
      bank: !!b.bank_sample_available,
      bankScan: !!(b.bank_sample_available && b.ocr?.available),
      party: !!b.statement_sample_available,
      exports: !!b.exports_sample_available,
    };
    st.eximRules = b.exim_rules || {};
    st.dataFolder = b.data_folder || null;
    st.update = b.update || null;
    showUpdateNotice();
    setInterval(refreshUpdate, 60000);           // the background check reports here
    setInterval(refreshAi, 60000);               // and so do the AI checker's
  } catch (err) {
    toast('The app cannot reach its own server',
      `Close the app and start it again. (${err.message})`, 'serious', 0);
  }
  wire();
  // With no address, go back to the run that was open last time.
  if (!location.hash && st.lastWs) {
    history.replaceState(null, '', `#/run/${st.lastWs}`);
  }
  Promise.resolve(route()).finally(() => window.acLaunch?.done());
}

boot();
