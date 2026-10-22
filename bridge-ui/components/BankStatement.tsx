"use client";

import { api, money, shortDate, type Company, type VoucherTypeOption } from "@/lib/api";
import { bank, paise, rupees, type BankRow, type RowLedger, type Statement } from "@/lib/bank";
import { LedgerPicker } from "@/components/LedgerPicker";
import { Banner, Icon, Spinner } from "@/components/ui";
import { Fragment, useEffect, useMemo, useState } from "react";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "pending", label: "To post" },
  { key: "in_tally", label: "In Tally" },
  { key: "posted", label: "Posted" },
  { key: "skipped", label: "Left out" },
  { key: "check", label: "Check the reading" },
] as const;
type Filter = (typeof FILTERS)[number]["key"];

const BANK_GROUPS = ["Bank Accounts", "Bank OD A/c", "Bank OCC A/c"];

/** Ready to post as it stands: its ledgers add up to the line. */
const ready = (r: BankRow) =>
  r.status === "pending" && r.ledgers.length > 0 &&
  r.ledgers.reduce((s, l) => s + paise(l.amount), 0) === Math.abs(paise(r.amount));

/**
 * One bank statement, laid out as the bank printed it, with what each line
 * becomes in Tally beside it. Nothing reaches Tally until you post a row
 * (or tick rows and post them), and every post asks first.
 */
export function BankStatement({ id, onChanged }: { id: number; onChanged: () => void }) {
  const [st, setSt] = useState<Statement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<number | null>(null);
  const [ticked, setTicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<{ tone: "good" | "warn" | "bad"; text: string } | null>(null);
  const [types, setTypes] = useState<string[]>(["Payment", "Receipt", "Contra", "Journal"]);

  useEffect(() => { bank.get(id).then(setSt).catch((e) => setError(e.message)); }, [id]);
  useEffect(() => {
    if (!st?.company_id) return;
    api.voucherTypes(st.company_id).then((r) => setTypes((t) => [...new Set([...t, ...r.types
      .filter((v: VoucherTypeOption) => ["Payment", "Receipt", "Contra", "Journal"].includes(v.parent))
      .map((v) => v.name)])])).catch(() => {});
  }, [st?.company_id]);

  const apply = (s: Statement) => { setSt(s); onChanged(); };
  const rows = useMemo(() => (st?.rows ?? []).filter((r) =>
    filter === "all" ? true : filter === "check" ? r.check === "bad" || r.check === "fixed" : r.status === filter),
  [st, filter]);

  if (error) return <Banner tone="bad">{error}</Banner>;
  if (!st) return <Spinner label="Opening the statement" />;

  const tickable = rows.filter(ready);
  const chosen = (st.rows ?? []).filter((r) => ticked.has(r.id) && ready(r));
  const out = chosen.reduce((s, r) => s + Math.max(0, -paise(r.amount)), 0);
  const inn = chosen.reduce((s, r) => s + Math.max(0, paise(r.amount)), 0);

  async function run(what: () => Promise<void>) {
    setBusy(true); setBanner(null);
    try { await what(); } catch (e) { setBanner({ tone: "bad", text: (e as Error).message }); } finally { setBusy(false); }
  }

  const postTicked = () => run(async () => {
    if (!chosen.length || !st) return;
    if (!window.confirm(`Post ${chosen.length} row${chosen.length === 1 ? "" : "s"} to ${st.company}, one voucher each?\n\n`
      + `Money out ${money(rupees(out))}, money in ${money(rupees(inn))}.`)) return;
    const r = await bank.post(st.id, chosen.map((x) => x.id));
    apply(r.statement);
    setTicked(new Set());
    const failed = r.results.filter((x) => !x.ok);
    setBanner({ tone: failed.length ? "warn" : "good",
      text: `Posted ${r.posted} of ${r.results.length}.` + (failed.length ? " " + failed.map((x) => x.message).join(" ") : "") });
  });

  const lookInTally = () => run(async () => {
    const s = await bank.check(st.id);
    apply(s);
    setBanner({ tone: "good", text: `Looked in ${s.company}: ${s.by_status.in_tally} of ${s.count} rows are there already.` });
  });

  return (
    <div className="space-y-3">
      <div className="panel">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 pt-4">
          <h2 className="heading min-w-0 truncate">
            {st.bank ?? "Bank statement"}
            {st.account_no && <span className="mono ml-2 text-[14px] font-normal tracking-normal muted">A/c {st.account_no}</span>}
          </h2>
          <span className="truncate text-[12px] faint">{st.filename}</span>
        </div>
        {st.period_from && <p className="px-5 text-[12.5px] muted">{shortDate(st.period_from)} – {shortDate(st.period_to)}</p>}
        <div className="flex flex-wrap items-start gap-x-10 gap-y-3 px-5 pb-4 pt-4">
          <Account st={st} onSet={(s) => apply(s)} />
          <Figure label="Money out (Dr)" value={money(st.debits)} />
          <Figure label="Money in (Cr)" value={money(st.credits)} />
          <Figure label="Closing balance" value={money(st.closing)} />
          <div className="ml-auto flex flex-col items-end gap-1">
            <button className="btn" disabled={busy || !st.bank_ledger_id} onClick={lookInTally}
              title="Find the rows posted already, by you or by hand, and learn from them">
              Look in Tally
            </button>
            <span className="text-[11.5px] faint">
              {st.tally_error ? <span className="text-[var(--warn)]" title={st.tally_error}>Tally could not be reached</span>
                : st.tally_checked_at ? `Looked ${new Date(st.tally_checked_at).toLocaleString("en-IN")}` : "Not looked yet"}
            </span>
          </div>
        </div>
        {st.unsure > 0 && (
          <div className="px-5 pb-4">
            <Banner tone="warn">
              {st.unsure} row{st.unsure === 1 ? "" : "s"} could not be squared with the running balance. Check them on the
              statement (Check the reading) before posting.
            </Banner>
          </div>
        )}
      </div>

      {banner && <Banner tone={banner.tone} onClose={() => setBanner(null)}>{banner.text}</Banner>}

      <div className="panel overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b hairline px-3 py-2.5">
          <div className="tabs scroll-x max-w-full">
            {FILTERS.map((f) => {
              const n = f.key === "all" ? st.count : f.key === "check"
                ? (st.rows ?? []).filter((r) => r.check === "bad" || r.check === "fixed").length : st.by_status[f.key];
              return (
                <button key={f.key} onClick={() => setFilter(f.key)} aria-pressed={filter === f.key} className="tab text-[12.5px]">
                  {f.label}<span className="mono ml-1.5 faint">{n}</span>
                </button>
              );
            })}
          </div>
          <span className="ml-auto flex items-center gap-2">
            {chosen.length > 0 && <span className="text-[12px] muted">{chosen.length} ticked</span>}
            <button className="btn btn-primary" disabled={busy || !chosen.length} onClick={postTicked}>
              Post ticked to Tally
            </button>
          </span>
        </div>
        <div className="overflow-auto">
          <table className="w-full border-collapse text-[12.5px]">
            <thead className="sticky top-0 border-b hairline bg-[var(--panel)] text-left text-[12px] font-medium muted">
              <tr>
                <th className="w-8 px-2 py-2">
                  <input type="checkbox" aria-label="Tick every row shown that is ready to post"
                    title="Tick every row shown whose ledgers are set"
                    checked={tickable.length > 0 && tickable.every((r) => ticked.has(r.id))}
                    onChange={(e) => setTicked((t) => {
                      const n = new Set(t);
                      tickable.forEach((r) => (e.target.checked ? n.add(r.id) : n.delete(r.id)));
                      return n;
                    })} />
                </th>
                <th className="px-2 py-2">#</th>
                <th className="px-2 py-2">Date</th>
                <th className="px-2 py-2">Value date</th>
                <th className="px-2 py-2">Transaction details</th>
                <th className="px-2 py-2">Chq / Ref no.</th>
                <th className="px-2 py-2 text-right">Debit</th>
                <th className="px-2 py-2 text-right">Credit</th>
                <th className="px-2 py-2 text-right">Balance</th>
                <th className="px-2 py-2">Goes to in Tally</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.id}>
                  <tr className={`row-hover cursor-pointer border-t hairline align-top ${open === r.id ? "bg-[var(--hover)]" : ""}`}
                    onClick={() => setOpen(open === r.id ? null : r.id)}>
                    <td className="px-2 py-1.5" onClick={(e) => e.stopPropagation()}>
                      {ready(r) && (
                        <input type="checkbox" aria-label={`Tick row ${r.row_no}`} checked={ticked.has(r.id)}
                          onChange={(e) => setTicked((t) => {
                            const n = new Set(t);
                            if (e.target.checked) n.add(r.id); else n.delete(r.id);
                            return n;
                          })} />
                      )}
                    </td>
                    <td className="mono px-2 py-1.5 faint">{r.serial ?? r.row_no}</td>
                    <td className="whitespace-nowrap px-2 py-1.5">{shortDate(r.date)}</td>
                    <td className="whitespace-nowrap px-2 py-1.5 muted">{shortDate(r.value_date)}</td>
                    <td className="max-w-[360px] px-2 py-1.5">
                      <span className="mono break-all text-[12px]">{r.details}</span>
                      {r.note && <span className="ml-1.5 italic muted" title="Written by hand on the statement">{r.note}</span>}
                    </td>
                    <td className="mono max-w-[150px] break-all px-2 py-1.5 text-[12px] muted">{r.ref}</td>
                    {/* Debit and credit apart, as the bank prints them: the column says which. */}
                    <td className="mono whitespace-nowrap px-2 py-1.5 text-right">
                      {r.amount && Number(r.amount) < 0 ? money(-Number(r.amount)) : ""}
                    </td>
                    <td className="mono whitespace-nowrap px-2 py-1.5 text-right">
                      {r.amount && Number(r.amount) > 0 ? money(r.amount) : ""}
                    </td>
                    <td className="mono whitespace-nowrap px-2 py-1.5 text-right">
                      {money(r.balance)} <Squared row={r} />
                    </td>
                    <td className="px-2 py-1.5"><GoesTo row={r} /></td>
                  </tr>
                  {open === r.id && (
                    <tr className="bg-[var(--hover)]">
                      <td colSpan={10} className="px-3 pb-3">
                        <RowEditor st={st} row={r} types={types} busy={busy} run={run} apply={apply} setBanner={setBanner} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
              {!rows.length && (
                <tr><td colSpan={10} className="px-4 py-8 text-center muted">No rows here.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="label">{label}</span>
      <span className="mono text-[17px] font-semibold text-[var(--ink-strong)]">{value}</span>
    </div>
  );
}

/** Whether the running balance agrees with the row, as the statement reads:
 *  a check, amber when the balances had to set it right; an alert when not. */
function Squared({ row }: { row: BankRow }) {
  if (row.check === "ok" || row.check === "first")
    return <Icon name="check" className="text-[var(--good)]" title="The running balance agrees" />;
  if (row.check === "fixed")
    return <Icon name="check" className="text-[var(--warn)]" title={row.read_notes.join(" ")} />;
  return <Icon name="alert" className="text-[var(--bad)]" title={row.read_notes.join(" ")} />;
}

function GoesTo({ row }: { row: BankRow }) {
  if (row.status === "in_tally" && row.in_tally)
    return (
      <span className="inline-flex flex-wrap items-center gap-x-1.5" title={row.in_tally.ledgers.map(([n, a]) => `${n} ${a}`).join(", ")}>
        <span className="chip chip-good">In Tally</span>
        <span className="mono text-[11.5px] faint">{row.in_tally.type} {row.in_tally.number}{row.in_tally.part ? " (part)" : ""}</span>
      </span>
    );
  if (row.status === "posted") return <span className="chip chip-good">Posted</span>;
  if (row.status === "skipped") return <span className="chip">Left out</span>;
  if (!row.ledgers.length) return <span className="chip chip-warn">Pick the ledger</span>;
  const sure = row.confidence >= 0.9;
  return (
    <span className="inline-flex items-center gap-1.5" title={row.reason ?? ""}>
      <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${sure ? "bg-[var(--good)]" : "bg-[var(--warn)]"}`} />
      <span className={sure ? "" : "text-[var(--warn)]"}>{row.ledgers.map((l) => l.name).join(" + ")}</span>
    </span>
  );
}

/** Whose account the statement is, when its number is in no bank ledger's name. */
function Account({ st, onSet }: { st: Statement; onSet: (s: Statement) => void }) {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.companies().then(setCompanies).catch(() => {}); }, []);
  const set = (body: { company_id?: number; bank_ledger_id?: number }) =>
    bank.setAccount(st.id, body).then(onSet).catch((e) => setErr(e.message));
  return (
    <div className="min-w-[280px]">
      <span className="label">Posts to</span>
      {st.bank_ledger ? (
        <span className="font-semibold">{st.company} · {st.bank_ledger}</span>
      ) : (
        <div className="flex flex-col gap-1.5">
          <span className="text-[12px] text-[var(--warn)]">
            {st.account_no ? `No bank ledger has ${st.account_no} in its name: pick it.` : "Pick the company and the bank's ledger."}
          </span>
          <select className="field" value={st.company_id ?? ""} onChange={(e) => set({ company_id: Number(e.target.value) })}>
            <option value="" disabled>Company…</option>
            {companies.map((c) => <option key={c.id} value={c.id}>{c.tally_name}</option>)}
          </select>
          {st.company_id && (
            <LedgerPicker companyId={st.company_id} value={null} valueName={null} groups={BANK_GROUPS}
              groupsLabel="bank accounts" placeholder="The bank's ledger…"
              onPick={(ledgerId) => set({ bank_ledger_id: ledgerId })} />
          )}
          {err && <span className="text-[12px] text-[var(--bad)]">{err}</span>}
        </div>
      )}
    </div>
  );
}

/** The row's part of the page, so what was read sits next to what was printed. */
function ScanStrip({ documentId, page, top, bottom }: { documentId: number; page: number; top: number; bottom: number }) {
  const [ratio, setRatio] = useState(1.414); // a page's height to its width, until the image says
  const pad = 0.006;
  const t = Math.max(0, top - pad);
  const h = Math.min(1, bottom + pad) - t;
  if (!page || !(bottom > top)) return null;
  return (
    // Percent margins and padding are of the width: the crop keeps to the image at any size.
    <div className="relative overflow-hidden rounded border border-[var(--edge)] bg-white"
      style={{ paddingBottom: `${h * ratio * 100}%` }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={api.pageUrl(documentId, page)} alt={`Page ${page}, as printed`}
        onLoad={(e) => setRatio(e.currentTarget.naturalHeight / e.currentTarget.naturalWidth)}
        className="absolute left-0 top-0 block w-full max-w-none" style={{ marginTop: `-${t * ratio * 100}%` }} />
    </div>
  );
}

function RowEditor({ st, row, types, busy, run, apply, setBanner }: {
  st: Statement; row: BankRow; types: string[]; busy: boolean;
  run: (what: () => Promise<void>) => Promise<void>;
  apply: (s: Statement) => void;
  setBanner: (b: { tone: "good" | "warn" | "bad"; text: string } | null) => void;
}) {
  const [type, setType] = useState(row.voucher_type ?? (Number(row.amount) > 0 ? "Receipt" : "Payment"));
  const open = row.status === "pending";
  const total = Math.abs(paise(row.amount));
  // Nothing suggested: one ledger to pick, for the whole line.
  const [lines, setLines] = useState<RowLedger[]>(row.ledgers.length || !open ? row.ledgers
    : [{ ledger_id: 0, name: "", amount: rupees(total) }]);
  const [narration, setNarration] = useState(row.narration ?? "");
  const left = total - lines.reduce((s, l) => s + paise(l.amount), 0);
  const picked = (ls: RowLedger[]) => JSON.stringify(ls.filter((l) => l.ledger_id).map((l) => [l.ledger_id, paise(l.amount)]));
  const dirty = type !== row.voucher_type || narration !== (row.narration ?? "") || picked(lines) !== picked(row.ledgers);
  const moneyIn = Number(row.amount) > 0;

  const save = async () => {
    const s = await bank.updateRow(row.id, { voucher_type: type, narration,
      ledgers: lines.filter((l) => l.ledger_id).map((l) => ({ ledger_id: l.ledger_id, amount: rupees(paise(l.amount)) })) });
    apply(s);
    return s;
  };

  const post = () => run(async () => {
    if (left !== 0) { setBanner({ tone: "warn", text: `The ledgers must add up to ${money(rupees(total))}.` }); return; }
    const what = lines.map((l) => `  ${l.name}  ${money(l.amount)}  ${moneyIn ? "Cr" : "Dr"}`).join("\n");
    if (!window.confirm(`${type} of ${money(rupees(total))} on ${shortDate(row.date)}, into ${st.company}:\n\n${what}\n`
      + `  ${st.bank_ledger}  ${money(rupees(total))}  ${moneyIn ? "Dr" : "Cr"}\n\nPost it?`)) return;
    if (dirty) await save();
    const r = await bank.postRow(row.id);
    apply(r.statement);
    setBanner({ tone: r.result.ok ? "good" : "warn", text: r.result.message });
  });

  return (
    <div className="space-y-3 pt-2">
    <div>
      <span className="label">As on the statement (page {row.page})</span>
      <ScanStrip documentId={st.document_id} page={row.page} top={row.top} bottom={row.bottom} />
    </div>
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
      <div className="space-y-2">
        {!row.read_notes.length && !row.note && !row.in_tally && (row.check === "ok" || row.check === "first") && (
          <p className="flex items-center gap-1.5 text-[12px] muted">
            <Icon name="check" className="text-[var(--good)]" /> The running balance agrees with this row as read.
          </p>
        )}
        {(row.read_notes.length > 0 || row.note) && (
          <ul className="space-y-0.5 text-[12px]">
            {row.read_notes.map((n) => <li key={n} className="text-[var(--warn)]">{n}</li>)}
            {row.note && <li className="muted">Written on it by hand: <span className="italic">{row.note}</span></li>}
          </ul>
        )}
        {row.in_tally && (
          <div className="note note-good flex-col !gap-0 text-[12px]">
            <b>In Tally:</b> {row.in_tally.type} {row.in_tally.number} of {shortDate(row.in_tally.date)}
            {row.in_tally.part && " (one line of a voucher that joins several)"}
            <div className="mono mt-1">{row.in_tally.ledgers.map(([n, a]) => <div key={n}>{n} {money(a)}</div>)}</div>
            {row.in_tally.narration && <div className="mt-1 muted">{row.in_tally.narration}</div>}
          </div>
        )}
      </div>

      <div className="space-y-2.5">
        <div className="flex items-end gap-3">
          <label className="w-44">
            <span className="label">Voucher type</span>
            <select className="field" value={type} disabled={!open} onChange={(e) => setType(e.target.value)}>
              {[...new Set([type, ...types])].map((t) => <option key={t}>{t}</option>)}
            </select>
          </label>
          <span className="pb-1.5 text-[12px] muted">
            {money(rupees(total))} {moneyIn ? "came in: the ledgers are Cr, the bank Dr" : "went out: the ledgers are Dr, the bank Cr"}
          </span>
        </div>

        <div>
          <span className="label">Goes to</span>
          <div className="space-y-1.5">
            {lines.map((l, i) => (
              <div key={i} className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <LedgerPicker companyId={st.company_id} value={l.ledger_id || null} valueName={l.name || null}
                    candidates={i === 0 ? row.candidates : undefined} disabled={!open} missing={!l.ledger_id}
                    onCreate={{ create: (b) => bank.createLedger(st.id, b),
                      defaultGroup: moneyIn ? "Sundry Debtors" : "Sundry Creditors" }}
                    onPick={(ledgerId, name) => setLines(lines.map((x, j) => (j === i ? { ...x, ledger_id: ledgerId, name } : x)))} />
                </div>
                <input className="field mono !w-32 text-right" inputMode="decimal" value={l.amount} disabled={!open}
                  aria-label={`Amount to ${l.name || "this ledger"}`}
                  onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} />
                {open && (
                  <button className="btn btn-ghost !px-2 muted" aria-label="Remove this ledger" title="Remove this ledger"
                    onClick={() => setLines(lines.filter((_x, j) => j !== i))}><Icon name="x" /></button>
                )}
              </div>
            ))}
            {open && (
              <button className="btn btn-ghost !px-2 !py-0.5 !text-[12px]"
                onClick={() => setLines([...lines, { ledger_id: 0, name: "", amount: rupees(Math.max(0, left)) }])}>
                <Icon name="plus" /> Add ledger{left > 0 && lines.length > 0 ? ` for the ${money(rupees(left))} left` : ""}
              </button>
            )}
            {left !== 0 && lines.length > 0 && (
              <p className="text-[12px] text-[var(--warn)]">
                {left > 0 ? `${money(rupees(left))} is not given to a ledger yet.` : `${money(rupees(-left))} more than the line.`}
              </p>
            )}
          </div>
          {row.reason && open && (
            <p className="mt-1 text-[11.5px] faint">
              Why: {row.reason}{row.confidence > 0 && row.confidence < 1 ? ` (${Math.round(row.confidence * 100)}% sure)` : ""}
            </p>
          )}
        </div>

        <label className="block">
          <span className="label">Narration</span>
          <textarea className="field mono !text-[12px]" rows={2} value={narration} disabled={!open}
            onChange={(e) => setNarration(e.target.value)} />
        </label>

        <div className="flex flex-wrap items-center gap-2">
          {open && (
            <>
              <button className="btn btn-primary" disabled={busy || !lines.length || left !== 0 || lines.some((l) => !l.ledger_id)}
                onClick={post}>Post to Tally</button>
              <button className="btn" disabled={busy || !dirty} onClick={() => run(async () => { await save(); })}>Save</button>
              <button className="btn btn-ghost" disabled={busy}
                onClick={() => run(async () => apply(await bank.updateRow(row.id, { skip: true })))}>Leave out</button>
            </>
          )}
          {row.status === "skipped" && (
            <button className="btn" disabled={busy}
              onClick={() => run(async () => apply(await bank.updateRow(row.id, { skip: false })))}>Put back</button>
          )}
        </div>
      </div>
    </div>
    </div>
  );
}
