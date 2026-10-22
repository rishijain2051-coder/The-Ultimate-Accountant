/* exports - the Exports module's two screens, as HTML strings built from state. */

import { CHEVRON, TICK, day, dayShort, esc, inr, num, plural, when } from './core.js';
import { colourKey, fileActions, groupCounts, keptFolder, lookUp, openList, openSorted, runName } from './views.js';

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.xlsx,.xls,.csv,.json';
const DONE = new Set(['resolved', 'ignore', 'match']);
const STAGES = [['sb', 'Shipping bill'], ['bl', 'Bill of lading'], ['gst', 'GSTR-1'],
                ['paid', 'Payment'], ['ebrc', 'eBRC'], ['refund', 'Refunds']];

/** 2025-26 - the financial year a date falls in. */
const fyOf = iso => {
  if (!iso) return '';
  const y = +iso.slice(0, 4), m = +iso.slice(5, 7);
  const start = m >= 4 ? y : y - 1;
  return `${start}-${String(start + 1).slice(2)}`;
};
const whenOf = r => r.sb?.sb_date || r.inv_date;

/** USD 22,800.00 - foreign money keeps its own grouping. */
export function fc(v, cur) {
  if (v === null || v === undefined || v === '') return '—';
  const n = Math.abs(Number(v)).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${cur || ''} ${n}`.trim();
}

// The engine's word for each stage, as one of the pen's marks.
const MARK = { ok: 'ok', marked: 'ok', look: 'look', missing: 'look', problem: 'serious', pending: 'wait', na: 'wait' };
const mark = state => {
  const m = MARK[state] || 'wait';
  return `<span class="mark ${m}">${m === 'ok' ? TICK : '<i></i>'}</span>`;
};

function dropZone(title, hint, slim = false) {
  return `<label class="blank drop${slim ? ' slim' : ''}" data-slot="exdocs">
    <input type="file" multiple accept="${ACCEPT}" data-file="exdocs">
    <span class="blank-title">${esc(title)}</span>
    <span class="blank-hint">${esc(hint)}</span>
  </label>`;
}

const ticked = (st, r) => DONE.has(st.decisions[r.fp]?.action);

/* ── the start ───────────────────────────────────────────────────────── */

const READS = [
  ['Shipping bill', 'The PDF from ICEGATE, or the copy your customs broker sends.'],
  ['Bill of lading or airway bill', 'From your forwarder. A scan or a photo is fine.'],
  ['GSTR-1', 'The JSON from the GST portal, or the Excel from the offline tool.'],
  ['Bank advice (FIRA)', 'What your bank sends when a buyer pays.'],
  ['DGFT IRM and eBRC lists', 'The Excel downloads from DGFT’s eBRC pages.'],
  ['Shipping bill status', 'ICEGATE’s “View Access Data” sheet, as Excel.'],
  ['Your export invoice', 'PDF or Excel. It settles which side is wrong when numbers differ.'],
  ['Your own shipping bill register', 'The Excel sheet you keep today - a tab per year, with the GST and drawback received and any ICEGATE error code. Years of records carry over.'],
  ['ICEGATE refund and drawback status', 'The IGST refund and drawback status downloads, with the response code on each shipping bill.'],
  ['The bank’s EDPMS statement', 'The list of shipping bills your bank still holds as unpaid, in Excel or PDF.'],
];

export function exportsHome(st) {
  const regs = st.workspaces.filter(w => w.module === 'exports');
  const steps = ['Add what you have', 'See what is missing', 'Add the rest as it comes'];
  return `<div class="page">
    <nav class="margin" aria-label="Steps">
      <ol class="steps">${steps.map((s, i) => `<li ${i ? '' : 'aria-current="step"'}>
        <span class="step-n">${i + 1}</span>${esc(s)}</li>`).join('')}</ol>
    </nav>
    <div class="sheet">
      <h1 class="verdict">Every export invoice, and what is still missing from it</h1>
      <p class="lede">Add your shipping bills, bills of lading, GSTR-1, the bank’s advices and the DGFT
        downloads – any number, in any order. Each is filed against the invoice it belongs to, and every
        invoice says what is still to come: the paperwork, the GST refund, the buyer’s money and the eBRC.</p>
      ${dropZone('Drop the documents here', 'Or click to choose them. Any number at once – they are sorted for you.')}
      <div class="go">
        ${st.samples.exports ? '<button class="btn" type="button" data-act="ex-sample">Try it with sample documents</button>' : ''}
        <p class="quiet">Everything stays on this computer.${st.samples.exports
          ? ' The sample is a made-up exporter with 23 invoices and a problem planted in most of them.' : ''}</p>
      </div>
      <section class="block">
        <h2>What it reads</h2>
        <p class="hint">Add each document when you get it. Nothing needs renaming or sorting first.</p>
        <dl class="reads">${READS.map(([t, d]) => `<div><dt>${esc(t)}</dt><dd>${esc(d)}</dd></div>`).join('')}</dl>
      </section>
      ${regs.length ? `<section class="block" id="runs">
        <h2>Your export registers</h2>
        <p class="hint">One register per exporter. It keeps every document added to it.</p>
        <ol class="runs">${regs.map(w => `<li class="run" data-on="${w.id === st.ex.wsId ? 1 : 0}">
          <span class="run-when">${esc(when(w.updated_at))}</span>
          <span class="run-name">${esc(runName(w))}</span>
          <span class="run-parts">${esc(plural(w.doc_count || 0, 'document'))}</span>
          <span class="run-acts">
            <button class="btn small" type="button" data-open-run="${esc(w.id)}">Open</button>
            <button class="link danger" type="button" data-del-run="${esc(w.id)}">Delete</button>
          </span></li>`).join('')}</ol>
      </section>` : ''}
    </div>
  </div>`;
}

/* ── one register ────────────────────────────────────────────────────── */

export function registerPage(st) {
  const d = st.ex.data;
  if (!d) return '<div class="page"><div class="sheet"><h1 class="verdict">Opening the register…</h1></div></div>';
  return `<div class="page">
    ${margin(st, d)}
    <div class="sheet">
      ${fromDownloads(st)}
      ${review(st, d)}
      ${summary(st, d)}
      ${invoices(st, d)}
      ${openList(st, 'exports', d.all_records || [], { id: 'open', title: 'Needs a look' })}
      ${money(d)}
      ${ebrc(d)}
      ${drafts(d)}
      ${documents(st, d)}
      ${downloads(d)}
      ${history(st, d)}
    </div>
  </div>`;
}

/* ── lists waiting in the Downloads folder ───────────────────────────── */

function fromDownloads(st) {
  const files = st.ex.downloads || [];
  if (!files.length) return '';
  return `<section class="block dl-offer" aria-labelledby="dlTitle">
    <h2 id="dlTitle">In your Downloads folder</h2>
    <ul class="plain">${files.map(f => `<li><span><b>${esc(f.type_words)}</b> · ${esc(f.name)}
      <span class="quiet">saved ${esc(when(f.saved))}</span></span>
      <button class="btn small primary" type="button" data-dl-add="${esc(f.id)}">Add it</button>
      <button class="link" type="button" data-dl-skip="${esc(f.id)}">Not this one</button></li>`).join('')}</ul>
    <p class="hint">Saved in the last week and not in this register yet. Added, it waits to be checked like any other
      document; the file itself stays where it is.</p>
  </section>`;
}

/* ── checking what was read, before it goes in ───────────────────────── */

const openOriginal = (wsId, id, words = 'Open the original') => `<a class="link" href="/api/files/${
  encodeURIComponent(wsId)}/${encodeURIComponent(id)}" target="_blank" rel="noopener">${words}<span class="sr">, in a new tab</span></a>`;

/** What the AI checker made of a document, once it has looked (app/backend/ai_check.py). */
const AI_WORDS = {
  agrees: 'the AI reads it the same', differs: 'the AI reads it differently',
  waiting: 'waiting for the AI checker', checking: 'the AI checker is reading it', failed: 'the AI could not read it',
};
const aiTag = a => a?.state ? `<span class="tag ${a.state === 'agrees' ? 'ok' : a.state === 'differs' ? '' : 'idle'}">${
  AI_WORDS[a.state]}</span>` : '';
const aiSaid = a => a?.state !== 'differs' ? '' : `<div class="ai-said">
    <p><b>The AI checker reads it differently.</b> Open the original and see which is right.</p>
    <ul>${a.kind_differs ? `<li>Kind: the AI takes it for ${esc(a.kind_words || 'another kind of document')}</li>` : ''}${
      (a.differences || []).map(f => `<li>${esc(f.label)}: the app read ${esc(f.app)}; the AI reads ${esc(f.ai)}</li>`)
        .join('')}</ul></div>`;

function review(st, d) {
  const docs = d.pending || [];
  if (!docs.length) return '';
  const money = x => ['fira', 'irm_list'].includes(x.type);
  const where = x => x.placed.length
    ? `${money(x) ? 'Its money will be counted against' : 'Will be filed against'} ${esc(x.placed.slice(0, 8).join(', '))}${
      x.placed.length > 8 ? ` and ${num(x.placed.length - 8)} more` : ''}.`
    : x.type === 'unknown' ? 'Not recognised. Correct it to say what it is and fill in its numbers, or remove it.'
    : x.type === 'fira' ? 'Nothing on it places the money. When you post it, you choose which invoices it pays.'
    : money(x) ? 'None of its money is placed yet; each payment can be set against invoices once it is posted.'
    : 'Matches no invoice on file yet. Posted as it is, it waits under Documents not placed.';
  return `<section class="block review" id="review" aria-labelledby="reviewTitle">
    <h2 class="verdict" id="reviewTitle">Check what was read before it goes in</h2>
    <p class="lede">${esc(plural(docs.length, 'document is', 'documents are'))} waiting. Look at what was read from each –
      the invoice, bill of lading and shipping bill numbers, the dates and the money – and where it will be filed.
      Nothing here counts in the register until you post it.</p>
    <ol class="check-list">${docs.map(x => `<li class="check-doc" id="p-${esc(x.id)}">
      <p class="check-head"><b>${esc(x.type_words)}</b><span>${esc(x.kept_as || x.name)}</span>${
        x.read_by === 'recognition' ? '<span class="tag">read from a scan</span>' : ''}${
        x.corrected ? '<span class="tag ok">corrected by you</span>' : ''}${aiTag(x.ai)}</p>
      ${x.facts.length ? `<dl class="facts">${x.facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`)
        .join('')}</dl>` : ''}
      ${aiSaid(x.ai)}
      <p class="check-where">${mark(x.placed.length ? 'ok' : money(x) ? 'wait' : 'look')}<span>${where(x)}</span></p>
      ${x.notes.length ? `<ul class="notes">${x.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
      <div class="check-acts">
        <button class="btn small primary" type="button" data-post="${esc(x.id)}">Post it</button>
        ${x.ai?.state === 'differs' ? `<button class="btn small" type="button" data-ai-use="${esc(x.id)}">Use the AI’s reading</button>` : ''}
        <button class="btn small" type="button" data-correct="${esc(x.id)}">Correct what was read</button>
        ${openOriginal(st.ex.wsId, x.id)}
        <button class="link danger" type="button" data-del-doc="${esc(x.id)}">Remove</button>
      </div></li>`).join('')}</ol>
    ${docs.length > 1 ? `<p class="go"><button class="btn primary" type="button" data-post-all>Post all ${num(docs.length)}</button>
      <span class="quiet">Each goes in as it is shown here.</span></p>` : ''}
  </section>`;
}

/* ── the correcting window ───────────────────────────────────────────── */

/** One box for one value, by the kind of value it is. */
export function control(f, value, attrs) {
  const v = value ?? '';
  if (f.kind === 'bool') return `<input type="checkbox" ${v ? 'checked' : ''} ${attrs}>`;
  if (f.kind === 'choice') {
    return `<select ${attrs}><option value="">—</option>${f.options.map(o => `<option ${o === v ? 'selected' : ''}>${
      esc(o)}</option>`).join('')}</select>`;
  }
  const type = f.kind === 'date' ? 'date' : f.kind === 'month' ? 'month' : 'text';
  const mode = f.kind === 'number' ? ' inputmode="decimal"' : '';
  const hint = ['list', 'refs'].includes(f.kind) ? ' placeholder="Separate them with commas"' : '';
  return `<input type="${type}"${mode}${hint} value="${esc(v)}" ${attrs}>`;
}

const cellText = (c, v) => (c.kind === 'bool' ? (v ? 'yes' : '') : c.kind === 'date' ? dayShort(v) : v ?? '');

export function rowView(t, r) {
  return `<tr data-i="${r._i}">${t.columns.map(c => `<td>${esc(cellText(c, r[c.key]))}</td>`).join('')}
    <td class="acts"><button class="link" type="button" data-row-edit>Change</button>
      <button class="link danger" type="button" data-row-remove>Remove</button></td></tr>`;
}

export function rowEdit(t, r, fresh = false) {
  return `<tr data-i="${fresh ? '' : r._i}" data-editing="1" ${fresh ? 'data-new="1"' : ''}>${t.columns.map(c =>
    `<td>${control(c, r[c.key], `data-c="${esc(c.key)}" aria-label="${esc(c.label)}"`)}</td>`).join('')}
    <td class="acts"><button class="link danger" type="button" data-row-remove>Remove</button></td></tr>`;
}

export function docForm(o, wsId) {
  const kinds = o.types.length ? `<label class="field kind"><span>What this document is</span>
      <select data-doc-kind>${o.types.some(([k]) => k === o.type) ? '' : `<option value="${esc(o.type)}" selected>${
        esc(o.type_words)}</option>`}${o.types.map(([k, w]) => `<option value="${esc(k)}" ${k === o.type ? 'selected' : ''}>${
        esc(w)}</option>`).join('')}</select></label>` : '';
  const fields = o.fields.length ? `<div class="fields">${o.fields.map(f => `<label class="${f.kind === 'bool'
    ? 'check' : 'field'}${f.corrected ? ' fixed' : ''}">${f.kind === 'bool' ? '' : `<span>${esc(f.label)}</span>`}${
    control(f, f.value, `data-f="${esc(f.key)}"`)}${f.kind === 'bool' ? esc(f.label) : ''}${f.corrected
    ? `<small>Read as ${esc(f.read)}</small>` : ''}</label>`).join('')}</div>` : '';
  const tables = o.tables.map(t => `<section class="doc-table" data-table="${esc(t.key)}">
      <h3 class="sub-head">${esc(t.label)} <span class="count">${num(t.rows.length)}</span>${t.corrected
        ? ' <span class="tag ok">corrected by you</span>' : ''}</h3>
      ${t.rows.length > 12 ? `<input type="search" data-row-filter placeholder="Find a row" aria-label="Find a row in ${
        esc(t.label.toLowerCase())}">` : ''}
      <div class="table-wrap"><table class="list edit-rows">
        <thead><tr>${t.columns.map(c => `<th scope="col">${esc(c.label)}</th>`).join('')}<th scope="col"><span class="sr">Change</span></th></tr></thead>
        <tbody>${t.rows.map(r => rowView(t, r)).join('')}</tbody>
      </table></div>
      ${t.can_add ? '<button class="link" type="button" data-row-add>Add a row</button>' : ''}
    </section>`).join('');
  return `<form class="doc-form" data-doc-form="${esc(o.id)}" novalidate>
    <h2 id="docEditTitle">Correct what was read</h2>
    <p class="lede">${esc(o.type_words)} · ${esc(o.kept_as || o.name)} · ${openOriginal(wsId, o.id, 'open the original')} to compare.
      ${o.read_by === 'recognition' ? 'It was read from a scan, so check the numbers closely.' : ''}</p>
    ${kinds}${fields}${tables}
    ${!o.fields.length && !o.tables.length ? '<p class="quiet">Say what this document is, and its boxes appear.</p>' : ''}
    <p class="settle-err" data-doc-err role="alert" hidden></p>
    <div class="go">
      <button class="btn primary" type="submit">Save the corrections</button>
      <button class="link" type="button" data-doc-close>Cancel</button>
      <span class="quiet">What was read is kept; the history keeps both.</span>
    </div>
    <p class="hint">Read before this copy of the app knew its layout?
      <button class="link" type="button" data-doc-reread>Read the file again</button> – it then waits to be checked again.</p>
  </form>`;
}

/* ── the history ─────────────────────────────────────────────────────── */

function history(st, d) {
  const all = d.audit || [];
  const shown = all.slice(0, st.ex.histLimit || 25);
  return `<section class="block" id="history">
    <h2>History <span class="count">${num(all.length)}${all.length >= 400 ? '+' : ''}</span></h2>
    <p class="hint">Everything done to this register, newest first: each document added, checked, corrected (with what it
      said before), posted or removed, each payment you placed, each yes or no you gave, and each entry you decided on.
      The register's Excel download carries all of it on a sheet of its own.</p>
    ${all.length ? `<ol class="history">${shown.map(a => `<li>
      <span class="h-when">${esc(dayShort(a.at))}<small>${esc(a.at.slice(11, 16))}</small></span>
      <span class="h-what">${esc(a.detail)}${a.changes.length
        ? `<ul class="h-changes">${a.changes.map(c => `<li>${esc(c.field)}: <s>${esc(c.old || 'blank')}</s> → <b>${
          esc(c.new || 'blank')}</b></li>`).join('')}</ul>` : ''}${a.by ? `<small>${esc(a.by)}</small>` : ''}</span></li>`).join('')}</ol>
      ${all.length > shown.length ? `<button class="btn" type="button" data-hist-more>Show ${num(Math.min(25,
        all.length - shown.length))} more</button>` : ''}`
    : '<p class="quiet">Nothing yet.</p>'}
  </section>`;
}

function margin(st, d) {
  const open = openSorted(st, 'exports', d.all_records || []);
  const left = open.filter(r => !ticked(st, r)).length;
  const pick = st.filter.open?.group;
  return `<nav class="margin" aria-label="This register">
    <h2 class="margin-head">On this page</h2>
    <ul class="index">
      ${(d.pending || []).length ? `<li><button type="button" data-jump="review"><b>To check (${num(d.pending.length)})</b></button></li>` : ''}
      <li><button type="button" data-jump="summary">Summary</button></li>
      <li><button type="button" data-jump="invoices">Invoices (${num(d.invoices.length)})</button></li>
      <li><button type="button" data-jump="open">Needs a look (${num(left)})</button>
        ${open.length ? `<ul class="sub">${groupCounts(st, 'exports', open).map(([g, n]) => `<li>
          <button type="button" data-pick="${esc(g)}" aria-pressed="${pick === g}">${esc(g)}
            <span class="count">${num(n)}</span></button></li>`).join('')}</ul>` : ''}</li>
      <li><button type="button" data-jump="money">Money received</button></li>
      <li><button type="button" data-jump="ebrc">eBRC</button></li>
      <li><button type="button" data-jump="drafts">Drafts</button></li>
      <li><button type="button" data-jump="docs">Documents (${num(d.documents.length)})</button></li>
      <li><button type="button" data-jump="files">Downloads</button></li>
      <li><button type="button" data-jump="history">History</button></li>
    </ul>
    <button class="link add" type="button" data-act="add-docs">Add documents</button>
    ${colourKey('', true)}
  </nav>`;
}

function summary(st, d) {
  const f = d.figures || {};
  const open = (d.all_records || []).filter(r => !ticked(st, r));
  const serious = open.filter(r => r.tone === 'serious').length;
  const needy = new Set(open.filter(r => r.key).map(r => r.key));
  const complete = d.invoices.length - d.invoices.filter(r => needy.has(r.key)).length;
  const n = needy.size;
  const h1 = !d.invoices.length ? 'No export invoices here yet'
    : n ? `${num(n)} of ${plural(d.invoices.length, 'invoice')} ${n === 1 ? 'needs' : 'need'} something from you`
    : 'Every invoice is complete';
  const lede = d.invoices.length
    ? `${esc(plural(f.shipped, 'invoice has', 'invoices have'))} gone out on a shipping bill, and
      ${num(f.paid_count)} ${f.paid_count === 1 ? 'is' : 'are'} fully paid.
      <span class="counts"><b class="ok">${plural(complete, 'invoice')} complete</b>, <b class="look">${num(open.length - serious)}
      ${open.length - serious === 1 ? 'entry needs' : 'entries need'} a look</b>${serious
        ? `, <b class="serious">${num(serious)} serious</b>` : ''}.</span>`
    : (d.pending || []).length ? 'Post the documents waiting above, and their invoices appear here.'
    : 'Add a shipping bill, a GSTR-1 or an invoice and it will appear here.';
  const fig = (label, note, value, cls = '') => `<div><dt>${esc(label)}${note
    ? `<small>${esc(note)}</small>` : ''}</dt><dd class="${cls}">${value}</dd></div>`;
  return `<section class="block summary" id="summary">
    <h1 class="verdict">${esc(h1)}</h1>
    <p class="lede">${lede}</p>
    <dl class="figures">
      ${fig('IGST refund held up', f.refund_at_risk_count
        ? `on ${plural(f.refund_at_risk_count, 'invoice')}, until the return or the shipping bill is put right`
        : 'nothing is held up', inr(f.refund_at_risk), f.refund_at_risk ? 'serious' : 'ok')}
      ${fig('Overdue from buyers', f.overdue_count
        ? `${plural(f.overdue_count, 'invoice')} past the date the money was due` : 'nothing is overdue',
        inr(f.overdue_inr), f.overdue_inr ? 'serious' : 'ok')}
      ${fig(`Due in the next ${d.rules?.due_soon_days ?? 90} days`, f.due_90_count ? plural(f.due_90_count, 'invoice')
        : 'nothing falls due', inr(f.due_90_inr))}
      ${fig('Still to come from buyers', 'every invoice not yet paid, at the shipping bill’s exchange rate',
        inr(f.to_receive_inr))}
      ${fig('eBRCs to generate', 'payments received that no eBRC covers yet', num(f.ebrc_to_generate),
        f.ebrc_to_generate ? '' : 'ok')}
      ${fig('Bills of lading missing', 'for goods that left more than 10 days ago', num(f.bl_missing),
        f.bl_missing ? '' : 'ok')}
      ${d.has?.own_register || d.has?.refund_status ? fig('Refunds and drawback not received',
        f.refund_pending_count ? `on ${plural(f.refund_pending_count, 'shipping bill')}, counting those held up above`
        : 'everything due has come back', inr(f.refund_pending_inr), f.refund_pending_inr ? 'serious' : 'ok') : ''}
    </dl>
    ${(d.notes || []).length ? `<ul class="notes">${d.notes.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
    ${dropZone('Got a new document? Drop it here', 'It is read and filed against its invoice straight away. The same file twice is kept once.', true)}
    <p class="source">Worked out on ${esc(day(d.as_at))} from ${esc(plural(d.documents.length, 'document'))}.
      Payment deadlines count from ${esc(d.rules?.counted_from_words || 'the shipping bill date')}.</p>
  </section>`;
}

/* ── the register of invoices ────────────────────────────────────────── */

const FILTERS = [
  ['all', 'Every invoice', () => true],
  ['open', 'Needs something from you', (r, needy) => needy.has(r.key)],
  ['sb', 'No shipping bill yet', r => !['ok', 'marked'].includes(r.chips.sb.state)],
  ['bl', 'No bill of lading yet', r => r.chips.bl.state === 'missing'],
  ['gst', 'GSTR-1 not right', r => ['problem', 'look'].includes(r.chips.gst.state)],
  ['unpaid', 'Money still to come', r => r.due && r.due.state !== 'paid'],
  ['overdue', 'Payment overdue', r => r.due?.state.startsWith('overdue')],
  ['ebrc', 'eBRC to generate', r => r.chips.ebrc.state === 'look'],
  ['refunds', 'Refund or drawback not in', r => ['held', 'pending'].includes(r.refund?.igst?.state)
    || ['held', 'pending'].includes(r.refund?.dbk?.state)],
];

function invoices(st, d) {
  if (!d.invoices.length) return '';
  const v = st.ex.inv;
  const needy = new Set((d.all_records || []).filter(r => r.key && !ticked(st, r)).map(r => r.key));
  const test = (FILTERS.find(([k]) => k === v.filter) || FILTERS[0])[2];
  const q = v.q.trim().toLowerCase();
  const years = [...new Set(d.invoices.map(r => fyOf(whenOf(r))).filter(Boolean))].sort().reverse();
  const inYear = r => !v.fy || fyOf(whenOf(r)) === v.fy;
  const rows = d.invoices.filter(r => inYear(r) && test(r, needy) && (!q || [r.inv_no, r.buyer, r.sb?.sb_no,
    ...STAGES.map(([k]) => r.chips[k].text)].join(' ').toLowerCase().includes(q)));
  return `<section class="block" id="invoices">
    <h2>Invoices <span class="count">${num(d.invoices.length)}</span></h2>
    <p class="hint">One line per export invoice, one column per thing it needs. Open a line to see every
      document on file for it.</p>
    <div class="tools-row">
      <label class="show">Show
        <select data-inv-filter>${FILTERS.map(([k, label, fn]) => `<option value="${k}" ${v.filter === k ? 'selected' : ''}>${
          esc(label)} (${num(d.invoices.filter(r => inYear(r) && fn(r, needy)).length)})</option>`).join('')}</select></label>
      ${years.length > 1 ? `<label class="show">Year
        <select data-inv-fy><option value="">Every year</option>${years.map(y => `<option value="${y}" ${
          v.fy === y ? 'selected' : ''}>${y}</option>`).join('')}</select></label>` : ''}
      <input type="search" data-inv-search value="${esc(v.q)}" placeholder="Search invoice, buyer or shipping bill" aria-label="Search the invoices">
    </div>
    ${rows.length ? `<div class="table-wrap"><table class="register">
      <thead><tr><th scope="col">Invoice</th>${STAGES.map(([, l]) => `<th scope="col">${l}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(r => invoiceRow(st, d, r)).join('')}</tbody>
    </table></div>` : '<p class="quiet">No invoice matches that. Clear the search or show every invoice.</p>'}
  </section>`;
}

function invoiceRow(st, d, r) {
  const open = st.ex.openInv.has(r.key);
  return `<tr class="inv" id="i-${esc(r.key)}" data-open="${open ? 1 : 0}">
      <th scope="row"><button class="inv-line" type="button" data-inv="${esc(r.key)}" aria-expanded="${open}">
        <span class="inv-no">${esc(r.inv_no)}${CHEVRON}</span>
        <span class="inv-sub">${esc([dayShort(r.inv_date), r.buyer].filter(Boolean).join(' · '))}</span>
        ${r.value_fc ? `<span class="inv-sub">${esc(fc(r.value_fc, r.currency))}</span>` : ''}</button></th>
      ${STAGES.map(([k, l]) => `<td data-label="${l}"><div class="stage">${mark(r.chips[k].state)}<span>${
        esc(r.chips[k].text)}</span></div></td>`).join('')}
    </tr>${open ? `<tr class="inv-file"><td colspan="${STAGES.length + 1}">${invoiceFile(st, d, r)}</td></tr>` : ''}`;
}

/** Everything on file for one invoice, stage by stage. */
function invoiceFile(st, d, r) {
  const ws = encodeURIComponent(st.ex.wsId);
  const opener = id => id ? ` <a class="open-doc" href="/api/files/${ws}/${encodeURIComponent(id)}"
    target="_blank" rel="noopener">Open<span class="sr"> the original, in a new tab</span></a>
    <button class="link open-doc" type="button" data-correct="${esc(id)}">Correct</button>` : '';
  // The invoice's own papers, to hand over together: not the lists that cover every invoice.
  const byId = Object.fromEntries((d.documents || []).map(x => [x.id, x]));
  const papers = [...new Set([...r.sbs.map(s => s.doc_id), ...r.bls.map(b => b.id), r.invoice_doc?.id,
    ...r.receipts.flatMap(m => (d.remittances || []).find(x => x.id === m.remittance_id)?.doc_ids || [])])]
    .filter(id => ['sb', 'bl', 'invoice', 'fira'].includes(byId[id]?.type));
  const sbs = r.sbs.map(s => `${esc(s.sb_no)} at ${esc(s.port)}, ${esc(day(s.sb_date))}${s.leo_date
    ? ` · let export order ${esc(day(s.leo_date))}` : ''} · ${s.scheme === 'WPAY'
    ? `IGST of ${inr(s.igst_amount)} paid, to come back as a refund` : 'under LUT, no IGST'}${opener(s.doc_id)}`);
  const bls = r.bls.map(b => `${esc(b.bl_no || 'Number not read')}${b.sob_date ? ` · shipped on board ${esc(day(b.sob_date))}` : ''}${
    b.vessel ? ` · ${esc(b.vessel)}` : ''}${b.read_by === 'recognition' ? ' · read from a scan' : ''}${opener(b.id)}`);
  const g = r.gst;
  const gst = (g ? `In Table 6A as ${esc(g.inum)} of ${esc(day(g.idt))} (${esc(g.exp_typ || '')}) · shipping bill ${
    esc(g.sbnum || 'left blank')}${g.sbpcode ? ` at ${esc(g.sbpcode)}` : ''} · IGST ${inr(g.iamt)}${
    g.doc_name ? ` · from ${esc(g.doc_name)}` : ''}${r.gst_lines.length > 1 ? ` · ${num(r.gst_lines.length)} lines` : ''}`
    : r.marks?.gstr1?.said ? 'No Table 6A line for it in the returns added here.' : esc(r.chips.gst.text))
    + yesNo(r, 'gstr1');
  const mine = (d.all_records || []).filter(x => x.key === r.key);
  const rows = [
    ['Shipping bill', sbs.join('<br>') || 'None on file yet.'],
    ['Bill of lading', bls.join('<br>') || (r.sb ? 'Not received yet.' : 'Comes after the shipping bill.')],
    ['GSTR-1', gst],
    ['Your invoice', r.invoice_doc ? `${esc(r.invoice_doc.name)}${opener(r.invoice_doc.id)}` : 'Not added. It is optional.'],
    ['Payment', payment(st, d, r)],
    ['eBRC', r.ebrcs.length ? r.ebrcs.map(e => `${esc(e.ebrc_no)} of ${esc(day(e.ebrc_date))} for ${
      esc(fc(e.realised_fc, e.currency))}`).join('<br>') : esc(r.chips.ebrc.text)],
    ...refunds(r),
    ...(r.bank_edpms ? [['Bank (EDPMS)', `${esc(r.bank_edpms.status || 'Listed')} · ${esc(fc(r.bank_edpms.outstanding_fc,
      r.bank_edpms.currency || r.currency))} outstanding${r.bank_edpms.due ? ` · due ${esc(day(r.bank_edpms.due))}` : ''
      }<br><span class="quiet">From ${esc(r.bank_edpms.doc_name)}</span>`]] : []),
    ...(r.dbk_brc ? [['Customs drawback list', `Customs wants proof that the buyer paid${r.dbk_brc.expected
      ? ` by ${esc(day(r.dbk_brc.expected))}` : ''}${r.dbk_brc.dbk ? ` (drawback ${inr(r.dbk_brc.dbk)})` : ''}.`]] : []),
    ...(r.own?.length ? [['Your register', r.own.map(o => `${esc(o.sheet)}, row ${esc(o.row)}${o.notes?.length
      ? ` · “${esc(o.notes.join('; '))}”` : ''}`).join('<br>') + (r.sb?.from_register
      ? '<br><span class="quiet">Only your register’s copy of this shipping bill is on file: add the PDF to check it against GSTR-1.</span>' : '')]] : []),
    ['Papers', papers.length ? `${esc(plural(papers.length, 'paper'))} on file: ${esc(papers.map(id => byId[id].type_words)
      .join(', '))}. <button class="link" type="button" data-kept="zip" data-ws="${esc(st.ex.wsId)}" data-ids="${
      esc(papers.join(','))}" data-name="${esc(`${r.inv_no} papers`)}">Save them as one zip</button>`
      : 'None of this invoice’s own papers is on file yet.'],
    ['Still open', mine.length ? `<ul class="plain">${mine.map(x => `<li><button class="link" type="button"
      data-goto-entry="${esc(x.fp)}">${esc(x.why.title)}</button>${ticked(st, x) ? ' <span class="quiet">(ticked off)</span>' : ''}</li>`).join('')}</ul>`
      : `<span class="done-line">${TICK}Nothing. This invoice is complete as far as the documents go.</span>`],
  ];
  return `<dl class="why file">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>`;
}

const REFUND_WORDS = { received: 'came back', short: 'came back short', held: 'held', pending: 'not received',
                       waiting: 'not due back yet', unknown: 'no status on file - look it up on ICEGATE' };

/** The IGST refund and the drawback, as the invoice file tells them. */
function refunds(r) {
  const rf = r.refund || {};
  const ig = rf.igst || {}, db = rf.dbk || {};
  if (!r.sb) return [];
  // Once the user has answered, the line under the figures says so instead.
  const from = (x, doc) => doc && !x.manual ? `<br><span class="quiet">From ${esc(doc)}</span>` : '';
  const igst = ig.state === 'na' || !ig.state
    ? 'None: no IGST was paid on this export (under LUT).'
    : [`${inr(ig.due)} paid on the shipping bill`,
       ['received', 'short'].includes(ig.state) ? `${inr(ig.recd)} came back${ig.recd_date ? ` on ${day(ig.recd_date)}` : ''}`
         : ig.state === 'held' ? `held: ICEGATE shows ${ig.code}` : REFUND_WORDS[ig.state],
       ig.scroll_no ? `scroll ${ig.scroll_no}${ig.scroll_date ? ` of ${day(ig.scroll_date)}` : ''}` : '',
       ig.code && ig.state !== 'held' ? `ICEGATE code ${ig.code}` : ''].filter(Boolean).map(esc).join(' · ')
      + from(ig, ig.source || ig.code_from) + yesNo(r, 'igst');
  const dbk = db.state === 'na' || !db.state
    ? 'None claimed on this shipping bill.'
    : [`${inr(db.due)} claimed`,
       db.state === 'received' ? `received${db.recd_date ? ` on ${day(db.recd_date)}` : ''}`
         : db.state === 'held' ? `held: “${db.status_text}”` : REFUND_WORDS[db.state],
       db.scroll_no ? `scroll ${db.scroll_no}` : ''].filter(Boolean).map(esc).join(' · ')
      + from(db, db.source || db.status_from);
  const rodtep = r.sb.rodtep_amount ? `<br><span class="quiet">RoDTEP: ${inr(r.sb.rodtep_amount)} claimed, credited as an e-scrip on ICEGATE.</span>` : '';
  return [['IGST refund', igst], ['Drawback', dbk + rodtep + yesNo(r, 'dbk')],
    ...(r.icegate ? [['On ICEGATE', lookUp(r.icegate)]] : [])];
}

const ASK = {
  gstr1: ['Filed in GSTR-1?', 'filed', 'not filed'],
  igst: ['Refund received?', 'received', 'not received'],
  dbk: ['Drawback received?', 'received', 'not received'],
};

/**
 * The user's own yes or no, under what the documents say. It counts over
 * them until undone - for a return not added here, or a refund that reached
 * the bank with no download to show it.
 */
function yesNo(r, what) {
  const x = r.marks?.[what];
  if (!x?.applies) return '';
  const [q, yes, no] = ASK[what];
  const word = v => (v === 'yes' ? yes : no);
  const b = (val, label) => `<button type="button" class="yn-b" data-mark="${what}" data-mark-inv="${esc(r.key)}"
    data-val="${val}" aria-pressed="${x.value === val}">${label}</button>`;
  const said = x.said
    ? `You marked it ${word(x.said)}${x.at ? ` on ${esc(day(x.at))}` : ''}${x.doc && x.doc !== x.said
      ? `; the documents say ${word(x.doc)}` : ''}. <button class="link" type="button" data-mark="${what}"
      data-mark-inv="${esc(r.key)}" data-val="">Undo</button>`
    : x.doc ? 'From the documents. Change it if they are wrong.' : 'No document says yet. Answer it yourself.';
  return `<div class="yn" role="group" aria-label="${esc(q)}"><span class="yn-q">${esc(q)}</span>
    <span class="yn-pair">${b('yes', 'Yes')}${b('no', 'No')}</span><span class="yn-from">${said}</span></div>`;
}

const HOW = { eBRC: 'its eBRC names this invoice', 'named on the bank advice': 'the bank advice names this invoice',
              'you confirmed': 'you confirmed it' };

function payment(st, d, r) {
  const due = r.due;
  if (!due) return 'The deadline starts from the shipping bill, so it is worked out once that is added.';
  const cur = r.currency;
  const top = due.state === 'paid'
    ? `Paid: ${fc(due.received_fc, cur)} received${due.short_by_charges
      ? `, ${fc(due.short_by_charges, cur)} short – taken as the banks’ charges` : ''}.`
    : due.received_fc ? `${fc(due.received_fc, cur)} of ${fc(r.value_fc, cur)} received; ${fc(due.outstanding_fc, cur)} still to come.`
    : `Nothing received yet; ${fc(due.outstanding_fc, cur)} to come.`;
  const rule = `Due by ${day(due.date)}: ${due.months} months from ${due.anchor_words} (${day(due.anchor)}), under ${due.rule}.`;
  const got = r.receipts.map(m => {
    const undo = m.how === 'you confirmed' && Object.entries(st.decisions).find(([, x]) => x.action === 'match'
      && x.context?.remittance === m.remittance_id && x.context?.invoice === r.key)?.[0];
    // What an eBRC proves stays; anything else can be moved to other invoices.
    return `<li>${esc(day(m.date))} · ${esc(fc(m.amount_fc, cur))} from ${esc(m.remitter || 'the buyer')}${
      m.irm_no ? ` · IRM ${esc(m.irm_no)}` : ''} – counted here because ${HOW[m.how] || esc(m.how)}.${undo
      ? ` <button class="link" type="button" data-decide="clear" data-fp="${esc(undo)}">Undo</button>` : ''}${
      m.how !== 'eBRC' ? ` <button class="link" type="button" data-settle="${esc(m.remittance_id)}">Change</button>` : ''}</li>`;
  });
  // Payments the user said are not for this invoice, so they can take that back.
  for (const [fp, x] of Object.entries(st.decisions)) {
    const m = x.action === 'ignore' && x.context?.kind === 'payment' && x.context.invoice === r.key
      && (d.remittances || []).find(y => y.id === x.context.remittance);
    if (m) {
      got.push(`<li class="quiet">You said the ${esc(fc(m.amount_fc, m.currency))} received on ${esc(day(m.date))}
        from ${esc(m.remitter || 'the buyer')} is not for this invoice.
        <button class="link" type="button" data-decide="clear" data-fp="${esc(fp)}">Undo</button></li>`);
    }
  }
  return `${esc(top)}<br><span class="quiet">${esc(rule)}</span>${got.length ? `<ul class="plain">${got.join('')}</ul>` : ''}`;
}

/* ── money, eBRCs, documents ─────────────────────────────────────────── */

function money(d) {
  const rems = d.remittances || [];
  const head = '<h2>Money received</h2>';
  if (!rems.length) {
    return `<section class="block" id="money">${head}<p class="quiet">No payment from abroad has been added yet.
      Add the bank’s advices (FIRAs) or the DGFT IRM list.</p></section>`;
  }
  // A payment with money left, or placed by anything but an eBRC, can be set
  // against invoices of the user's choosing.
  const act = m => {
    if (m.cancelled) return '';
    const open = (m.available_fc || 0) > 0.005;
    if (!open && !m.chosen && !m.mapped.some(x => x.how !== 'eBRC')) return '';
    return `<button class="link" type="button" data-settle="${esc(m.id)}">${
      open && !m.chosen && !m.mapped.length ? 'Choose invoices' : 'Change'}</button>`;
  };
  return `<section class="block" id="money">${head}
    <p class="hint">Every payment from abroad, from the DGFT IRM list and the bank’s advices together. A payment
      counts against an invoice only when its eBRC or the bank’s advice names it, or when you choose the
      invoices it is for.</p>
    <div class="table-wrap"><table class="list">
      <thead><tr><th scope="col">Received</th><th scope="col">From</th><th scope="col" class="money">Amount</th>
        <th scope="col">Counted against</th><th scope="col" class="money">Not yet placed</th>
        <th scope="col"><span class="sr">Choose the invoices</span></th></tr></thead>
      <tbody>${rems.map(m => `<tr ${m.cancelled ? 'data-cancelled="1"' : ''}>
        <td>${esc(dayShort(m.date))}</td>
        <td>${esc(m.remitter || 'Remitter not named')}<small>${esc([m.irm_no ? `IRM ${m.irm_no}` : m.ref,
          m.advance ? 'an advance' : '', m.cancelled ? 'cancelled' : ''].filter(Boolean).join(' · '))}</small></td>
        <td class="money">${esc(fc(m.amount_fc, m.currency))}</td>
        <td>${m.mapped.length ? m.mapped.map(x => `${esc(x.inv_no)} · ${esc(fc(x.amount_fc, m.currency))}`).join('<br>')
          : '<span class="quiet">Nothing yet</span>'}</td>
        <td class="money">${m.cancelled ? '' : mark((m.available_fc || 0) > 0.005 ? 'look' : 'ok')}${
          esc(fc(m.cancelled ? 0 : m.available_fc, m.currency))}</td>
        <td class="acts">${act(m)}</td></tr>`).join('')}</tbody>
    </table></div>
  </section>`;
}

/* ── the settle window ───────────────────────────────────────────────── */

/**
 * Which invoices one payment is for, and how much goes to each. Opens by
 * itself when a bank advice is added that no document places, and from any
 * payment's Choose invoices or Change.
 */
export function settleDialog(o) {
  const m = o.remittance, cur = m.currency, rows = o.candidates;
  const ref = m.irm_no ? `IRM ${m.irm_no}` : m.ref;
  const listed = rows.length ? `<div class="settle-list"><table class="list">
      <thead><tr><th scope="col">Invoice</th><th scope="col">Buyer</th>
        <th scope="col" class="money">Still to come</th><th scope="col" class="money">Set against it</th></tr></thead>
      <tbody>${rows.map(c => `<tr>
        <td><b>${esc(c.inv_no)}</b><small>${esc([c.shipped ? `shipped ${day(c.shipped)}` : day(c.inv_date),
          c.named ? 'named on the advice' : ''].filter(Boolean).join(' · '))}</small><small class="narrow-only">${
          esc(c.buyer || '')}</small></td>
        <td>${esc(c.buyer || '—')}</td>
        <td class="money">${esc(fc(c.room, cur))}</td>
        <td class="money"><input type="number" inputmode="decimal" min="0" step="0.01" max="${c.room}"
          value="${c.suggest ? c.suggest.toFixed(2) : ''}" placeholder="0.00" data-split="${esc(c.key)}"
          aria-label="${esc(`${cur} set against invoice ${c.inv_no}`)}"></td></tr>`).join('')}</tbody>
    </table></div>`
    : `<p class="settle-none">No invoice in ${esc(cur)} is waiting for money. Add the shipping bill for the export
      this pays for, and it can be set against it then.</p>`;
  return `<form class="settle-form" data-settle-form="${esc(m.id)}" novalidate>
    <h2 id="settleTitle">Which invoices is this payment for?</h2>
    <p class="lede">${esc(fc(m.amount_fc, cur))} received on ${esc(day(m.date))} from ${
      esc(m.remitter || 'a remitter the advice does not name')}${ref ? ` · ${esc(ref)}` : ''}.</p>
    ${o.fixed.map(f => `<p class="hint">${esc(fc(f.amount_fc, cur))} of it is already counted against invoice ${
      esc(f.inv_no)}, because its eBRC says so.</p>`).join('')}
    ${rows.length ? `<p class="hint">Type how much goes to each invoice. ${rows.some(c => c.suggest)
      ? 'The amounts filled in are a suggestion, nothing more: change them as you like.'
      : 'Nothing on the advice says which invoices, so nothing is filled in.'}</p>` : ''}
    ${listed}
    <p class="settle-sum" data-settle-sum aria-live="polite"></p>
    <p class="settle-err" data-settle-err role="alert" hidden></p>
    <div class="go">
      ${rows.length ? '<button class="btn primary" type="submit" data-settle-save>Save</button>' : ''}
      ${rows.length > 1 ? '<button class="btn" type="button" data-settle-fill>Fill from the top</button>' : ''}
      <button class="link" type="button" data-settle-close>${rows.length ? 'Not now' : 'Close'}</button>
      ${m.chosen ? '<button class="link" type="button" data-settle-forget>Clear what you chose</button>' : ''}
    </div>
  </form>`;
}

function ebrc(d) {
  const bulk = d.bulk_rows || [];
  const onFile = d.ebrcs || [];
  return `<section class="block" id="ebrc">
    <h2>eBRC ${bulk.length ? `<span class="count">${num(bulk.length)} to generate</span>` : ''}</h2>
    <p class="hint">DGFT makes eBRCs for free. What is listed here has been received and counted against an
      invoice, but no eBRC covers it yet. It is filled in on DGFT’s bulk upload sheet, each row checked the way
      DGFT checks it.</p>
    ${bulk.length ? `<div class="table-wrap"><table class="list">
      <thead><tr><th scope="col">Invoice</th><th scope="col">IRM</th><th scope="col" class="money">Amount</th>
        <th scope="col">Ready to upload</th></tr></thead>
      <tbody>${bulk.map(b => `<tr><td>${esc(b.bill_no)}<small>${esc(b.remitter || '')}</small></td>
        <td class="nowrap">${esc(b.irm_no || 'Not known yet')}<small>${esc(day(b.irm_date))}</small></td>
        <td class="money">${esc(fc(b.mapped_fc, b.currency))}</td>
        <td><div class="stage">${b.problems.length ? `${mark('look')}<span>${esc(b.problems.join('; '))}</span>`
          : `${mark('ok')}<span>Ready</span>`}</div></td></tr>`).join('')}</tbody>
    </table></div>
    <p class="go"><button class="btn" type="button" data-ex-dl="ebrc">Download the bulk upload sheet</button></p>`
    : `<p class="clear-line">${TICK}<span>Nothing to generate. Every payment counted against an invoice has its eBRC.</span></p>`}
    ${onFile.length ? `<h3 class="sub-head">eBRCs on file</h3>
    <div class="table-wrap"><table class="list">
      <thead><tr><th scope="col">eBRC</th><th scope="col">Date</th><th scope="col">Invoice</th>
        <th scope="col" class="money">Realised</th><th scope="col">Status</th></tr></thead>
      <tbody>${onFile.map(e => `<tr ${e.cancelled ? 'data-cancelled="1"' : ''}><td>${esc(e.ebrc_no)}</td>
        <td>${esc(dayShort(e.ebrc_date))}</td><td>${esc(e.gst_inv_no || e.bill_no || '')}</td>
        <td class="money">${esc(fc(e.realised_fc, e.currency))}</td><td>${esc(e.status || '')}</td></tr>`).join('')}</tbody>
    </table></div>` : ''}
  </section>`;
}

const DRAFTS = [
  ['sb005', 'SB005 letters for the port (zip)', x => `${plural(x.count, 'shipping bill')} at ${x.ports.join(', ')}: a request
    letter for each customs station with the Annexure A table, and a challan schedule for ${inr(x.fee)}.`],
  ['table9a', 'Table 9A worksheet (Excel)', x => `${plural(x.count, 'line')} to amend in GSTR-1${x.add
    ? `, and ${plural(x.add, 'export')} to add to this month’s Table 6A` : ''}, each with the date to do it by.`],
  ['sb003', 'SB003 undertaking (Word)', x => `${plural(x.count, 'shipping bill')} filed under another GSTIN of your PAN,
    with the undertaking customs asks for.`],
  ['declaration', 'Declaration to the bank (zip)', x => `${plural(x.count, 'bill')} of up to ₹10 lakh to close or reduce in
    EDPMS: bank charges taken abroad, and overdue amounts if they will not come.`],
  ['extension', 'Request to the bank for more time (zip)', x => `${plural(x.count, 'bill')} overdue or falling due, with the
    date to ask for beside each.`],
  ['lookup', 'Shipping bills to look up on ICEGATE (Excel)', x => `${plural(x.count, 'shipping bill')} whose refund or
    drawback is not confirmed as received.`],
];

function drafts(d) {
  const x = d.drafts || {};
  const ready = DRAFTS.filter(([k]) => x[k]?.count || (k === 'table9a' && x[k]?.add));
  return `<section class="block" id="drafts">
    <h2>Drafts</h2>
    <p class="hint">The papers that fix what is listed above, filled in from the register. Each is a draft: read it,
      fill in what is in square brackets, and sign it.</p>
    ${ready.length ? `<ul class="files">${ready.map(([k, name, words]) => `<li>
      <button class="link" type="button" data-ex-dl="${k}">${esc(name)}</button>
      <span>${esc(words(x[k]).replace(/\s+/g, ' '))}</span></li>`).join('')}</ul>`
    : `<p class="clear-line">${TICK}<span>Nothing to draft. No fix needs a letter or a worksheet.</span></p>`}
    ${x.sb005?.check ? `<p class="source">${esc(plural(x.sb005.check, 'more SB005 case needs', 'more SB005 cases need'))} your
      invoice to say which side is wrong; ${x.sb005.check === 1 ? 'it is' : 'they are'} in the Table 9A worksheet, marked
      to check first, and not in the port letters.</p>` : ''}
  </section>`;
}

function documents(st, d) {
  const docs = (d.documents || []).slice().reverse();          // the newest first
  const types = [...new Set(docs.map(x => x.type_words))];
  const want = st.ex.docType;
  const shown = want ? docs.filter(x => x.type_words === want) : docs;
  const ws = encodeURIComponent(st.ex.wsId);
  return `<section class="block" id="docs">
    <h2>Documents <span class="count">${num(docs.length)}</span></h2>
    <p class="hint">Everything added to this register, with what was read from it and the invoices it was
      filed against. Each original is kept, named for what it is and filed by kind, so any of them can be opened,
      saved or shared from here.</p>
    ${keptFolder(st.ex.wsId, d.folder)}
    ${docs.length ? `<div class="tools-row"><label class="show">Show
      <select data-doc-type><option value="">Every document (${num(docs.length)})</option>${types.map(t =>
        `<option value="${esc(t)}" ${want === t ? 'selected' : ''}>${esc(t)} (${num(docs.filter(x => x.type_words === t).length)})</option>`).join('')}
      </select></label></div>
    <div class="table-wrap"><table class="list docs">
      <thead><tr><th scope="col">Added</th><th scope="col">Document</th><th scope="col">Filed against</th>
        <th scope="col"><span class="sr">Actions</span></th></tr></thead>
      <tbody>${shown.map(x => `<tr ${x.type === 'unknown' ? 'data-unread="1"' : ''}>
        <td>${esc(dayShort(x.added_at))}</td>
        <td><b>${esc(x.what)}</b><small>${esc([x.kept_as ? `${x.folder ? `${x.folder} › ` : ''}${x.kept_as}` : x.name,
          x.kept_as && x.kept_as !== x.name ? `added as ${x.name}` : '', x.read_by === 'recognition' ? 'read from a scan' : '',
          x.corrected ? 'corrected by you' : '', x.ai?.state ? AI_WORDS[x.ai.state] : '',
          ...(x.type === 'unknown' ? x.notes : [])].filter(Boolean).join(' · '))}</small></td>
        <td>${!x.placed.length ? '<span class="quiet">—</span>' : x.placed.length > 6
          ? esc(plural(x.placed.length, 'invoice')) : esc(x.placed.join(', '))}</td>
        <td class="acts">${fileActions(st.ex.wsId, x.id, x.kept_as || x.name)}
          <button class="link" type="button" data-correct="${esc(x.id)}">Correct</button>
          <button class="link danger" type="button" data-del-doc="${esc(x.id)}">Remove</button></td></tr>`).join('')}</tbody>
    </table></div>` : '<p class="quiet">Nothing yet.</p>'}
  </section>`;
}

function downloads(d) {
  const bulk = d.bulk_rows || [];
  return `<section class="block" id="files">
    <h2>Downloads</h2>
    <ul class="files">
      <li><button class="link" type="button" data-ex-dl="register">The register (Excel)</button>
        <span>Every invoice and where it stands, the open entries with their reasons, the money received and the eBRCs.</span></li>
      <li><button class="link" type="button" data-ex-dl="own_format">The register in your own sheet’s layout (Excel)</button>
        <span>A tab per year with the columns you keep - GST and drawback received, the error code - filled in from every document.</span></li>
      ${bulk.length ? `<li><button class="link" type="button" data-ex-dl="ebrc">DGFT bulk eBRC sheet (Excel)</button>
        <span>${esc(plural(bulk.length, 'row'))} in the column order of DGFT’s bulk upload, with the checks on a second tab.</span></li>` : ''}
    </ul>
  </section>`;
}
