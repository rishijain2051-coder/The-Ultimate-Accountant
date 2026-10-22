"use client";

import {
  api, money, shortDate,
  type EntryDetail, type EntryLine, type FieldMeta, type Preview, type PreviewRow,
} from "@/lib/api";
import { Evidence, Icon, Spinner } from "@/components/ui";
import { LedgerPicker } from "@/components/LedgerPicker";
import { useEffect, useRef, useState } from "react";

export type Draft = Record<string, unknown>;
// matched_name and ledger_name only show a pick until it is saved; delete
// takes the line off when it is.
export type LineEdit = Partial<EntryLine> & { matched_name?: string; ledger_name?: string; delete?: boolean };
// A voucher with no lines read posts as one line; picking its ledger adds that
// line. Rows added to the voucher here are -2, -3... until saved.
export const NEW_LINE = -1;

type Side = "Dr" | "Cr";
const other = (s: Side): Side => (s === "Dr" ? "Cr" : "Dr");
// An amount as typed ("1,354.32", "-5", "(-)0.40", ""): a number, 0 for nothing.
export const amt = (v: unknown) => Number(String(v ?? "").replace(/\(-\)/g, "-").replace(/,/g, "")) || 0;
const neg = (t: string) => (t.startsWith("-") ? t.slice(1) : t && `-${t}`);
const two = (v: unknown) => amt(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// Tally writes a reduction as (-)0.40.
const tally = (v: number) => (v < 0 ? `(-)${two(-v)}` : two(v));
const typed = (t: string) => t.replace(/\(-\)/g, "-").replace(/,/g, "").trim();
const plain = (v: string | null | undefined) => (v === null || v === undefined || v === "" ? "" : String(Number(v)));
const TAXES = ["cgst", "sgst", "igst", "cess"] as const;
// A tax row's kind, as the "+ Create ... in Tally" form's own duty head.
const DUTY_HEAD_BY_KIND: Record<string, string> = { cgst: "CGST", sgst: "SGST/UTGST", igst: "IGST", cess: "Cess" };
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

/** A row of the voucher as it stands with the unsaved edits: value is its
 *  amount signed to its natural side (negative: on the other side). */
type VRow = PreviewRow & { key: string; value: number; gone: boolean; newId?: number; added?: boolean };

export function TallyVoucher({
  e, preview, meta, draft, lineEdits, val, set, unset, setLine, dropLine, keepLine,
  inTally, dirty, checking, registration, onSave, onOptional, onCreateParty,
}: {
  e: EntryDetail; preview: Preview | null; meta: Record<string, FieldMeta>;
  draft: Draft; lineEdits: Record<number, LineEdit>;
  val: (k: string) => string | null; set: (k: string, v: unknown) => void; unset: (k: string) => void;
  setLine: (lid: number, patch: LineEdit) => void; dropLine: (lid: number) => void; keepLine: (lid: number) => void;
  inTally: boolean; dirty: boolean;
  /** The AI is still reading the bill in the background. */
  checking: boolean;
  registration?: string | null;
  onSave: () => void; onOptional: () => void; onCreateParty: () => void;
}) {
  const shape = (meta._voucher?.value as { shape?: string } | undefined)?.shape ?? "invoice";
  const [asVoucher, setAsVoucher] = useState(false);
  const [more, setMore] = useState(false);
  // An amount being typed, kept as typed until the field is left.
  const [typing, setTyping] = useState<{ key: string; side?: Side; text: string } | null>(null);
  const [balance, setBalance] = useState<{ id: number; ledger: number; balance: string | null; side?: string } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const dateRef = useRef<HTMLInputElement>(null);

  // Tally shows the party's balance under it.
  useEffect(() => {
    const ledger = e.party_ledger_id;
    if (!ledger) return;
    let live = true;
    api.balance(e.id).then((b) => { if (live) setBalance({ id: e.id, ledger, ...b }); }).catch(() => {});
    return () => { live = false; };
  }, [e.id, e.party_ledger_id]);
  const bal = balance && balance.id === e.id && balance.ledger === e.party_ledger_id && balance.balance !== null
    ? balance : null;

  const aiRead = Array.isArray(meta._llm_filled?.value) ? (meta._llm_filled.value as string[]) : [];
  const aiLines = aiRead.includes("lines") || aiRead.includes("charges");
  const llm = (k: string) => (meta[k]?.source === "llm" ? "field-llm" : "");

  const salesSide = ["Sales", "Credit Note", "Receipt", "Sales Order", "Delivery Note"].includes(preview?.behaves_as ?? "");
  const partyGroup = salesSide ? "Debtors" : "Creditors";
  // Where the non-party lines normally live; "search every ledger" widens it.
  const lineGroups = shape === "invoice"
    ? (salesSide ? ["Sales Accounts", "Direct Incomes", "Indirect Incomes"]
                 : ["Purchase Accounts", "Direct Expenses", "Indirect Expenses", "Fixed Assets"])
    : ["Bank Accounts", "Cash-in-Hand", "Bank OD", "Bank OCC"];
  const lineGroupsLabel = shape === "invoice" ? (salesSide ? "income ledgers" : "expense and purchase ledgers")
                                              : "bank and cash ledgers";
  const partyLabel = salesSide ? "customers (Sundry Debtors)" : "suppliers (Sundry Creditors)";

  // Each row of the voucher can go to another ledger: which ledgers to
  // offer it, what it shows until saved, and where the pick is kept.
  const taxPicks = (draft.tax_ledgers ?? {}) as Record<string, number>;
  const taxNames = (draft.tax_ledger_names ?? {}) as Record<string, string>;
  const rowKey = (r: PreviewRow) => (r.role === "tax" ? r.kind ?? "" : "round");
  const rowScope = (r: PreviewRow): [string[], string] =>
    r.role === "party" ? [[partyGroup], partyLabel]
      : r.role === "line" ? [lineGroups, lineGroupsLabel]
        : r.role === "tax" ? [["Duties & Taxes"], "tax ledgers (Duties & Taxes)"]
          : [["Indirect Expenses", "Indirect Incomes"], "indirect expenses and incomes"];
  const rowPicked = (r: VRow): string | undefined => {
    if (r.role === "party") return draft.party_ledger_name as string | undefined;
    if (r.role !== "line") return taxNames[rowKey(r)];
    const ed = lineEdits[r.newId ?? r.line_ids?.[0] ?? NEW_LINE];
    return ed?.ledger_name ?? (ed?.stock_item_id === null ? ed.matched_name : undefined);
  };
  const pickRow = (r: VRow, lid: number, name: string, kind: "ledger" | "item" = "ledger") => {
    if (r.role === "party") { set("party_ledger_id", lid); set("party_ledger_name", name); return; }
    if (r.role !== "line") {
      set("tax_ledgers", { ...taxPicks, [rowKey(r)]: lid });
      set("tax_ledger_names", { ...taxNames, [rowKey(r)]: name });
      return;
    }
    if (r.newId !== undefined) {
      setLine(r.newId, kind === "item" ? { stock_item_id: lid, matched_name: name, ledger_name: name }
                                       : { ledger_id: lid, ledger_name: name });
      return;
    }
    if (!r.line_ids?.length) { setLine(NEW_LINE, { ledger_id: lid, ledger_name: name, amount: r.amount }); return; }
    for (const id of r.line_ids) {
      if (kind === "item") { setLine(id, { stock_item_id: lid, matched_name: name, ledger_name: name }); continue; }
      // On an item's row it is the ledger behind the item: the item stays.
      const item = e.lines.find((l) => l.id === id)?.stock_item_id;
      setLine(id, item ? { ledger_id: lid, ledger_name: name } : { ledger_id: lid, matched_name: name, ledger_name: name });
    }
  };

  // ---- the voucher as it stands, with what is typed but not saved yet ----
  // Every row's amount lives in a field of the bill or a line of it, signed
  // to the row's natural side: typing here writes there, and a row moved to
  // the other side saves as a negative amount.
  const lineSide: Side = preview?.line_side ?? (salesSide ? "Cr" : "Dr");
  const lineOf = (lid: number) => e.lines.find((l) => l.id === lid);
  const lineAmount = (lid: number) =>
    lineEdits[lid]?.delete ? 0 : amt(lineEdits[lid]?.amount ?? lineOf(lid)?.amount);
  const signedOf = (r: PreviewRow) => (r.side === r.natural ? 1 : -1) * amt(r.amount);
  const valueOf = (r: PreviewRow): number => {
    if (r.role === "tax") return r.kind! in draft ? amt(draft[r.kind!]) : signedOf(r);
    if (r.role === "round") return "round_off" in draft ? amt(draft.round_off) : signedOf(r);
    const ids = r.line_ids ?? [];
    if (r.role !== "line") return signedOf(r);
    if (!ids.length) return lineEdits[NEW_LINE]?.amount !== undefined ? amt(lineEdits[NEW_LINE].amount) : signedOf(r);
    return ids.some((lid) => lid in lineEdits) ? ids.reduce((s, lid) => s + lineAmount(lid), 0) : signedOf(r);
  };
  const vrows: VRow[] = (preview?.rows ?? []).map((r) => ({
    ...r,
    key: r.role === "tax" ? `tax:${r.kind}` : r.role === "line" ? `line:${(r.line_ids ?? []).join(",")}` : r.role,
    value: valueOf(r),
    gone: r.role === "tax" ? draft[r.kind!] === null : r.role === "round" ? draft.round_off === null
      : !!r.line_ids?.length && r.line_ids.every((lid) => lineEdits[lid]?.delete),
  }));
  const blank = (x: Partial<VRow>): VRow => ({
    role: "line", key: "", natural: lineSide, side: lineSide, ledger: "", ledger_id: null, amount: "0",
    is_party: false, missing: false, value: 0, gone: false, added: true, ...x,
  });
  // Rows added here sit after the last line, where they will be once saved;
  // a tax or the round-off added here, where Tally puts them.
  const added = Object.entries(lineEdits).filter(([k]) => Number(k) < NEW_LINE).map(([k, p]) => blank({
    key: `new:${k}`, newId: Number(k), ledger: p.ledger_name ?? "", ledger_id: p.ledger_id ?? null,
    missing: !p.ledger_id && !p.stock_item_id, value: amt(p.amount),
  }));
  vrows.splice(vrows.map((r) => r.role).lastIndexOf("line") + 1, 0, ...added);
  for (const kind of TAXES) {
    if (typeof draft[kind] === "string" && !vrows.some((r) => r.role === "tax" && r.kind === kind)) {
      const at = vrows.findIndex((r) => r.role === "round");
      vrows.splice(at < 0 ? vrows.length : at, 0, blank({
        role: "tax", kind, key: `tax:${kind}`, ledger: taxNames[kind] ?? kind.toUpperCase(), value: amt(draft[kind]),
      }));
    }
  }
  if (typeof draft.round_off === "string" && !vrows.some((r) => r.role === "round")) {
    vrows.push(blank({ role: "round", key: "round", ledger: taxNames.round ?? "Round off", value: amt(draft.round_off) }));
  }
  // The party's row is the balance of the others, as Tally totals an invoice.
  const party = vrows.find((r) => r.role === "party");
  if (party) {
    const rest = vrows.filter((r) => r !== party && !r.gone)
      .reduce((s, r) => s + (r.natural === "Dr" ? r.value : -r.value), 0);
    party.value = party.natural === "Dr" ? -rest : rest;
  }
  // A row taken off keeps its old side and amount, struck through.
  for (const r of vrows) if (!r.gone) r.side = r.value < 0 ? other(r.natural) : r.natural;
  const total = (s: Side) => vrows.filter((r) => !r.gone && r.side === s).reduce((t, r) => t + Math.abs(r.value), 0);
  const [totalDr, totalCr] = [total("Dr"), total("Cr")];
  const balanced = Math.abs(totalDr - totalCr) < 0.005;
  const billTotal = [null, undefined, ""].includes(val("grand_total")) ? null : amt(val("grand_total"));
  const voucherTotal = party ? Math.abs(party.value) : totalDr;

  const writeRow = (r: VRow, v: string) => {
    // A tax or round-off typed empty is none: it is not posted.
    if (r.role === "tax") return set(r.kind!, v === "" ? "0" : v);
    if (r.role === "round") return set("round_off", v === "" ? "0" : v);
    if (r.newId !== undefined) return setLine(r.newId, { amount: v });
    const ids = r.line_ids ?? [];
    if (!ids.length) return setLine(NEW_LINE, { amount: v, ...(r.ledger_id ? { ledger_id: r.ledger_id, ledger_name: r.ledger } : {}) });
    if (ids.length === 1) return setLine(ids[0], { amount: v });
    // Several lines behind one row: the change goes on the last of them.
    const others = ids.slice(0, -1).reduce((s, lid) => s + lineAmount(lid), 0);
    setLine(ids[ids.length - 1], { amount: (amt(v) - others).toFixed(2) });
  };
  // Typed into the Debit or the Credit column (As Voucher): that is the row's side now.
  const typeInto = (r: VRow, side: Side, text: string) => {
    setTyping({ key: r.key, side, text });
    const t = typed(text);
    writeRow(r, side === r.natural ? t : neg(t));
  };
  const shownDrCr = (r: VRow, side: Side) =>
    typing?.key === r.key ? (typing.side === side ? typing.text : "") : r.side === side && r.value ? two(Math.abs(r.value)) : "";
  // Typed into an invoice's Amount column: (-) or a minus is a reduction.
  const typeAmount = (r: VRow, text: string) => { setTyping({ key: r.key, text }); writeRow(r, typed(text)); };
  const shownAmount = (r: VRow) => (typing?.key === r.key ? typing.text : r.value ? tally(r.value) : "");
  const flipRow = (r: VRow) => {
    const ids = r.newId === undefined && r.role === "line" ? r.line_ids ?? [] : [];
    if (ids.length > 1) { for (const lid of ids) setLine(lid, { amount: (-lineAmount(lid)).toFixed(2) }); return; }
    writeRow(r, (-r.value).toFixed(2));
  };
  const removable = (r: VRow) => r.role !== "party" && (r.role !== "line" || r.newId !== undefined || !!r.line_ids?.length);
  const removeRow = (r: VRow) => {
    if (r.newId !== undefined) return dropLine(r.newId);
    if (r.role === "tax") return r.added ? unset(r.kind!) : set(r.kind!, null);
    if (r.role === "round") return r.added ? unset("round_off") : set("round_off", null);
    for (const lid of r.line_ids ?? []) setLine(lid, { delete: true });
  };
  const keepRow = (r: VRow) => {
    if (r.role === "tax") return unset(r.kind!);
    if (r.role === "round") return unset("round_off");
    for (const lid of r.line_ids ?? []) keepLine(lid);
  };
  const addRow = () => {
    const next = Math.min(NEW_LINE, ...Object.keys(lineEdits).map(Number)) - 1;
    // A voucher with no lines read posts as one line: keep that one too.
    const only = e.lines.length ? undefined : vrows.find((r) => r.role === "line" && !r.line_ids?.length && r.newId === undefined);
    if (only && !(NEW_LINE in lineEdits)) writeRow(only, only.value.toFixed(2));
    setLine(next, { amount: "" });
  };

  // ---- items, as Tally keys them: quantity x rate = amount ----
  const items = vrows.filter((r) => r.role === "line" && r.stock_item && r.line_ids?.length === 1);
  const itemMode = shape === "invoice" && items.length > 0;
  const voucherView = shape !== "invoice" || asVoucher;
  const modeName = voucherView ? "As Voucher" : itemMode ? "Item Invoice" : "Accounting Invoice";
  const ledgerRows = vrows.filter((r) => r.role !== "party" && !items.includes(r));
  const lineVal = (lid: number, k: "quantity" | "rate" | "amount") => lineEdits[lid]?.[k] ?? lineOf(lid)?.[k] ?? null;
  const setQty = (lid: number, t: string) => {
    const r = amt(lineVal(lid, "rate"));
    setLine(lid, { quantity: t, ...(r && t !== "" ? { amount: (amt(t) * r).toFixed(2) } : {}) });
  };
  const setRate = (lid: number, t: string) => {
    const q = amt(lineVal(lid, "quantity"));
    setLine(lid, { rate: t, ...(q && t !== "" ? { amount: (q * amt(t)).toFixed(2) } : {}) });
  };
  const setAmount = (lid: number, t: string) => {
    const q = amt(lineVal(lid, "quantity"));
    setLine(lid, { amount: t, ...(q && t !== "" ? { rate: (amt(t) / q).toFixed(2) } : {}) });
  };
  const units = new Set(items.filter((r) => !r.gone).map((r) => r.unit ?? lineOf(r.line_ids![0])?.uom ?? ""));
  const qtyTotal = units.size === 1
    ? items.filter((r) => !r.gone).reduce((s, r) => s + amt(lineVal(r.line_ids![0], "quantity")), 0) : null;

  // ---- a tax or the round-off added as Tally works it out ----
  const goods = vrows.filter((r) => r.role === "line" && !r.gone).reduce((s, r) => s + r.value, 0);
  const itemRates = new Set(items.filter((r) => !r.gone).map((r) => lineOf(r.line_ids![0])?.gst_rate ?? null));
  const billRate = itemRates.size === 1 ? [...itemRates][0] : null;
  const has = (kind: string) => vrows.some((r) => (r.role === "tax" ? r.kind : r.role) === kind && !r.gone);
  const addTaxes = (kinds: readonly string[], part: number) => {
    for (const k of kinds) set(k, billRate ? ((goods * billRate * part) / 100).toFixed(2) : "0");
  };
  const addRound = () => {
    const sum = voucherTotal - (vrows.find((r) => r.role === "round" && !r.gone)?.value ?? 0);
    set("round_off", (Math.round(sum) - sum).toFixed(2));
  };

  // ---- keys, as in Tally ----
  useEffect(() => {
    const keys = (ev: KeyboardEvent) => {
      if (ev.key === "F2") { ev.preventDefault(); dateRef.current?.focus(); return; }
      if (!(ev.ctrlKey || ev.metaKey) || ev.shiftKey || ev.altKey) return;
      const k = ev.key.toLowerCase();
      if (k === "h" && shape === "invoice") { ev.preventDefault(); setAsVoucher((v) => !v); }
      else if (k === "i") { ev.preventDefault(); setMore((m) => !m); }
      else if (k === "l" && !inTally) { ev.preventDefault(); onOptional(); }
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, [shape, inTally, onOptional]);
  // Enter takes you to the next field, and past the last one accepts;
  // Ctrl+A accepts. Shift+Enter is a new line in the narration.
  const onKey = (ev: React.KeyboardEvent<HTMLDivElement>) => {
    const t = ev.target as HTMLElement;
    if (t.closest("[role=listbox]")) return; // a ledger list's own search box
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "a" && !ev.shiftKey && !ev.altKey) {
      ev.preventDefault();
      if (dirty) onSave();
      return;
    }
    if (ev.key !== "Enter" || ev.shiftKey || ev.ctrlKey || ev.altKey || ev.metaKey) return;
    if (t.tagName === "BUTTON" && t.dataset.picker !== "set") return; // an empty field opens its list
    const all = Array.from(box.current?.querySelectorAll<HTMLElement>(
      "input:not([disabled]):not([type=checkbox]), textarea:not([disabled]), button[data-picker]:not([disabled])") ?? [])
      .filter((x) => !x.closest("[role=listbox]"));
    const i = all.indexOf(t);
    if (i < 0) return;
    ev.preventDefault();
    if (i === all.length - 1) { if (dirty) onSave(); return; }
    all[i + 1].focus();
    if (all[i + 1] instanceof HTMLInputElement) (all[i + 1] as HTMLInputElement).select();
  };

  const date = val("invoice_date");
  const day = date ? DAYS[new Date(`${date}T00:00:00`).getDay()] : "";
  const cross = (r: VRow) => !inTally && removable(r) && (
    <button type="button" className="btn btn-ghost !px-1.5 !py-1 muted" title={r.gone ? "Keep this row" : "Take this row off"}
      aria-label={r.gone ? "Keep this row" : "Take this row off"}
      onClick={() => (r.gone ? keepRow(r) : removeRow(r))}><Icon name={r.gone ? "undo" : "x"} /></button>
  );
  const picker = (r: VRow, withItems = false) => (inTally || r.gone ? (
    <span className={`px-1.5 ${r.is_party ? "font-bold" : ""}`}>{r.ledger}</span>
  ) : (
    <>
      <LedgerPicker companyId={e.company_id} value={r.ledger_id}
        valueName={rowPicked(r) ?? (r.missing ? null : r.ledger || null)}
        groups={rowScope(r)[0]} groupsLabel={rowScope(r)[1]} items={withItems}
        placeholder={withItems ? "Search items and ledgers…" : undefined}
        candidates={r.role === "party" ? e.party_candidates
          : r.line_ids?.length === 1 ? lineOf(r.line_ids[0])?.candidates : undefined}
        missing={r.missing} onPick={(lid, name, kind) => pickRow(r, lid, name, kind)}
        onCreate={{
          entryId: e.id,
          lineId: r.role === "line" ? r.line_ids?.[0] : undefined,
          // A charge or unmatched line defaults to Indirect Expenses, not the
          // item groups' own first pick (Purchase/Sales Accounts).
          defaultGroup: r.role === "line" ? "Indirect Expenses" : undefined,
          dutyHead: r.role === "tax" ? DUTY_HEAD_BY_KIND[r.kind!] : undefined,
        }} />
      {r.missing && !rowPicked(r) && r.ledger && (
        <span className="block px-1.5 text-[11.5px] font-semibold text-[var(--bad)]">{r.ledger.replace(/[⟨⟩]/g, "")}</span>
      )}
    </>
  ));

  return (
    <div ref={box} className="tv panel" onKeyDown={onKey}>
      <div className="tv-grid">
        <div className="tv-main">
          {/* ---- header: type, number, date, party ---- */}
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <span className="tv-type">{e.voucher_type}</span>
              <span className="text-[12px] font-semibold"><Evidence meta={meta.invoice_no}>No.</Evidence></span>
              <input className={`field mono w-56 font-bold ${llm("invoice_no")}`} aria-label="Voucher number"
                value={val("invoice_no") ?? ""} disabled={inTally} onChange={(x) => set("invoice_no", x.target.value)} />
            </div>
            <div className="text-right">
              <input ref={dateRef} type="date" aria-label="Date" className={`field mono w-40 font-bold ${llm("invoice_date")}`}
                value={date ?? ""} disabled={inTally} onChange={(x) => set("invoice_date", x.target.value)} />
              <div className="px-1.5 text-[12px] muted"><Evidence meta={meta.invoice_date}>{day || "Date"}</Evidence></div>
            </div>
          </div>
          {shape === "invoice" && (
            <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[12px]">
              <span className="font-semibold">Supplier Invoice No.:</span>
              <input className="field mono !w-48" aria-label="Supplier Invoice No." value={val("reference") ?? ""}
                placeholder="blank, as your books keep it" disabled={inTally}
                onChange={(x) => set("reference", x.target.value)} />
              {val("reference") && <span className="muted">Date: {shortDate(date)}</span>}
            </div>
          )}
          {/* As a voucher, the party is one of the rows below. */}
          {!voucherView && (
            <div className="mt-2 flex items-center gap-2">
              <span className="w-32 shrink-0 text-[12.5px] font-semibold">
                <Evidence meta={meta.party_name}>Party A/c name</Evidence>
              </span>
              <div className="min-w-0 flex-1">
                {party ? picker(party) : (
                  <LedgerPicker companyId={e.company_id} value={e.party_ledger_id}
                    valueName={(draft.party_ledger_name as string) ?? e.party_ledger_name}
                    candidates={e.party_candidates} groups={[partyGroup]} groupsLabel={partyLabel} disabled={inTally}
                    missing={!e.party_ledger_id} onPick={(lid, name) => { set("party_ledger_id", lid); set("party_ledger_name", name); }}
                    onCreate={{ entryId: e.id }} />
                )}
              </div>
            </div>
          )}
          <div className={`tv-sub mt-0.5 ${voucherView ? "" : "pl-[8.5rem]"}`}>
            {!voucherView && bal && <span>Current balance: <b className="mono text-[var(--ink)]">{two(bal.balance)} {bal.side}</b></span>}
            {!voucherView && e.party_match_reason && <span className="faint">{e.party_match_reason}</span>}
            {!e.party_ledger_id && e.party_name_raw && !inTally && (
              <button className="btn !py-0 !text-[11.5px]" onClick={onCreateParty}><Icon name="plus" /> Create this ledger in Tally</button>
            )}
          </div>
          <div className={`tv-sub mt-0.5 ${voucherView ? "" : "pl-[8.5rem]"}`}>
            <span>Status: <b className="text-[var(--ink)]">{e.status === "drafted" ? "Optional" : "Regular"}</b></span>
            <span>Mode: <b className="text-[var(--ink)]">{modeName}</b></span>
          </div>

          {more && (
            <div className="tv-box mt-2">
              <b className="block text-[12px]">More Details: GST</b>
              <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12px]">
                <label className="flex items-center gap-2"><span className="w-28 shrink-0 muted"><Evidence meta={meta.place_of_supply}>Place of supply</Evidence></span>
                  <input className={`field mono !w-16 ${llm("place_of_supply")}`} placeholder="08" value={val("place_of_supply") ?? ""}
                    disabled={inTally} onChange={(x) => set("place_of_supply", x.target.value)} />
                  <span className="truncate muted">{e.place_of_supply_name}</span>
                </label>
                <label className="flex items-center gap-2"><span className="w-28 shrink-0 muted"><Evidence meta={meta.due_date}>Due date</Evidence></span>
                  <input type="date" className={`field mono ${llm("due_date")}`} value={val("due_date") ?? ""} disabled={inTally}
                    onChange={(x) => set("due_date", x.target.value)} />
                </label>
                <span><span className="muted">Your GSTIN:</span> <span className="mono">{e.own_gstin ?? "—"}</span></span>
                <span><span className="muted">GST registration:</span> {registration ?? "—"}</span>
              </div>
            </div>
          )}

          {/* ---- the body ---- */}
          {!preview ? <p className="py-6"><Spinner /></p> : preview.error && !added.length ? (
            <p className="tv-box mt-3 text-[12.5px]">
              {checking ? <>The AI is still reading this bill. Its amounts show here when it is done.</>
                : <><span className="text-[var(--bad)]">{preview.error}</span> Type the bill&apos;s total under <i>On the bill</i>, or add a row, and save.</>}
            </p>
          ) : voucherView ? (
            <table className="tv-tbl">
              <thead><tr>
                <th className="w-12">Dr/Cr</th><th>Particulars</th>
                <th className="w-32 r">Debit</th><th className="w-32 r">Credit</th>{!inTally && <th className="w-8" />}
              </tr></thead>
              <tbody>
                {vrows.map((r) => (
                  <tr key={r.key} className={r.gone ? "tv-gone" : r.missing && !rowPicked(r) ? "bg-[var(--bad-soft)]" : ""}>
                    <td>
                      {inTally || r.gone || r.role === "party" || r.stock_item ? (
                        <span className="px-1.5 font-semibold text-[var(--ink-strong)]">{r.side}</span>
                      ) : (
                        <button type="button" className="px-1.5 font-semibold text-[var(--ink-strong)] underline decoration-dotted underline-offset-2"
                          title={`Move this row to the ${r.side === "Dr" ? "credit" : "debit"} side`}
                          onClick={() => flipRow(r)}>{r.side}</button>
                      )}
                    </td>
                    <td>
                      {picker(r, r.role === "line" && shape === "invoice")}
                      {r.stock_item && <span className="block px-1.5 text-[11.5px] muted">{r.stock_item} · {r.quantity}</span>}
                    </td>
                    {(["Dr", "Cr"] as const).map((s) => (
                      <td key={s} className="r">
                        {inTally || r.gone || r.role === "party" ? (
                          <span className="mono px-1.5">{r.side === s ? two(r.gone ? r.amount : Math.abs(r.value)) : ""}</span>
                        ) : (
                          <input className="field mono text-right" inputMode="decimal"
                            aria-label={`${s === "Dr" ? "Debit" : "Credit"}: ${r.ledger || "new row"}`}
                            value={shownDrCr(r, s)} onChange={(x) => typeInto(r, s, x.target.value)} onBlur={() => setTyping(null)} />
                        )}
                      </td>
                    ))}
                    {!inTally && <td>{cross(r)}</td>}
                  </tr>
                ))}
                <tr className="tv-sum">
                  <td /><td className="r">Total {balanced ? "" : <span className="text-[var(--bad)]">· {two(Math.abs(totalDr - totalCr))} apart</span>}</td>
                  <td className="r mono">{two(totalDr)}</td><td className="r mono">{two(totalCr)}</td>{!inTally && <td />}
                </tr>
              </tbody>
            </table>
          ) : (
            <table className="tv-tbl">
              <thead><tr>
                <th>{itemMode ? "Name of Item" : "Particulars"}</th>
                {itemMode && <th className="w-24 r">Quantity</th>}
                <th className="w-24 r">Rate</th>
                {itemMode && <th className="w-12">per</th>}
                <th className="w-32 r">Amount</th>
                {!inTally && <th className="w-8" />}
              </tr></thead>
              <tbody>
                {items.map((r) => {
                  const lid = r.line_ids![0], l = lineOf(lid), ed = lineEdits[lid] ?? {};
                  const off = inTally || r.gone, unit = r.unit ?? l?.uom ?? "";
                  const qty = amt(lineVal(lid, "quantity")), rate = amt(lineVal(lid, "rate")), amount = amt(lineVal(lid, "amount"));
                  const bad = qty && rate && Math.abs(qty * rate - amount) > 1;
                  return (
                    <tr key={r.key} className={`tv-item ${r.gone ? "tv-gone" : ""}`}>
                      <td>
                        {off ? <span className="px-1.5 font-bold">{ed.matched_name ?? r.stock_item}</span> : (
                          <LedgerPicker companyId={e.company_id} value={l?.stock_item_id ?? null} items
                            valueName={ed.matched_name ?? r.stock_item ?? null} candidates={l?.candidates}
                            groups={lineGroups} groupsLabel={lineGroupsLabel} placeholder="Search items and ledgers…"
                            onPick={(id, name, kind) => setLine(lid, kind === "item"
                              ? { stock_item_id: id, matched_name: name }
                              : { ledger_id: id, stock_item_id: null, matched_name: name, ledger_name: name })}
                            onCreate={{
                              entryId: e.id, lineId: lid, allowStock: true,
                              uom: l?.uom ?? undefined, hsn: l?.hsn_sac ?? undefined, gstRate: l?.gst_rate ?? undefined,
                            }} />
                        )}
                        <div className="tv-sub">
                          {/* Tally's own description of the item, as you type it; what the bill says beside it. */}
                          <input className="field !w-44 !py-0 text-[11.5px]" placeholder="Description in Tally"
                            aria-label="Description in Tally" title="Shown under the item in Tally, as typed (blank: none)"
                            disabled={off} value={ed.description ?? l?.description ?? ""}
                            onChange={(x) => setLine(lid, { description: x.target.value })} />
                          {l?.description_raw && (
                            <span className={`max-w-56 truncate ${aiLines ? "text-[var(--warn)]" : "faint"}`}
                              title={`As written on the bill: ${l.description_raw}`}>“{l.description_raw}”</span>
                          )}
                          <span className="tv-mini" title="The ledger behind the item">
                            {off ? r.ledger : (
                              <LedgerPicker companyId={e.company_id} value={r.ledger_id}
                                valueName={ed.ledger_name ?? (r.missing ? null : r.ledger)} groups={lineGroups}
                                groupsLabel={lineGroupsLabel} missing={r.missing && !ed.ledger_name}
                                onPick={(id, name) => setLine(lid, { ledger_id: id, ledger_name: name })}
                                onCreate={{ entryId: e.id, lineId: lid }} />
                            )}
                          </span>
                          {l?.gst_rate != null && <span>GST {l.gst_rate}%</span>}
                        </div>
                      </td>
                      <td className="r">
                        <input className={`field mono text-right ${aiLines ? "field-llm" : ""}`} inputMode="decimal" aria-label="Quantity"
                          disabled={off} value={ed.quantity ?? plain(l?.quantity)} onChange={(x) => setQty(lid, x.target.value)} />
                      </td>
                      <td className="r">
                        <input className={`field mono text-right ${bad ? "!text-[var(--bad)]" : ""}`} inputMode="decimal" aria-label="Rate"
                          disabled={off} title={bad ? `${qty} × ${rate} = ${(qty * rate).toFixed(2)}, not ${amount.toFixed(2)}` : ""}
                          value={ed.rate ?? plain(l?.rate)} onChange={(x) => setRate(lid, x.target.value)} />
                      </td>
                      <td className="px-1.5 pt-1.5 text-[12px]">{unit}</td>
                      <td className="r">
                        <input className={`field mono text-right font-bold ${aiLines ? "field-llm" : ""}`} inputMode="decimal"
                          aria-label="Amount" disabled={off}
                          value={ed.amount ?? (l?.amount ? two(l.amount) : "")} onChange={(x) => setAmount(lid, typed(x.target.value))} />
                      </td>
                      {!inTally && <td>{cross(r)}</td>}
                    </tr>
                  );
                })}
                {itemMode && ledgerRows.length > 0 && <tr><td colSpan={6} className="!py-1" /></tr>}
                {ledgerRows.map((r) => {
                  // What the bill says the row is, under the ledger it posts to.
                  const l = r.line_ids?.length === 1 ? lineOf(r.line_ids[0]) : undefined;
                  return (
                  <tr key={r.key} className={r.gone ? "tv-gone" : r.missing && !rowPicked(r) ? "bg-[var(--bad-soft)]" : ""}>
                    <td>
                      {picker(r, r.role === "line")}
                      {l?.description_raw && (
                        <div className="tv-sub">
                          <span title="As written on the bill">{l.description_raw}</span>
                          {l.quantity && l.rate && <span className="mono">{plain(l.quantity)} {l.uom ?? ""} × {plain(l.rate)}</span>}
                        </div>
                      )}
                    </td>
                    {itemMode && <td />}
                    <td className="r px-1.5 pt-1.5 text-[12px]">{r.role === "tax" && r.pct ? r.pct : ""}</td>
                    {itemMode && <td className="px-1.5 pt-1.5 text-[12px]">{r.role === "tax" && r.pct ? "%" : ""}</td>}
                    <td className="r">
                      {inTally || r.gone ? <span className="mono px-1.5">{tally(r.gone ? amt(r.amount) : r.value)}</span> : (
                        <input className={`field mono text-right ${r.role === "tax" ? llm(r.kind!) : r.role === "round" ? llm("round_off") : aiLines ? "field-llm" : ""}`}
                          inputMode="decimal" aria-label={`Amount: ${r.ledger || "new row"}`}
                          value={shownAmount(r)} onChange={(x) => typeAmount(r, x.target.value)} onBlur={() => setTyping(null)} />
                      )}
                    </td>
                    {!inTally && <td>{cross(r)}</td>}
                  </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {!inTally && preview && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <button className="btn !py-0.5 !text-[12px]" onClick={addRow} disabled={!e.company_id}><Icon name="plus" /> Add ledger</button>
              {shape === "invoice" && !voucherView && <>
                {!has("cgst") && !has("igst") && (
                  <button className="btn btn-ghost !py-0.5 !text-[12px]" onClick={() => addTaxes(["cgst", "sgst"], 0.5)}
                    title={billRate ? `At ${billRate / 2}% each, on the items` : "Type the amounts"}><Icon name="plus" /> CGST &amp; SGST</button>
                )}
                {!has("igst") && !has("cgst") && (
                  <button className="btn btn-ghost !py-0.5 !text-[12px]" onClick={() => addTaxes(["igst"], 1)}
                    title={billRate ? `At ${billRate}%, on the items` : "Type the amount"}><Icon name="plus" /> IGST</button>
                )}
                {!has("round") && (
                  <button className="btn btn-ghost !py-0.5 !text-[12px]" onClick={addRound}><Icon name="plus" /> Rounded Off</button>
                )}
              </>}
            </div>
          )}

          {/* ---- bottom: narration and the total ---- */}
          <div className="tv-foot">
            <label className="min-w-0 flex-1">
              <span className="text-[12px] font-semibold">Narration:</span>
              <textarea className="field" rows={2} value={val("narration") ?? ""} disabled={inTally}
                placeholder="blank, as your books keep it" onChange={(x) => set("narration", x.target.value)} />
            </label>
            <div className="tv-total shrink-0 text-right">
              {qtyTotal !== null && itemMode && !voucherView && <span className="mr-6">{+qtyTotal.toFixed(3)} {[...units][0]}</span>}
              {two(voucherTotal)}
            </div>
          </div>
          {party && billTotal !== null && (Math.abs(voucherTotal - billTotal) < 0.005
            ? <p className="mt-1 flex items-center justify-end gap-1 text-[12px] text-[var(--good)]">
                Bill total {money(billTotal)} <Icon name="check" title="matches" /></p>
            : <p className="mt-1 text-right text-[12px] font-semibold text-[var(--warn)]">
                The bill&apos;s total is {two(billTotal)}; this voucher comes to {two(voucherTotal)}, {two(Math.abs(voucherTotal - billTotal))} apart.
              </p>)}
          {dirty && <p className="mt-1 text-right text-[12px] text-[var(--warn)]">Not saved yet: <kbd>Ctrl+A</kbd> saves.</p>}

          {/* ---- what the bill itself says, to compare ---- */}
          <div className="tv-bill">
            <b className="font-semibold muted">On the bill</b>
            <label className="flex min-w-0 items-center gap-1"><span className="muted"><Evidence meta={meta.party_name}>{salesSide ? "Customer" : "Supplier"}</Evidence></span>
              <input className={`field !w-56 ${llm("party_name")}`} value={val("party_name_raw") ?? ""} disabled={inTally}
                onChange={(x) => set("party_name_raw", x.target.value)} />
            </label>
            <label className="flex items-center gap-1"><span className="muted"><Evidence meta={meta.party_gstin}>GSTIN</Evidence></span>
              <input className={`field mono !w-44 ${llm("party_gstin")}`} value={val("party_gstin") ?? ""} disabled={inTally}
                onChange={(x) => set("party_gstin", x.target.value)} />
            </label>
            {shape === "invoice" && (
              <label className="flex items-center gap-1"><span className="muted"><Evidence meta={meta.taxable_total}>Taxable value</Evidence></span>
                <input className={`field mono !w-28 text-right ${llm("taxable_total")}`} inputMode="decimal" value={val("taxable_total") ?? ""}
                  disabled={inTally} onChange={(x) => set("taxable_total", typed(x.target.value))} />
              </label>
            )}
            <label className="flex items-center gap-1"><span className="muted"><Evidence meta={meta.grand_total}>Total</Evidence></span>
              <input className={`field mono !w-28 text-right font-bold ${llm("grand_total")}`} inputMode="decimal" value={val("grand_total") ?? ""}
                disabled={inTally} onChange={(x) => set("grand_total", typed(x.target.value))} />
            </label>
          </div>
          {preview && preview.missing.length > 0 && (
            <p className="mt-2 text-[12.5px] text-[var(--bad)]">Before posting: {preview.missing.join(" · ")}</p>
          )}
        </div>

        {/* ---- the buttons down Tally's right-hand side ---- */}
        <aside className="tv-keys" aria-label="Voucher keys">
          <button type="button" onClick={() => dateRef.current?.focus()} disabled={inTally}><kbd>F2</kbd>Date</button>
          <button type="button" onClick={() => setAsVoucher((v) => !v)} disabled={shape !== "invoice"}
            title={shape === "invoice" ? `Now: ${modeName}` : "A payment, receipt or journal is kept as a voucher"}>
            <kbd>Ctrl+H</kbd>Change Mode
          </button>
          <button type="button" onClick={() => setMore((m) => !m)}><kbd>Ctrl+I</kbd>More Details</button>
          <button type="button" onClick={onOptional} disabled={inTally}
            title="Save it in Tally as an optional voucher: seen in the Day Book, not in your books until you make it regular">
            <kbd>Ctrl+L</kbd>Optional
          </button>
          <button type="button" onClick={onSave} disabled={!dirty}><kbd>Ctrl+A</kbd>Accept</button>
          <p className="mt-auto px-1.5 text-[10.5px] leading-snug faint"><kbd>Enter</kbd> next field. Hover a label: the line on the bill.</p>
        </aside>
      </div>
    </div>
  );
}
