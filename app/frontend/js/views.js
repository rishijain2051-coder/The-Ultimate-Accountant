/* views - every screen as an HTML string, built from state. No events here. */

import { CHEVRON, TICK, day, dayShort, esc, inr, num, plural, when } from './core.js';

/* ── words ───────────────────────────────────────────────────────────── */

export const PARTS = [          // route name, engine kind, label
  ['gst', 'purchase', 'GST purchases'],
  ['bank', 'bank', 'Bank'],
  ['party', 'statement', 'Party statement'],
];
export const KIND_OF = Object.fromEntries(PARTS.map(([p, k]) => [p, k]));
export const PART_OF = Object.fromEntries(PARTS.map(([p, k]) => [k, p]));

// What the engines call each open state, and what a person calls it.
const GST_GROUP = {
  'In GSTR-2B only': 'Not in your books',
  'In Tally only': 'Not filed by the supplier',
  'Value mismatch': 'Amount differs',
  'Invoice no. mismatch': 'Bill number differs',
  'Probable match': 'Probably the same bill',
  'Split booking': 'Entered in parts',
};
const SALES_GROUP = {
  'In GSTR-1 only': 'Reported, not in your books',
  'In Tally only': 'In your books, not reported',
  'Value mismatch': 'Amount differs',
};
const BANK_GROUP = {
  'Cheque issued, not presented': 'Paid, not yet through the bank',
  'Deposit made, not credited': 'Received, not yet in the bank',
  'Bank charges not in books': 'Bank charges to enter',
  'GST on bank charges not in books': 'Bank charges to enter',
  'Interest credited, not in books': 'Interest to enter',
  'Interest debited, not in books': 'Interest to enter',
  'Timing difference': 'Same amount, different dates',
  'Amount differs': 'Amounts differ',
};
const STMT_GROUP = {
  'Billed by them, not in our books': 'Their bills missing from your ledger',
  'Charged to us, not in our books': 'Their bills missing from your ledger',
  'Credit note issued by them, no copy with us': 'Their credits missing from your ledger',
  'Credited by them, not in our books': 'Their credits missing from your ledger',
  'Booked by us, not in their statement': 'Your entries missing from their statement',
  'Billed by us, not in their statement': 'Your entries missing from their statement',
  'Debited by us, not in their statement': 'Your entries missing from their statement',
  'Paid by us, not yet credited by them': 'Your payments they have not recorded',
  'Debit note raised by us, not accepted by them': 'Your notes they have not accepted',
  'Timing difference': 'Same amount, different dates',
  'Interest worked out differently': 'Interest differs',
  'TDS differs': 'TDS differs',
  'Amount disputed': 'Amounts differ',
};

// The Exports module's open entries, by what the reader has to do about them.
const EX_GROUP = {
  not_in_gst: 'GSTR-1 and the refund', sb001: 'GSTR-1 and the refund', sb005: 'GSTR-1 and the refund',
  sb003: 'GSTR-1 and the refund', igst_differs: 'GSTR-1 and the refund', type_differs: 'GSTR-1 and the refund',
  gst_twice: 'GSTR-1 and the refund', no_sb: 'Paperwork missing', no_bl: 'Paperwork missing',
  overdue: 'Payment overdue', due_soon: 'Payment due soon', pay_confirm: 'Payment to confirm',
  advance: 'Money not set against an invoice', pay_unplaced: 'Money not set against an invoice',
  ebrc_due: 'eBRC to generate', ebrc_differs: 'eBRC to correct', ebrc_unplaced: 'Documents not placed',
  bl_unplaced: 'Documents not placed', unread: 'Documents not placed',
  igst_pending: 'Refunds and drawback', igst_short: 'Refunds and drawback', refund_unentered: 'Refunds and drawback',
  dbk_pending: 'Refunds and drawback', dbk_brc: 'Refunds and drawback',
  edpms_open: 'Bank (EDPMS)', edpms_closed: 'Bank (EDPMS)', edpms_missing: 'Bank (EDPMS)',
  advance_shipped: 'Bank (EDPMS)', ai_differs: 'Read differently by the AI',
};

const SETTLED = new Set(['Matched', 'Agreed', 'Batched', 'Superseded']);
export const isOpen = rec => !SETTLED.has(rec.status);

export function groupOf(kind, rec, sales = false) {
  if (kind === 'exports') return EX_GROUP[rec.status] || 'Other';
  if (sales) return SALES_GROUP[rec.status] || rec.status;
  if (kind === 'purchase') return GST_GROUP[rec.status] || rec.status;
  if (kind === 'bank') {
    return BANK_GROUP[rec.category] || BANK_GROUP[rec.status]
      || (rec.bank_fp ? 'In the bank, not in your books' : 'In your books, not in the bank');
  }
  return STMT_GROUP[rec.category] || STMT_GROUP[rec.status] || 'Other differences';
}

/** Red is for money at risk; everything else open is amber. */
export function toneOf(kind, rec) {
  if (kind === 'exports') return rec.tone === 'serious' ? 'serious' : 'look';
  const flags = rec.flags || [];
  if (kind === 'purchase') {
    return rec.status === 'In GSTR-2B only' || flags.includes('sec164_lapsed')
      || flags.includes('sec164_due') ? 'serious' : 'look';
  }
  if (kind === 'bank') {
    return rec.tone === 'risk' || flags.includes('stale_cheque') ? 'serious' : 'look';
  }
  return rec.status === 'Amount disputed' || (rec.age_days || 0) > 180 ? 'serious' : 'look';
}

const DONE = new Set(['resolved', 'ignore', 'match']);
const DECIDED_WORDS = {
  resolved: 'You marked this done', ignore: 'You took this off the list as not a problem',
  match: 'You confirmed these are the same bill', follow_up: 'You are chasing this',
};

function junk(text) {
  const s = String(text ?? '').trim();
  if (!s) return 'no description';
  if (!/[A-Za-z0-9]/.test(s)) return `only “${s}” as its description`;
  return s;
}

/** Where the entry came from, in a few words: who, which number, which side. */
function whoOf(kind, r) {
  if (kind === 'exports') return r.who || '';
  if (kind === 'purchase') {
    const side = r.inv_val_2b != null && r.amt_tally != null ? ''
      : r.inv_val_2b != null ? 'on the GST portal' : 'in your books';
    return [r.party_2b || r.party_tally, r.inv_no_2b || r.vch_no_tally
      ? `no. ${r.inv_no_2b || r.vch_no_tally}` : '', side].filter(Boolean).join(' · ');
  }
  if (kind === 'sales') {
    return [r.party_gstr1 || r.party_tally, r.inv_no_gstr1 || r.vch_no_tally
      ? `no. ${r.inv_no_gstr1 || r.vch_no_tally}` : ''].filter(Boolean).join(' · ');
  }
  if (kind === 'bank') {
    const text = junk(r.bank_fp ? r.narration : r.book_party);
    const ref = r.bank_ref || r.book_vch_no;
    const side = r.bank_fp && r.book_fp ? '' : r.bank_fp ? 'on the bank statement' : 'in your bank book';
    return [text, ref && !text.includes(ref) ? ref : '', side].filter(Boolean).join(' · ');
  }
  const text = junk(r.particulars_theirs || r.particulars_ours);
  const ref = r.ref_theirs || r.ref_ours;
  return [text, ref && !text.includes(ref) ? ref : ''].filter(Boolean).join(' · ');
}

function dateOf(kind, r) {
  return kind === 'exports' ? r.date
    : kind === 'purchase' ? (r.inv_date_2b || r.vch_date_tally)
    : kind === 'sales' ? (r.inv_date_gstr1 || r.vch_date_tally)
    : kind === 'bank' ? (r.date_bank || r.date_book)
    : (r.date_theirs || r.date_ours);
}

function amountOf(kind, r) {
  return kind === 'exports' ? r.inr
    : kind === 'purchase' ? (r.inv_val_2b ?? r.amt_tally)
    : kind === 'sales' ? (r.val_gstr1 ?? r.amt_tally)
    : kind === 'bank' ? (r.bank_amount ?? r.book_amount)
    : (r.owed_theirs ?? r.owed_ours);
}

export function haystack(kind, r) {
  return [whoOf(kind, r), r.why?.title, amountOf(kind, r), r.gstin, r.category]
    .filter(v => v != null).join(' ').toLowerCase();
}

/* ── the cover and the start ─────────────────────────────────────────── */

export function runName(ws) {
  if (!ws) return '';
  return (ws.name || ws.client_name || 'Untitled run')
    .replace(/\d{4}-\d{2}-\d{2}/g, d => day(d)).replace(/ to /g, ' – ');
}

function partsLine(parts) {
  const label = { purchase: 'GST', bank: 'Bank', statement: 'Party' };
  return Object.entries(parts || {}).map(([k, n]) => `${label[k] || k}${n > 1 ? ` ×${n}` : ''}`)
    .join(' · ') || 'nothing in it yet';
}

const MODES = [
  ['gst', 'GST purchases',
   'Your purchase bills against what your suppliers filed on the GST portal (GSTR-2B).'],
  ['bank', 'Bank',
   'Your bank book against the statement the bank sent.'],
  ['party', 'A party’s statement',
   'A supplier’s or customer’s statement of account against your ledger for them.'],
];

function blank(slot, title, hint, files, { multi = false, accept = '' } = {}) {
  const list = Array.isArray(files) ? files : files ? [files] : [];
  const chosen = list.length === 1 ? list[0].name : list.length ? `${list.length} files` : '';
  return `<label class="blank" data-slot="${slot}" data-filled="${chosen ? 1 : 0}">
    <input type="file" ${multi ? 'multiple' : ''} accept="${accept}" data-file="${slot}">
    <span class="blank-title">${esc(title)}</span>
    <span class="blank-hint">${esc(hint)}</span>
    <span class="blank-file">${chosen ? `${TICK}<span>${esc(chosen)}</span>`
      : '<span>Choose the file, or drop it here</span>'}</span>
  </label>`;
}

const SHEETS = '.xlsx,.xls';
const ANY = '.xlsx,.xls,.csv,.pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp';

function field(label, input, hint = '') {
  return `<label class="field"><span>${esc(label)}</span>${input}${hint
    ? `<small>${esc(hint)}</small>` : ''}</label>`;
}

function modeBody(st) {
  const f = st.files, s = st.settings || {};
  const ready = { gst: f.tally && f.gst.length, bank: f.stmtFile && f.bankBook,
                  party: f.theirs && f.ours }[st.mode];
  const samples = st.samples || {};
  if (st.mode === 'gst') {
    return `<div class="blanks">
        ${blank('tally', 'Your purchase register', 'Export it from Tally as an Excel file (.xlsx).', f.tally, { accept: SHEETS })}
        ${blank('gst', 'GSTR-2B from the GST portal', 'One merged file, or every monthly file at once.', f.gst, { multi: true, accept: SHEETS })}
      </div>
      <details class="more"><summary>${CHEVRON}Also check your sales (optional)</summary>
        <div class="blanks">
          ${blank('sales', 'Your sales register', 'From Tally, as Excel.', f.sales, { accept: SHEETS })}
          ${blank('gstr1', 'The GSTR-1 you filed', 'From the GST portal.', f.gstr1, { multi: true, accept: SHEETS })}
        </div></details>
      <details class="more"><summary>${CHEVRON}Matching settings</summary>
        <div class="fields">
          ${field('Financial year', `<select id="inpFy">${fyOptions()}</select>`, 'Read from the file unless you choose.')}
          ${field('Allow a difference of (₹)', `<input type="number" id="inpTolerance" min="0" step="0.5" value="${esc(s.tolerance ?? 2)}">`, 'For rounding.')}
          ${field('Your GSTIN', `<input type="text" id="inpGstin" maxlength="15" spellcheck="false" autocomplete="off" value="${esc(s.gstin || '')}">`)}
          ${field('Business name', `<input type="text" id="inpEntity" autocomplete="off" value="${esc(s.entity_name || '')}" placeholder="Taken from the Tally file">`)}
        </div></details>
      ${goRow('gst', ready, samples.gst)}`;
  }
  if (st.mode === 'bank') {
    return `<div class="blanks">
        ${blank('stmtFile', 'The bank statement', 'Excel, CSV or PDF from the bank. A scan or a photo works too.', f.stmtFile, { accept: ANY })}
        ${blank('bankBook', 'Your bank book', 'The bank ledger from Tally for the same period.', f.bankBook, { accept: '.xlsx,.xls,.csv,.pdf' })}
      </div>
      <details class="more"><summary>${CHEVRON}Matching settings</summary>
        <div class="fields">
          ${field('Allow a difference of (₹)', `<input type="number" id="inpBankTol" min="0" step="0.5" value="${esc(s.bank_amount_tolerance ?? 1)}">`)}
          ${field('Same transaction if within (days)', `<input type="number" id="inpBankWindow" min="0" max="90" value="${esc(s.bank_date_window_days ?? 7)}">`)}
          ${field('Timing difference up to (days)', `<input type="number" id="inpBankWide" min="0" max="365" value="${esc(s.bank_wide_date_window_days ?? 45)}">`)}
          <label class="check"><input type="checkbox" id="inpForceOcr"> Read the statement as a scan, even if it is a PDF with text</label>
        </div></details>
      ${goRow('bank', ready, samples.bank, samples.bankScan)}`;
  }
  return `<div class="blanks">
      ${blank('theirs', 'Their statement', 'Whatever they sent: Excel, PDF, or a photo of a printout.', f.theirs, { accept: ANY })}
      ${blank('ours', 'Your ledger for them', 'Their ledger account from Tally, same period.', f.ours, { accept: '.xlsx,.xls,.csv,.pdf' })}
    </div>
    <div class="fields">
      ${field('They are your', `<select id="inpRelation">
        <option value="supplier">Supplier (you owe them)</option>
        <option value="customer">Customer (they owe you)</option></select>`)}
      ${field('Their name', '<input type="text" id="inpPartyName" autocomplete="off" placeholder="Read from their statement">')}
    </div>
    <details class="more"><summary>${CHEVRON}Matching settings</summary>
      <div class="fields">
        ${field('Allow a difference of (₹)', `<input type="number" id="inpStmtTol" min="0" step="0.5" value="${esc(s.stmt_amount_tolerance ?? 1)}">`)}
        <label class="check"><input type="checkbox" id="inpCrossGst" checked> Also check their bills against GSTR-2B</label>
      </div></details>
    ${goRow('party', ready, samples.party)}`;
}

function goRow(mode, ready, sample, scanSample) {
  return `<div class="go">
    <button class="btn primary" type="button" data-run="${mode}" ${ready ? '' : 'disabled'}>Check these files</button>
    ${sample ? `<button class="btn" type="button" data-sample="${mode}">Try it with sample files</button>` : ''}
    ${scanSample ? '<button class="btn" type="button" data-sample="bank-scan">Try a scanned sample</button>' : ''}
    <p class="quiet">${ready ? 'Ready.' : 'Add both files to continue.'} Everything stays on this computer.</p>
  </div>`;
}

function fyOptions() {
  const now = new Date();
  const fy = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
  let out = '<option value="">Read from the file</option>';
  for (let y = fy + 1; y >= fy - 4; y--) out += `<option value="${y}">${y}-${String(y + 1).slice(2)}</option>`;
  return out;
}

export function home(st) {
  const adding = st.workspaces.find(w => w.id === st.addingTo);
  const step = !st.modeChosen ? 1 : 2;
  const steps = ['Choose a check', 'Add your two files', 'Read what does not match'];
  return `<div class="page">
    <nav class="margin" aria-label="Steps">
      <ol class="steps">${steps.map((s, i) => `<li ${i + 1 === step ? 'aria-current="step"' : ''}>
        <span class="step-n">${i + 1}</span>${esc(s)}</li>`).join('')}</ol>
    </nav>
    <div class="sheet">
      ${adding ? `<p class="adding">This check will be added to <b>${esc(runName(adding))}</b>.
        <button class="link" type="button" data-act="stop-adding">Keep it as a separate run instead</button></p>` : ''}
      <h1 class="verdict">What do you want to check?</h1>
      <p class="lede">Pick one, add the two files that should agree, and every entry
        that does not will be listed with the reason it does not.</p>
      <fieldset class="modes"><legend class="sr">Choose a check</legend>
        ${MODES.map(([key, title, sub]) => `<label class="mode" data-on="${st.mode === key ? 1 : 0}">
          <input type="radio" name="mode" value="${key}" ${st.mode === key ? 'checked' : ''}>
          <span class="mode-title">${esc(title)}</span><span class="mode-sub">${esc(sub)}</span>
        </label>`).join('')}
      </fieldset>
      <div class="mode-body">${modeBody(st)}</div>
      ${runsList(st)}
    </div>
  </div>`;
}

function runsList(st) {
  const runs = st.workspaces.filter(w => w.module !== 'exports');
  if (!runs.length) return '';
  const shown = st.showAllRuns ? runs : runs.slice(0, 8);
  return `<section class="block" id="runs">
    <h2>Your runs</h2>
    <p class="hint">Each run is one client and one period. A new check starts a run of its own
      unless you add it to one of these.</p>
    <ol class="runs">${shown.map(w => `<li class="run" data-on="${w.id === st.wsId ? 1 : 0}">
      <span class="run-when">${esc(when(w.last_run_at || w.updated_at))}</span>
      <span class="run-name">${esc(runName(w))}</span>
      <span class="run-parts">${esc(partsLine(w.parts))}</span>
      <span class="run-acts">
        <button class="btn small" type="button" data-open-run="${esc(w.id)}">Open</button>
        <button class="btn small" type="button" data-add-run="${esc(w.id)}">${w.id === st.addingTo ? 'Adding to this run' : 'Add a check to this run'}</button>
        <button class="link danger" type="button" data-del-run="${esc(w.id)}">Delete</button>
      </span></li>`).join('')}</ol>
    ${runs.length > 8 && !st.showAllRuns
      ? `<button class="link" type="button" data-act="all-runs">Show all ${num(runs.length)} runs</button>` : ''}
  </section>`;
}

/* ── one run ─────────────────────────────────────────────────────────── */

const PART_SHORT = { purchase: 'GST', bank: 'Bank', statement: 'Party' };

function partLabel(kind, data) {
  if (kind === 'statement') return data?.their_meta?.party_name || 'Party statement';
  if (kind === 'bank') return data?.bank_meta?.bank ? `Bank · ${data.bank_meta.bank}` : 'Bank';
  return 'GST purchases';
}

const ticked = (st, r) => DONE.has(st.decisions[r.fp]?.action);

/** Open entries in reading order: still to do before ticked off, money at risk
 *  first, then the largest amounts. */
export function openSorted(st, kind, records, sales = false) {
  const rowKind = sales ? 'sales' : kind;
  const toneKind = sales ? 'purchase' : kind;
  return records.filter(isOpen).sort((a, b) =>
    ticked(st, a) - ticked(st, b)
    || (toneOf(toneKind, b) === 'serious') - (toneOf(toneKind, a) === 'serious')
    || Math.abs(amountOf(rowKind, b) ?? 0) - Math.abs(amountOf(rowKind, a) ?? 0));
}

/** Entries still needing a look, per plain-named group, in reading order. */
export function groupCounts(st, kind, open, sales = false) {
  const groups = new Map();
  for (const r of open) {
    const g = groupOf(kind, r, sales);
    if (!groups.has(g)) groups.set(g, 0);
    if (!ticked(st, r)) groups.set(g, groups.get(g) + 1);
  }
  return [...groups.entries()];
}

function openCount(st, kind, data) {
  const left = rows => (rows || []).filter(r => isOpen(r) && !ticked(st, r)).length;
  return left(data?.all_records) + (kind === 'purchase' ? left(data?.sales?.all_records) : 0);
}

function margin(st, kind, data) {
  const present = PARTS.filter(([, k]) => st.data[k]);
  const open = openSorted(st, kind, data.all_records || []);
  const left = open.filter(r => !ticked(st, r)).length;
  const f = st.filter.open || {};
  return `<nav class="margin" aria-label="This run">
    <h2 class="margin-head">In this run</h2>
    <ul class="parts">${present.map(([p, k]) => `<li>
      <a href="#/run/${esc(st.wsId)}/${p}" ${k === kind ? 'aria-current="page"' : ''}>
        <span class="long">${esc(partLabel(k, st.data[k]))}</span><span class="short">${PART_SHORT[k]}</span>
        <span class="count">${num(openCount(st, k, st.data[k]))}</span></a></li>`).join('')}
      <li class="add-li"><button class="link add" type="button" data-act="add-to-run"><span class="long">Add another check to this run</span><span class="short">Add a check</span></button></li>
    </ul>
    <h2 class="margin-head">On this page</h2>
    <ul class="index">
      <li><button type="button" data-jump="summary">Summary</button></li>
      <li><button type="button" data-jump="open">Needs a look (${num(left)})</button>
        ${open.length ? `<ul class="sub">${groupCounts(st, kind, open).map(([g, n]) => `<li>
          <button type="button" data-pick="${esc(g)}" aria-pressed="${f.group === g}">${esc(g)}
            <span class="count">${num(n)}</span></button></li>`).join('')}</ul>` : ''}</li>
      ${data.sales ? '<li><button type="button" data-jump="sales">Sales</button></li>' : ''}
      <li><button type="button" data-jump="all">All entries</button></li>
      <li><button type="button" data-jump="files">Downloads and files</button></li>
    </ul>
    ${colourKey()}
  </nav>`;
}

export function colourKey(where = '', exports = false) {
  const short = where === 'inline';
  return `<dl class="key ${where}" aria-label="What the colours mean">
    <div><dt class="mark ok">${TICK}</dt><dd>${exports ? 'Done' : short ? 'Settled' : 'Matches, or you have ticked it off'}</dd></div>
    <div><dt class="mark look"><i></i></dt><dd>Needs a look</dd></div>
    <div><dt class="mark serious"><i></i></dt><dd>${short ? 'Serious' : 'Serious: money at risk'}</dd></div>
    ${exports ? `<div><dt class="mark wait"><i></i></dt><dd>${short ? 'Not yet' : 'Not due yet, or a document is still to come'}</dd></div>` : ''}
  </dl>`;
}

export function runPage(st) {
  const kind = KIND_OF[st.route.part];
  const data = st.data[kind];
  if (!data) return `<div class="page"><div class="sheet"><h1 class="verdict">Opening the run…</h1></div></div>`;
  return `<div class="page">
    ${margin(st, kind, data)}
    <div class="sheet">
      ${summary(st, kind, data)}
      ${openList(st, kind, data.all_records || [], { id: 'open', title: 'Needs a look' })}
      ${kind === 'purchase' && data.sales ? salesBlock(st, data.sales) : ''}
      ${allEntries(st, kind, data)}
      ${downloads(st, kind, data)}
    </div>
  </div>`;
}

/* ── the verdict ─────────────────────────────────────────────────────── */

const poss = name => /s$/i.test(name) ? `${name}’` : `${name}’s`;

// Every line of the balancing working, said the way a person would say it.
const LINE_WORDS = {
  unpresented: 'Cheques you issued that the bank has not paid out yet',
  uncredited: 'Deposits you made that the bank has not credited yet',
  bank_charges: 'Bank charges you have not entered yet',
  bank_interest: 'Interest the bank paid that you have not entered yet',
  bank_debits: 'Other money the bank took out that you have not entered',
  bank_credits: 'Other money the bank received that you have not entered',
  their_invoices: 'Their bills that are not in your ledger',
  their_credits: 'Their credits that are not in your ledger',
  our_invoices: 'Your entries that are not in their statement',
  our_payments: 'Your payments they have not recorded yet',
  matched_diff: 'Small differences on entries that otherwise match',
  interest_diff: 'Interest they worked out differently',
  tds_diff: 'TDS they show differently',
  opening_diff: 'Difference in the opening balances',
  residual: 'Not explained – a figure was probably read wrongly',
};

function owedWords(v) {
  return v > 0.5 ? 'you owe them' : v < -0.5 ? 'they owe you' : 'nothing owed either way';
}

function countsLine(ok, look, serious) {
  return `<span class="counts"><b class="ok">${num(ok)} settled</b>, <b class="look">${num(look)}
    ${look === 1 ? 'needs' : 'need'} a look</b>${serious ? `, <b class="serious">${num(serious)} serious</b>` : ''}.</span>`;
}

function summary(st, kind, data) {
  const records = data.all_records || [];
  // Ticked-off entries are settled: the verdict counts only what is left to do.
  const open = records.filter(r => isOpen(r) && !ticked(st, r));
  const serious = open.filter(r => toneOf(kind, r) === 'serious').length;
  const settled = records.filter(r => r.status !== 'Superseded').length - open.length;
  const counts = countsLine(settled, open.length - serious, serious);
  let h1, lede, body = '';

  if (kind === 'purchase') {
    const c = data.counts || {}, ct = data.control_totals || {};
    const itc = data.insights?.itc_position || {};
    h1 = open.length ? `${plural(open.length, 'entry needs', 'entries need')} a look`
      : 'Everything is settled';
    lede = `${num(c.matched)} of the ${num(ct.invoices_in_2b)} bills your suppliers filed
      (GSTR-2B) match your ${num(ct.vouchers_in_tally)} purchase entries exactly. ${counts}`;
    const tallyOnly = records.filter(r => r.status === 'In Tally only');
    const sec = data.insights?.sec164 || {};
    const fig = (label, note, value, cls = '') => `<div><dt>${label}${note
      ? `<small>${note}</small>` : ''}</dt><dd class="${cls}">${value}</dd></div>`;
    body = `<dl class="figures">
      ${fig('GST credit you can safely claim now', 'on your books as they stand',
            inr(itc.claimable_now), 'ok')}
      ${fig('More credit, once the missing bills are in your books',
            `${plural(c.gst_only || 0, 'bill')} filed by suppliers, not in your books`,
            inr(itc.recoverable_by_booking))}
      ${fig('Purchases your suppliers have not filed',
            `${plural(tallyOnly.length, 'entry', 'entries')}; no credit on these until they appear`,
            inr(tallyOnly.reduce((a, r) => a + (r.amt_tally || 0), 0)))}
      ${sec.applicable && sec.count ? fig('Last date to claim this year’s credit',
            sec.lapsed ? 'the date has passed' : `${plural(sec.days_left, 'day')} left`,
            day(sec.deadline), sec.lapsed || sec.urgent ? 'serious' : '') : ''}
    </dl>`;
  } else {
    const block = kind === 'bank' ? data.brs : data.bridge;
    const b = data.balances || {};
    const party = data.their_meta?.party_name || 'The party';
    const gap = Math.abs(b.difference || 0);
    if (!block?.ties) {
      h1 = `${inr(Math.abs(block?.residual || 0))} of the difference cannot be explained`;
    } else if (gap < 0.5) {
      h1 = kind === 'bank' ? 'Your books and the bank agree' : `${party} agrees with your ledger`;
    } else {
      h1 = kind === 'bank' ? `The bank and your books differ by ${inr(gap)}`
        : `${party} and your ledger differ by ${inr(gap)}`;
    }
    if (kind === 'bank') {
      const bm = data.bank_meta || {};
      lede = `Your bank book shows ${inr(b.book_closing, { sign: true })}; the bank statement shows
        ${inr(b.bank_closing, { sign: true })}${bm.last_date ? `, on ${day(bm.last_date)}` : ''}.`;
    } else {
      lede = `Their statement shows ${inr(Math.abs(b.theirs || 0))} (${owedWords(b.theirs || 0)});
        your ledger shows ${inr(Math.abs(b.ours || 0))} (${owedWords(b.ours || 0)}).`;
    }
    lede += block?.ties ? ` Every rupee of the difference is named below. ${counts}`
      : ' That usually means a figure was read wrongly from a file. Check the source files before relying on this.';
    body = working(kind, block, party);
  }

  const notes = [...(data.notes || []), ...(kind === 'bank' ? data.issues || [] : [])];
  return `<section class="block summary" id="summary">
    <h1 class="verdict">${esc(h1)}</h1>
    <p class="lede">${lede}</p>
    ${body}
    ${notes.length ? `<ul class="notes">${notes.slice(0, 6).map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
  </section>`;
}

/** The balancing working, written the way a person would say it. */
function working(kind, block, party) {
  if (!block) return '';
  const OPEN = { bank: 'Your bank book shows', statement: 'Your ledger shows' };
  const CLOSE = { bank: 'The bank statement shows', statement: `${poss(party)} statement shows` };
  const rows = (block.lines || []).map(l => {
    if (l.kind === 'opening' || l.kind === 'closing') {
      return `<tr class="${l.kind}"><td class="op">${l.kind === 'closing' ? '=' : ''}</td>
        <th scope="row">${esc(l.kind === 'opening' ? OPEN[kind] : CLOSE[kind])}</th>
        <td class="money">${inr(l.amount, { sign: true })}</td></tr>`;
    }
    const label = LINE_WORDS[l.key] || l.label.replace(/^(Add|Less|Adjust):\s*/, '');
    const pick = (block.groups || []).some(g => g.key === l.key);
    return `<tr class="${l.key === 'residual' ? 'serious' : ''}">
      <td class="op">${l.amount < 0 ? '−' : '+'}</td>
      <th scope="row">${pick ? `<button class="link" type="button" data-pick-group="${esc(l.key)}">${esc(label)}</button>`
        : esc(label)}${l.count ? ` <small>${plural(l.count, 'entry', 'entries')}</small>` : ''}</th>
      <td class="money">${inr(Math.abs(l.amount))}</td></tr>`;
  }).join('');
  return `<table class="ledger balance"><tbody>${rows}</tbody></table>
    <p class="adds" data-ties="${block.ties ? 1 : 0}">${block.ties
      ? `${TICK}<span>It adds up. Every rupee between the two balances is on a line above.</span>`
      : `<span>It does not add up: ${inr(Math.abs(block.residual))} is not explained.</span>`}</p>`;
}

function sourceLine(kind, data) {
  const s = data.source_info || {};
  const files = [s.gst_file, s.tally_file, s.sales_file, s.gstr1_file, s.statement_file,
                 s.book_file, s.their_file, s.our_file].filter(Boolean);
  const read = s.read_as === 'ocr' || s.read_as === 'mixed'
    ? ` · the statement was read by recognition${s.ocr_confidence ? ` at ${Math.round(s.ocr_confidence * 100)}% confidence` : ''}` : '';
  const at = data.generated_at || data.entity_info?.generated_at;
  return `<p class="source">${at ? `Checked ${esc(day(at))}` : 'Checked'}${files.length
    ? ` from ${files.map(f => `<b>${esc(f)}</b>`).join(' and ')}` : ''}${read}${
    s.is_sample ? ' (the sample files)' : ''}.</p>`;
}

/* ── the open entries ────────────────────────────────────────────────── */

export function openList(st, kind, records, { id, title, sales = false }) {
  const rowKind = sales ? 'sales' : kind;
  const open = openSorted(st, kind, records, sales);
  if (!open.length) {
    return `<section class="block" id="${id}"><h2>${esc(title)}</h2>
      <p class="clear-line">${TICK}<span>Nothing. Every entry is settled.</span></p></section>`;
  }
  const f = st.filter[id] || (st.filter[id] = { group: '', q: '', hideDone: false, fps: null });
  const groups = groupCounts(st, kind, open, sales);
  const left = open.filter(r => !ticked(st, r)).length;
  const done = open.length - left;
  const q = f.q.trim().toLowerCase();
  const shown = open.filter(r => (!f.group || groupOf(kind, r, sales) === f.group)
    && (!f.fps || f.fps.includes(r.fp))
    && (!q || haystack(rowKind, r).includes(q))
    && (!f.hideDone || !ticked(st, r)));
  const limit = st.limit[id] || 150;

  return `<section class="block" id="${id}">
    <h2>${esc(title)} <span class="count">${num(left)}</span>${done
      ? ` <span class="done-count">${num(done)} ticked off</span>` : ''}</h2>
    <p class="hint">Each line says why it is still open. Open one to see what was checked and what to do.</p>
    ${colourKey('inline', kind === 'exports')}
    <div class="tools-row">
      <label class="show">Show
        <select data-group-select="${id}">
          <option value="" ${!f.group && !f.fps ? 'selected' : ''}>Everything (${num(left)})</option>
          ${groups.map(([g, n]) => `<option value="${esc(g)}" ${f.group === g ? 'selected' : ''}>${esc(g)} (${num(n)})</option>`).join('')}
          ${f.fps ? `<option value="__fps" selected>One line of the working (${num(f.fps.length)})</option>` : ''}
        </select></label>
      <input type="search" data-search="${id}" value="${esc(f.q)}" placeholder="Search names, numbers or amounts" aria-label="Search these entries">
      ${done ? `<label class="check"><input type="checkbox" data-hide-done="${id}" ${f.hideDone ? 'checked' : ''}> Hide what I have ticked off</label>` : ''}
      <button class="link" type="button" data-expand="${id}">${st.expandAll[id] ? 'Close every line' : 'Open every line'}</button>
    </div>
    ${shown.length ? `<ol class="entries">${shown.slice(0, limit).map(r => entry(st, rowKind, r)).join('')}</ol>`
      : '<p class="quiet">Nothing matches that. Clear the search or show everything.</p>'}
    ${shown.length > limit ? `<button class="btn" type="button" data-more="${id}">Show ${num(Math.min(150, shown.length - limit))} more of ${num(shown.length - limit)}</button>` : ''}
  </section>`;
}

export function entry(st, kind, r) {
  const dec = st.decisions[r.fp];
  const isDone = DONE.has(dec?.action);
  const tone = toneOf(kind === 'sales' ? 'purchase' : kind, r);
  const expanded = st.openFps.has(r.fp);
  const amt = amountOf(kind, r);
  const diff = r.difference;
  return `<li class="entry" id="e-${esc(r.fp)}" data-fp="${esc(r.fp)}" data-kind="${kind}"
      data-tone="${tone}" data-done="${isDone ? 1 : 0}" ${st.justDone === r.fp ? 'data-just="1"' : ''}>
    <button class="entry-line" type="button" aria-expanded="${expanded}" aria-controls="x-${esc(r.fp)}">
      <span class="e-mark">${isDone ? TICK : '<i></i>'}</span>
      <span class="e-date">${esc(dayShort(dateOf(kind, r)))}</span>
      <span class="e-text"><span class="e-why">${esc(r.why?.title
        || groupOf(kind === 'sales' ? 'purchase' : kind, r, kind === 'sales'))}</span>
        <span class="e-who">${esc(whoOf(kind, r))}</span></span>
      <span class="e-amt">${kind === 'exports' ? esc(r.amount_text || '') : inr(Math.abs(amt ?? 0))}${
        diff && Math.abs(diff) >= 0.01 && r.status !== 'Split booking'
        ? `<small>off by ${inr(Math.abs(diff))}</small>` : ''}</span>
      ${CHEVRON}
    </button>
    ${expanded ? detail(st, kind, r, dec) : ''}
  </li>`;
}

function checkItem(text) {
  const m = /^([A-Z][A-Za-z ]{1,26}?)(:|\?)\s+(.*)$/s.exec(text);
  if (!m) return `<li>${esc(text)}</li>`;
  const label = m[2] === '?' ? `${m[1]}?` : m[1];
  return `<li><b>${esc(label)}</b> ${esc(m[3].charAt(0).toUpperCase() + m[3].slice(1))}</li>`;
}

/**
 * Where to look a shipping bill up on ICEGATE: each value its enquiry asks for,
 * a click copies it; the link opens the page in the browser, where the user
 * logs in and types the captcha themselves - the app never does either.
 */
export function lookUp(x) {
  if (!x?.sb_no) return '';
  const v = (label, val) => val ? `${label} <button class="copy-v" type="button" data-copy-text="${esc(val)}"
    title="Copy ${esc(val)}">${esc(val)}</button>` : '';
  return `<div class="ice"><span class="ice-q">Look it up on ICEGATE</span>
    <span class="ice-v">${[v('port', x.port), v('SB', x.sb_no), v('date', x.sb_date)].filter(Boolean).join(' ')}</span>
    ${x.sb_enquiry ? `<a class="link" href="${esc(x.sb_enquiry)}" target="_blank" rel="noopener">Open the shipping bill
      enquiry<span class="sr">, on ICEGATE, in your browser</span></a>` : ''}
    <span class="ice-from">No login - you type its captcha. The IGST refund status needs your ICEGATE
      ${x.login ? `<a class="link" href="${esc(x.login)}" target="_blank" rel="noopener">login</a>` : 'login'}${
      x.igst_path ? `, then ${esc(x.igst_path)}` : ''}.</span></div>`;
}

function detail(st, kind, r, dec) {
  const w = r.why || {};
  const rows = [
    ['What this is', w.what && esc(w.what)],
    ['What was checked', w.checks?.length && `<ul class="checks">${w.checks.map(checkItem).join('')}</ul>`],
    ['Closest match found', w.nearest && esc(w.nearest)],
    ['What it means for you', w.impact && esc(w.impact)],
    ['What to do', w.todo && `<span class="todo">${esc(w.todo)}</span>`],
    ['Also note', w.notes?.length && `<ul class="plain">${w.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>`],
  ].filter(([, v]) => v);
  const probable = r.status === 'Probable match' && r.tally_fp;
  const btn = (action, label, primary = false) => `<button class="btn small ${primary ? 'primary' : ''}"
    type="button" data-decide="${action}" data-fp="${esc(r.fp)}">${esc(label)}</button>`;
  const settle = (rem, label, primary = false) => `<button class="btn small ${primary ? 'primary' : ''}"
    type="button" data-settle="${esc(rem)}">${esc(label)}</button>`;
  let acts;
  if (dec) {
    acts = `<p class="decided">${esc(DECIDED_WORDS[dec.action] || 'You recorded a decision')}${
      dec.updated_at ? ` on ${esc(day(dec.updated_at))}` : ''}.</p>${btn('clear', 'Undo')}`;
  } else if (r.status === 'pay_confirm' && r.counterpart?.kind === 'set') {
    // Several invoices at once: the settle window opens with the split filled in.
    acts = settle(r.counterpart.remittance, 'Yes – check the amounts and save', true)
      + btn('ignore', 'No, it is for other invoices');
  } else if (r.status === 'pay_confirm') {
    // A payment is set against an invoice only when the user says so.
    acts = btn('match', `Yes, it is for invoice ${r.inv_no}`, true) + btn('ignore', 'No, it is for another invoice')
      + settle(r.counterpart.remittance, 'Split it over several invoices');
  } else if (r.status === 'ai_differs') {
    // Neither reading goes in by itself: the user looks at the original and chooses.
    acts = `<button class="btn small primary" type="button" data-ai-use="${esc(r.doc_id)}">Use the AI’s reading</button>
      <button class="btn small" type="button" data-correct="${esc(r.doc_id)}">Correct it yourself</button>
      <a class="btn small" href="/api/files/${encodeURIComponent(st.ex?.wsId || '')}/${encodeURIComponent(r.doc_id)}"
        target="_blank" rel="noopener">Open the original<span class="sr">, in a new tab</span></a>`
      + btn('ignore', 'The app read it right – take this off the list');
  } else if (r.remittance) {
    acts = settle(r.remittance, 'Choose the invoices it is for', true)
      + btn('follow_up', 'I am chasing this') + btn('ignore', 'Not a problem – take it off the list');
  } else {
    acts = (probable ? btn('match', 'Yes, it is the same bill', true) : '')
      + btn('resolved', r.status === 'no_bl' ? 'We have it – mark as received' : 'Mark as done', !probable)
      + btn('follow_up', 'I am chasing this')
      + btn('ignore', 'Not a problem – take it off the list');
  }
  return `<div class="e-detail" id="x-${esc(r.fp)}">
    <dl class="why">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>
    ${lookUp(r.icegate)}
    <div class="e-actions">${acts}
      <button class="link" type="button" data-copy-link="${esc(r.fp)}">Copy a link to this entry</button>
    </div>
  </div>`;
}

function salesBlock(st, sales) {
  const c = sales.counts || {};
  return `<div class="sub-block">
    ${openList(st, 'purchase', sales.all_records || [], { id: 'sales', title: 'Sales: your GSTR-1 against your books', sales: true })}
    <p class="source">${num(c.matched || 0)} of ${num((sales.control_totals || {}).invoices_in_gstr1 || 0)}
      sales you reported in GSTR-1 match your books.</p>
  </div>`;
}

/* ── everything, matched too ─────────────────────────────────────────── */

const RESULT_WORD = {
  Matched: 'Matches', Agreed: 'Agrees', Batched: 'Matches (several entries)',
  Superseded: 'Replaced by an amendment',
};

function allEntries(st, kind, data) {
  const records = data.all_records || [];
  const v = st.all;
  let body = '';
  if (v.open) {
    const q = v.q.trim().toLowerCase();
    const rows = q ? records.filter(r => haystack(kind, r).includes(q)) : records;
    const pages = Math.max(1, Math.ceil(rows.length / 100));
    const page = Math.min(v.page, pages);
    const cols = {
      purchase: ['Result', 'Date', 'Supplier', 'Bill no.', 'On the portal', 'In your books'],
      bank: ['Result', 'Date', 'Description', 'Bank', 'Your book'],
      statement: ['Result', 'Date', 'Particulars', 'Reference', 'Their statement', 'Your ledger'],
    }[kind];
    const cell = r => {
      const ticked = isOpen(r) && DONE.has(st.decisions[r.fp]?.action);
      const result = !isOpen(r) ? (RESULT_WORD[r.status] || r.status)
        : ticked ? `${groupOf(kind, r)} – ticked off` : groupOf(kind, r);
      const mark = isOpen(r) && !ticked ? `<span class="mark ${toneOf(kind, r)}"><i></i></span>`
        : `<span class="mark ok">${TICK}</span>`;
      if (kind === 'purchase') {
        return [`${mark}${esc(result)}`, esc(dayShort(r.inv_date_2b || r.vch_date_tally)),
          esc(r.party_2b || r.party_tally || ''), esc(r.inv_no_2b || r.vch_no_tally || ''),
          inr(r.inv_val_2b), inr(r.amt_tally)];
      }
      if (kind === 'bank') {
        return [`${mark}${esc(result)}`, esc(dayShort(r.date_bank || r.date_book)),
          esc(junk(r.narration || r.book_party)), inr(r.bank_amount), inr(r.book_amount)];
      }
      return [`${mark}${esc(result)}`, esc(dayShort(r.date_theirs || r.date_ours)),
        esc(junk(r.particulars_theirs || r.particulars_ours)), esc(r.ref_theirs || r.ref_ours || ''),
        inr(r.owed_theirs), inr(r.owed_ours)];
    };
    const money = kind === 'bank' ? [3, 4] : [4, 5];
    body = `<div class="tools-row">
        <input type="search" data-all-search value="${esc(v.q)}" placeholder="Search every entry" aria-label="Search every entry">
        <span class="quiet">${plural(rows.length, 'entry', 'entries')}</span></div>
      <div class="table-wrap"><table class="all">
        <thead><tr>${cols.map((c, i) => `<th scope="col" class="${money.includes(i) ? 'money' : ''}">${c}</th>`).join('')}</tr></thead>
        <tbody>${rows.slice((page - 1) * 100, page * 100).map(r => `<tr ${isOpen(r) ? `data-goto-fp="${esc(r.fp)}"` : ''}>${
          cell(r).map((c, i) => `<td class="${money.includes(i) ? 'money' : ''}">${c}</td>`).join('')}</tr>`).join('')}</tbody>
      </table></div>
      ${pages > 1 ? `<p class="pager">
        <button class="btn small" type="button" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>Previous</button>
        <span>Page ${num(page)} of ${num(pages)}</span>
        <button class="btn small" type="button" data-page="${page + 1}" ${page >= pages ? 'disabled' : ''}>Next</button></p>` : ''}`;
  }
  return `<section class="block" id="all">
    <h2>All entries</h2>
    <button class="btn" type="button" data-act="toggle-all" aria-expanded="${v.open}">${v.open
      ? 'Hide the full list' : `Show all ${num(records.length)} entries, matched ones too`}</button>
    ${body}
  </section>`;
}

/* ── downloads ───────────────────────────────────────────────────────── */

/* ── kept files ──────────────────────────────────────────────────────── */

/** What can be done with one kept file: open it, save a copy to hand over,
 *  share it, or find it in its folder to drag into an email. */
export function fileActions(ws, id, name) {
  const at = `data-ws="${esc(ws)}" data-id="${esc(id)}"`, sr = `<span class="sr"> ${esc(name)}</span>`;
  // A browser shows PDFs and pictures itself; an Excel file would only download.
  const opens = /\.(pdf|png|jpe?g|gif|webp|bmp|txt|json)$/i.test(name || '');
  return `${opens ? `<a href="/api/files/${encodeURIComponent(ws)}/${encodeURIComponent(id)}" target="_blank" rel="noopener">Open${
    sr}<span class="sr">, in a new tab</span></a>` : ''}
    <button class="link" type="button" data-kept="save" ${at}>Save a copy${sr}</button>
    <button class="link" type="button" data-kept="share" ${at}>Share${sr}</button>
    <button class="link" type="button" data-kept="reveal" ${at}>Show in folder${sr}</button>`;
}

/** Where a run's files are kept, and the ways to take them all at once. */
export function keptFolder(ws, folder) {
  return folder ? `<p class="kept">Kept on this computer in <code>${esc(folder)}</code>
    <span class="kept-acts"><button class="link" type="button" data-kept="reveal" data-ws="${esc(ws)}">Show the folder</button>
    <button class="link" type="button" data-kept="zip" data-ws="${esc(ws)}">Save them all as one zip</button></span></p>` : '';
}

const PART_FOLDER = { purchase: 'GST purchases', bank: 'Bank', statement: 'Party statements' };

/** The files this check was made from, kept so they can be handed over. */
function checkedFiles(st, kind) {
  const files = st.ws?.files || {};
  const mine = (files.items || []).filter(f => f.folder === PART_FOLDER[kind]);
  if (!mine.length) {
    return `<h3 class="sub-head">The files you checked</h3><p class="quiet">Not kept: this check was made before the app kept
      its files. Files you check from now on are kept here.</p>`;
  }
  return `<h3 class="sub-head">The files you checked</h3>
    <ul class="files kept-files">${mine.map(f => `<li><b>${esc(f.role || 'File')}</b> <span>${esc(f.kept_as)}${
      f.added_at ? ` · ${esc(dayShort(f.added_at))}` : ''}</span>
      <span class="acts">${fileActions(st.wsId, f.id, f.kept_as)}</span></li>`).join('')}</ul>
    ${keptFolder(st.wsId, files.folder)}`;
}

function downloads(st, kind, data) {
  const runId = st.runIds[kind];
  if (!runId) {
    return `<section class="block" id="files"><h2>Downloads</h2>
      <p class="quiet">This check was not saved (saving is switched off in Settings), so there is
        nothing to download.</p></section>`;
  }
  const item = (dl, name, note) => `<li><button class="link" type="button" data-dl="${dl}">${esc(name)}</button>
    <span>${esc(note)}</span></li>`;
  const list = [
    item('excel', 'Excel workbook', 'Every sheet, for your file. Each open entry carries its reason.'),
    item('pdf', 'PDF working paper', 'Ready to print and sign.'),
    item('csv?view=all', 'Every entry as CSV', 'For your own spreadsheet.'),
  ];
  if (kind === 'purchase') {
    list.push(item('csv?view=gst_only', 'Bills not in your books (CSV)', 'Filed by suppliers, missing from Tally.'),
      item('csv?view=tally_only', 'Bills your suppliers have not filed (CSV)', 'In Tally, missing from GSTR-2B.'),
      item('followups', 'Letters to suppliers (zip)', 'One ready-to-send letter per supplier with open bills.'));
  } else if (kind === 'bank') {
    list.push(item('csv?view=brs', 'The bank reconciliation statement (CSV)', 'The working above, line by line.'));
  } else {
    list.push(item('csv?view=bridge', 'The balance working (CSV)', 'The working above, line by line.'),
      `<li><button class="link" type="button" data-act="letter">Balance confirmation letter</button>
        <span>A draft letter to ${esc(data.their_meta?.party_name || 'the party')} setting out every difference.</span></li>`);
  }
  return `<section class="block" id="files">
    <h2>Downloads and files</h2>
    ${sourceLine(kind, data)}
    <ul class="files">${list.join('')}</ul>
    ${checkedFiles(st, kind)}
    ${st.letter ? letter(st, data) : ''}
    ${kind === 'purchase' ? rematch(st, data) : ''}
  </section>`;
}

function letter(st, data) {
  const c = st.letter.contact || {};
  return `<div class="letter-box" id="letter">
    <h3>Balance confirmation letter</h3>
    <p class="hint">A draft. Read it before you send it.</p>
    <div class="fields">
      ${field('Write to', `<input type="text" data-letter="contact" value="${esc(c.contact || '')}" placeholder="Contact person">`)}
      ${field('Their email', `<input type="email" data-letter="email" value="${esc(c.email || '')}" placeholder="accounts@example.com">`)}
    </div>
    <pre class="letter">${esc(st.letter.body || '')}</pre>
    <div class="go">
      <button class="btn primary" type="button" data-act="letter-copy">Copy the letter</button>
      ${st.letter.mailto && c.email ? `<a class="btn" href="${esc(st.letter.mailto)}">Open it in your email</a>` : ''}
      ${data.their_meta?.gstin ? '<button class="btn" type="button" data-act="letter-save">Save these details and redo the letter</button>' : ''}
      <button class="link" type="button" data-act="letter-close">Close the letter</button>
    </div>
  </div>`;
}

function rematch(st, data) {
  const cfg = data.config || {};
  return `<details class="more"><summary>${CHEVRON}Match again with different settings</summary>
    <p class="hint">Uses the same files. The result is saved as a new check in this run.</p>
    <div class="fields">
      ${field('Allow a difference of (₹)', `<input type="number" data-rematch="tolerance" min="0" step="0.5" value="${esc(cfg.tolerance ?? 2)}">`)}
      ${field('Look this many days either side', `<input type="number" data-rematch="date_window_days" min="0" value="${esc(cfg.date_window_days ?? 45)}">`)}
    </div>
    <button class="btn" type="button" data-act="rematch">Match again</button>
  </details>`;
}

/* ── settings ────────────────────────────────────────────────────────── */

/** Where the data lives, and the way to move it. */
function dataFolder(d) {
  if (!d?.path) return '';
  const size = d.bytes >= 1048576 ? `${(d.bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(d.bytes / 1024))} KB`;
  return `<div class="field data-folder"><span class="label">Where your data is kept</span>
    <p class="kept"><code>${esc(d.path)}</code> <span class="quiet">· ${esc(size)}</span>
      <span class="kept-acts"><button class="link" type="button" data-act="data-reveal">Show the folder</button>
      ${d.fixed ? '' : '<button class="link" type="button" data-act="data-move">Move it to another folder…</button>'}</span></p>
    <p class="hint">Every run, export register and file you gave the app. Moving copies it all to the folder you
      choose and checks the copy before the old one is removed. Updates keep it there, and uninstalling asks
      before it touches it.</p></div>`;
}

/** The version, and a newer one when it is waiting (app/backend/updates.py). */
const bytes = n => n >= 1073741824 ? `${(n / 1073741824).toFixed(1)} GB`
  : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;

/** The AI checker: an add-on fetched only when asked for, then run in the background (app/backend/ai.py). */
function aiBlock(a, s) {
  if (!a) return '';
  const c = a.counts || {};
  const on = s.ai_checks !== false;
  const getting = ['downloading', 'unpacking'].includes(a.status);
  const btn = (act, label, cls = 'btn') => `<button class="${cls}" type="button" data-act="${act}">${label}</button>`;
  // Running on the copy another app downloaded (Invoice Bridge, say): nothing of its own to fetch.
  const theirs = a.status === 'ready' && a.shared;
  const said = getting ? `${a.status === 'unpacking' ? 'Unpacking' : 'Downloading'} ${esc(a.step)}${
      a.total ? ` – ${esc(bytes(a.done))} of ${esc(bytes(a.total))}` : ''}…`
    : theirs ? `Uses the AI that ${esc(theirs.app)} downloaded, in <code>${esc(theirs.folder)}</code> – nothing more to download`
    : a.status === 'ready' ? `On this computer${a.size ? `, ${esc(bytes(a.size))}` : ''}`
    : a.status === 'failed' ? `Not set up. ${esc(a.message)}` : 'Not on this computer';
  const act = getting ? ''
    : theirs ? (a.size ? btn('ai-remove', `Remove its own unfinished download (${esc(bytes(a.size))})`, 'link danger') : '')
    : a.status === 'ready' ? btn('ai-remove', 'Remove it', 'link danger')
    : btn('ai-get', a.status === 'failed' ? 'Try again' : 'Get the AI checker', 'btn primary');
  const done = (c.agrees || 0) + (c.differs || 0);
  const tally = [done && `${plural(done, 'document')} checked: ${num(c.agrees || 0)} read the same`,
    c.differs && `${num(c.differs)} read differently – each is on its register’s list, under Read differently by the AI`,
    c.failed && `${plural(c.failed, 'document')} it could not read`,
    (c.waiting || c.checking) && `${plural((c.waiting || 0) + (c.checking || 0), 'document')} still to check`]
    .filter(Boolean).join('; ');
  return `<section class="block" id="ai"><h2>AI checker</h2>
      <p class="hint">A second reader for your export documents. A small AI model, run on this computer, reads each
        shipping bill, bill of lading, bank advice and invoice again on its own – those waiting to be checked and those
        already posted – and compares what it reads with what the app read. Where the two differ, the register lists the
        document for you to settle. Nothing it reads goes into the register by itself.</p>
      <div class="field version"><span class="label">State</span>
        <p class="kept">${said}${act ? `<span class="kept-acts">${act}</span>` : ''}</p>
        ${getting && a.total ? `<progress max="${a.total}" value="${a.done}"></progress>` : ''}</div>
      ${a.status === 'ready' && !theirs && a.also ? `<p class="hint">${esc(a.also.app)} has downloaded the same AI too, in
        ${esc(a.also.folder)}. Removing this copy frees ${esc(a.size ? bytes(a.size) : 'about 4 GB')}, and the checker
        goes on with ${esc(a.also.app)}’s.</p>` : ''}
      ${a.status === 'ready' ? `<p>${tally ? `${esc(tally)}.` : 'Nothing checked yet.'}${
        !on ? ' <span class="quiet">Background checks are off.</span>' : ''}</p>
      <label class="check"><input type="checkbox" data-setting="ai_checks" ${on ? 'checked' : ''}>
        Check documents in the background</label>` : ''}
      <p class="hint">The same AI reads bills in the Invoice Bridge: one download serves both.
        It works only on this computer: nothing you add is ever sent anywhere. It starts after the app has
        been left alone for a minute, reads one document at a time, and never takes more than half the processor.
        ${a.status === 'ready' ? '' : `Getting it downloads about 5 GB once – the Ollama runtime from its own GitHub
        releases, then the ${esc(a.model)} model – and keeps about 4 GB. It is not part of the app’s updates, which stay
        small.`}</p>
    </section>`;
}

function updateBlock(u, s) {
  if (!u) return '';
  const on = s.check_updates !== false;
  const checked = u.checked_at ? ` · checked ${esc(when(u.checked_at))}` : '';
  const said = {
    checking: 'Checking for a newer version…',
    downloading: `Downloading version ${esc(u.latest)}…`,
    current: `The newest version${checked}`,
    ready: `Version ${esc(u.latest)} is ready to install${u.size ? ` (${u.light ? 'the app’s own code, ' : ''}${
      esc(bytes(u.size))})` : ''}`,
    offline: `Could not check – no internet connection${checked}`,
    failed: `Could not check. ${esc(u.message)}${checked}`,
  }[u.status] || (!on ? 'Update checks are off' : u.automatic ? 'Not checked yet'
    : 'Updates come to the installed app');
  const act = u.status === 'ready' && u.can_install
    ? '<button class="btn primary" type="button" data-act="update-install">Restart and update</button>'
    : ['checking', 'downloading'].includes(u.status) ? ''
    : '<button class="link" type="button" data-act="update-check">Check now</button>';
  return `<div class="field version"><span class="label">Version</span>
    <p class="kept">${esc(u.current)} <span class="quiet">· ${said}</span>${act ? `<span class="kept-acts">${act}</span>` : ''}</p>
    ${u.status === 'ready' && u.notes ? `<p class="hint">New in ${esc(u.latest)}: ${esc(u.notes)}</p>` : ''}</div>
    <label class="check"><input type="checkbox" data-setting="check_updates" ${on ? 'checked' : ''}>
      Check for updates when this computer is online</label>
    <p class="hint">The app asks its release page on GitHub for the number of the newest version, and downloads that
      version when there is one. That request is the only thing it ever sends over the internet – nothing about your
      data goes with it. An update is installed only when you press Restart and update.</p>`;
}

export function settingsPage(st) {
  const s = st.settings || {};
  const text = (key, label, hint = '') => field(label,
    `<input type="text" data-setting="${key}" value="${esc(s[key] ?? '')}" autocomplete="off">`, hint);
  // The bank and statement engines keep their own defaults until one is saved.
  const DEFAULT = { bank_amount_tolerance: 1, bank_date_window_days: 7,
                    bank_wide_date_window_days: 45, stmt_amount_tolerance: 1, exim_due_soon_days: 90 };
  const r = st.eximRules || {};
  const span = x => x.shipped_from ? (x.shipped_to ? `${day(x.shipped_from)} to ${day(x.shipped_to)}`
    : `from ${day(x.shipped_from)}`) : `up to ${day(x.shipped_to)}`;
  const periods = (r.realisation || []).map(x => `shipped ${span(x)}: ${x.months} months${
    x.months_inr ? ` (${x.months_inr} if invoiced in rupees)` : ''}`).join('; ');
  const back = st.lastIn?.[st.module] || '#/';
  const n = (key, label, hint = '', step = '1') => field(label,
    `<input type="number" data-setting="${key}" value="${esc(s[key] ?? DEFAULT[key] ?? '')}" min="0" step="${step}">`, hint);
  return `<div class="page"><nav class="margin" aria-label="Settings">
      <a class="back" href="${esc(back)}">Back</a>
    </nav>
    <div class="sheet">
      <h1 class="verdict">Settings</h1>
      <p class="lede">Changes are saved as you make them.</p>
      <section class="block"><h2>Your business</h2>
        <div class="fields">
          ${text('entity_name', 'Business name', 'Printed on reports and letters.')}
          ${text('gstin', 'Your GSTIN')}
          ${text('preparer', 'Your name', 'Shown as the preparer on reports.')}
          ${text('entity_address', 'Address', 'Used in letters.')}
        </div></section>
      <section class="block"><h2>How closely entries must agree</h2>
        <div class="fields">
          ${n('tolerance', 'GST: allow a difference of (₹)', 'For rounding.', '0.5')}
          ${n('date_window_days', 'GST: look this many days either side')}
          ${n('bank_amount_tolerance', 'Bank: allow a difference of (₹)', '', '0.5')}
          ${n('bank_date_window_days', 'Bank: same transaction if within (days)')}
          ${n('bank_wide_date_window_days', 'Bank: timing difference up to (days)')}
          ${n('stmt_amount_tolerance', 'Party statements: allow a difference of (₹)', '', '0.5')}
        </div></section>
      <section class="block"><h2>Exports</h2>
        ${periods ? `<p class="hint">Payment deadlines follow the rules as of ${esc(day(r.as_of))} – ${esc(periods)}.</p>` : ''}
        <div class="fields">
          ${field('Count the payment deadline from', `<select data-setting="exim_counted_from">${
            Object.entries(r.counted_from_choices || {}).map(([k, words]) => `<option value="${k}" ${
              (s.exim_counted_from || r.counted_from) === k ? 'selected' : ''}>${esc(words.charAt(0).toUpperCase()
              + words.slice(1))}</option>`).join('')}</select>`, 'Your bank decides which; most count from the shipping bill.')}
          ${n('exim_due_soon_days', 'Warn this many days before a payment is due')}
        </div>
        <label class="check"><input type="checkbox" data-setting="exim_extend_open" ${s.exim_extend_open ? 'checked' : ''}>
          Also give 15 months to bills shipped before 14 Nov 2025 that were still unpaid on that day</label>
        <p class="hint">Left off, those bills keep 9 months – the earlier date, and the safer one to work to.</p>
      </section>
      ${aiBlock(st.ai, s)}
      <section class="block"><h2>Invoice Bridge</h2>
        <p>The connection to Tally, writing to it, bills in Tally, the watched folder and its backups are in
          <a href="/bridge/settings/">the Invoice Bridge's Settings</a>.</p></section>
      <section class="block"><h2>The app</h2>
        ${updateBlock(st.update, s)}
        ${dataFolder(st.dataFolder)}
        <label class="check"><input type="checkbox" data-setting="auto_save_runs" ${s.auto_save_runs !== false ? 'checked' : ''}>
          Keep every check, so you can open it again and download its reports</label>
        <p><button class="btn" type="button" data-act="close-app">Close the app</button></p>
      </section>
    </div></div>`;
}
