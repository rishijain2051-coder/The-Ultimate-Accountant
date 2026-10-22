"use client";

import { api, type Candidate, type Ledger, type StockItem } from "@/lib/api";
import { Icon } from "@/components/ui";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

const MENU_W = 300;
const MENU_H = 320;

// Groups common enough to offer in the "+ Create" form's own select; the
// user can still change it. Not every Tally group -- just the ones a bill's
// ledgers land under.
const COMMON_GROUPS = [
  "Sundry Creditors", "Sundry Debtors", "Purchase Accounts", "Sales Accounts",
  "Duties & Taxes", "Direct Expenses", "Indirect Expenses", "Direct Incomes",
  "Indirect Incomes", "Bank Accounts", "Cash-in-Hand", "Fixed Assets",
];
const DUTY_HEADS = ["CGST", "SGST/UTGST", "IGST", "Cess"];

/** What a picker needs to offer "+ Create ... in Tally" for its own row:
 * which entry it is on, the line it is for (a stock item needs one), and the
 * defaults for the row's role (a tax row's duty head, an item line's GST). */
export interface CreateHint {
  entryId?: number;
  /** Off a bill (a bank statement's row): how that screen makes the ledger. */
  create?: (body: { name: string; parent?: string; duty_head?: string }) => Promise<{ id: number; name: string }>;
  lineId?: number;
  /** This row may become a stock item, not only a ledger (the item picker). */
  allowStock?: boolean;
  defaultGroup?: string;
  dutyHead?: string;
  uom?: string;
  hsn?: string | null;
  gstRate?: number | null;
}

/**
 * Pick a Tally master. Shows the app's own suggestions first with their
 * scores, because "why did it pick that?" is the question this screen exists
 * to answer -- then falls back to a live search.
 *
 * The menu is position:fixed, measured from the trigger, so a table or a
 * scrolling panel around it can never clip it. It opens upward when there is
 * no room below.
 *
 * items: a bill line can post to a stock item as well as a ledger; the pick
 * says which it is, so an item is never saved as a ledger.
 *
 * onCreate: when typing matches nothing, offer to create the master in Tally
 * (of the right group) and pick it at once. Omit to leave that picker as
 * search-only.
 */
export function LedgerPicker({
  companyId, value, valueName, candidates, groups, groupsLabel, placeholder = "Search ledgers…",
  onPick, disabled, missing, items, onCreate,
}: {
  companyId: number | null;
  value: number | null;
  valueName: string | null;
  candidates?: Candidate[] | null;
  /** Tally groups to search by default, e.g. ["Sundry Creditors"]. */
  groups?: string[];
  groupsLabel?: string;
  placeholder?: string;
  onPick: (id: number, name: string, kind: "ledger" | "item") => void;
  disabled?: boolean;
  missing?: boolean;
  items?: boolean;
  onCreate?: CreateHint;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Ledger[]>([]);
  const [itemResults, setItemResults] = useState<StockItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [allGroups, setAllGroups] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number; width: number; up: boolean } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  // "+ Create ... in Tally": a small inline form in place of the results.
  const [creating, setCreating] = useState(false);
  const [asStock, setAsStock] = useState(false);
  const [cName, setCName] = useState("");
  const [cGroup, setCGroup] = useState("");
  const [cDuty, setCDuty] = useState("");
  const [cUom, setCUom] = useState("Nos");
  const [cHsn, setCHsn] = useState("");
  const [cRate, setCRate] = useState("");
  const [cBusy, setCBusy] = useState(false);
  const [cErr, setCErr] = useState<string | null>(null);
  const [units, setUnits] = useState<string[]>([]);

  const place = useCallback(() => {
    const r = trigger.current?.getBoundingClientRect();
    if (!r) return;
    const width = Math.max(r.width, MENU_W);
    const up = window.innerHeight - r.bottom < MENU_H + 12 && r.top > MENU_H;
    setPos({
      top: up ? r.top - 4 : r.bottom + 4,
      left: Math.min(r.left, window.innerWidth - width - 8),
      width, up,
    });
  }, []);

  useLayoutEffect(() => { if (open) place(); }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!menu.current?.contains(t) && !trigger.current?.contains(t)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true); // capture: any scrolling ancestor
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", key);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, place]);

  const scope = allGroups ? "" : (groups ?? []).join(",");
  useEffect(() => {
    if (!open || !companyId) return;
    setLoading(true);
    const t = setTimeout(() => {
      Promise.all([
        api.ledgers(companyId, q, scope).then(setResults).catch(() => setResults([])),
        items ? api.stockItems(companyId, q).then(setItemResults).catch(() => setItemResults([])) : null,
      ]).finally(() => setLoading(false));
    }, 160); // debounce: the list can be thousands of ledgers
    return () => clearTimeout(t);
  }, [open, q, companyId, scope, items]);

  // The units already used by synced stock items, for the create-item form's
  // own select -- fetched once per open, not on every keystroke.
  useEffect(() => {
    if (!open || !items || !companyId || !onCreate) return;
    api.stockItems(companyId, "").then((list) => {
      const seen = Array.from(new Set(list.map((s) => s.uom).filter((u): u is string => !!u)));
      setUnits(seen.includes("Nos") ? seen : ["Nos", ...seen]);
    }).catch(() => setUnits(["Nos"]));
  }, [open, items, companyId, onCreate]);

  // A fresh open always starts at the search, not a create form left over from last time.
  const openMenu = (next: boolean) => { if (next) setCreating(false); setOpen(next); };

  const suggestions = useMemo(() => (candidates ?? []).filter((c) => c.ledger_id ?? c.stock_item_id), [candidates]);
  const pick = (id: number, name: string, kind: "ledger" | "item" = "ledger") => {
    onPick(id, name, kind); setOpen(false); setQ("");
    trigger.current?.focus(); // keys carry on from here, as in Tally
  };
  // Enter in the search box takes the first thing listed.
  const first = (): [number, string, "ledger" | "item"] | null => {
    const c = !q ? suggestions[0] : undefined;
    if (c) return [(c.ledger_id ?? c.stock_item_id)!, c.name, c.stock_item_id ? "item" : "ledger"];
    if (itemResults.length) return [itemResults[0].id, itemResults[0].tally_name, "item"];
    return results.length ? [results[0].id, results[0].tally_name, "ledger"] : null;
  };

  // Whether the typed text matches nothing shown, so "+ Create" can offer it.
  const qTrim = q.trim();
  const canStock = !!items && !!onCreate?.allowStock && onCreate?.lineId !== undefined;
  const canCreate = !!onCreate && qTrim !== "" && !loading
    && ![...results, ...itemResults].some((r) => r.tally_name.toLowerCase() === qTrim.toLowerCase());

  const openCreate = () => {
    setCName(qTrim);
    setAsStock(canStock);
    setCGroup(canStock ? "Primary" : (onCreate?.defaultGroup ?? groups?.[0] ?? "Sundry Creditors"));
    setCDuty(onCreate?.dutyHead ?? "CGST");
    setCUom(onCreate?.uom || "Nos");
    setCHsn(onCreate?.hsn ?? "");
    setCRate(onCreate?.gstRate != null ? String(onCreate.gstRate) : "");
    setCErr(null);
    setCreating(true);
  };

  const submitCreate = async () => {
    if (!onCreate) return;
    const name = cName.trim();
    if (!name) { setCErr("A name is required."); return; }
    setCBusy(true); setCErr(null);
    try {
      const ledger = { name, parent: cGroup || undefined,
        duty_head: cGroup === "Duties & Taxes" ? (cDuty || "CGST") : undefined };
      const r = asStock
        ? await api.createMaster({
            entry_id: onCreate.entryId, kind: "stock", name, parent: cGroup || "Primary",
            uom: cUom || "Nos", hsn: cHsn || undefined, gst_rate: cRate ? Number(cRate) : undefined,
            line_id: onCreate.lineId,
          })
        : onCreate.create ? await onCreate.create(ledger)
        : await api.createMaster({ entry_id: onCreate.entryId, kind: "ledger", ...ledger });
      pick(r.id, r.name, asStock ? "item" : "ledger");
    } catch (err) {
      setCErr((err as Error).message);
    } finally {
      setCBusy(false);
    }
  };

  return (
    <>
      <button ref={trigger} type="button" disabled={disabled || !companyId} onClick={() => openMenu(!open)}
        data-picker={valueName ? "set" : "empty"}
        onKeyDown={(ev) => {
          // Typing on the field searches, as in Tally.
          if (ev.key.length === 1 && ev.key !== " " && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
            ev.preventDefault(); setQ(ev.key); openMenu(true);
          }
        }}
        className={`field flex items-center justify-between gap-2 text-left ${missing && !valueName ? "!border-[var(--bad)]" : ""}`}>
        <span className={`truncate ${valueName ? "" : "muted"}`}>
          {valueName ?? (companyId ? "Choose a ledger" : "Choose the company first")}
        </span>
        <Icon name="chevron" className="faint" />
      </button>

      {open && pos && (
        <div ref={menu} role="listbox"
          className="panel panel-shadow fixed z-[100] flex flex-col p-1.5"
          style={{ left: pos.left, width: pos.width, maxHeight: MENU_H,
                   ...(pos.up ? { bottom: window.innerHeight - pos.top } : { top: pos.top }) }}>
          {!creating && (
            <>
              <input autoFocus className="field mb-1.5" placeholder={placeholder}
                value={q} onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  e.preventDefault();
                  const f = loading ? null : first();
                  if (f) pick(...f);
                }} />
              {!!groups?.length && (
                <label className="mb-1 flex items-center gap-1.5 px-1 text-[11.5px] muted">
                  <input type="checkbox" checked={allGroups} onChange={(e) => setAllGroups(e.target.checked)} />
                  Search every ledger, not only {groupsLabel ?? groups.join(" / ")}
                </label>
              )}
            </>
          )}
          <div className="min-h-0 flex-1 overflow-auto">
            {creating ? (
              <div className="space-y-1.5 p-1">
                {canStock && (
                  <div className="flex gap-3 px-1 text-[11.5px]">
                    <label className="flex items-center gap-1">
                      <input type="radio" checked={asStock} onChange={() => { setAsStock(true); setCGroup("Primary"); }} /> Stock item
                    </label>
                    <label className="flex items-center gap-1">
                      <input type="radio" checked={!asStock}
                        onChange={() => { setAsStock(false); setCGroup(onCreate?.defaultGroup ?? groups?.[0] ?? "Sundry Creditors"); }} /> Ledger
                    </label>
                  </div>
                )}
                <input className="field" placeholder="Name" value={cName} onChange={(e) => setCName(e.target.value)} />
                {asStock ? (
                  <>
                    <input className="field" placeholder="Group" value={cGroup} onChange={(e) => setCGroup(e.target.value)} />
                    <div className="flex gap-1.5">
                      <select className="field !w-24" value={cUom} onChange={(e) => setCUom(e.target.value)}>
                        {units.map((u) => <option key={u} value={u}>{u}</option>)}
                      </select>
                      <input className="field" placeholder="HSN" value={cHsn} onChange={(e) => setCHsn(e.target.value)} />
                      <input className="field !w-20" placeholder="GST %" inputMode="decimal" value={cRate}
                        onChange={(e) => setCRate(e.target.value)} />
                    </div>
                  </>
                ) : (
                  <>
                    <select className="field" value={cGroup} onChange={(e) => setCGroup(e.target.value)}>
                      {COMMON_GROUPS.map((g) => <option key={g} value={g}>{g}</option>)}
                    </select>
                    {cGroup === "Duties & Taxes" && (
                      <select className="field" value={cDuty} onChange={(e) => setCDuty(e.target.value)}>
                        {DUTY_HEADS.map((h) => <option key={h} value={h}>{h}</option>)}
                      </select>
                    )}
                  </>
                )}
                {cErr && <p className="text-[11.5px] text-[var(--bad)]">{cErr}</p>}
                <div className="flex justify-end gap-1.5 pt-0.5">
                  <button type="button" className="btn btn-ghost !py-1 !text-[12px]" disabled={cBusy}
                    onClick={() => setCreating(false)}>Cancel</button>
                  <button type="button" className="btn btn-primary !py-1 !text-[12px]" disabled={cBusy} onClick={submitCreate}>
                    {cBusy ? "Creating…" : "Create"}
                  </button>
                </div>
              </div>
            ) : (
              <>
                {!q && suggestions.length > 0 && (
                  <>
                    <p className="px-2 pb-1 pt-0.5 text-[11.5px] font-semibold faint">Suggested</p>
                    {suggestions.map((c) => {
                      const id = (c.ledger_id ?? c.stock_item_id)!;
                      return (
                        <button key={`s${id}`} type="button" onClick={() => pick(id, c.name, c.stock_item_id ? "item" : "ledger")}
                          className="row-hover flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left">
                          <span className="truncate">{c.name}</span>
                          <span className="mono shrink-0 text-[11px] faint">{Math.round(c.score * 100)}%</span>
                        </button>
                      );
                    })}
                    <div className="my-1 border-t hairline" />
                  </>
                )}
                {loading && <p className="px-2 py-1 muted">Searching…</p>}
                {!loading && itemResults.length > 0 && (
                  <>
                    <p className="px-2 pb-1 pt-0.5 text-[11.5px] font-semibold faint">Stock items</p>
                    {itemResults.map((it) => (
                      <button key={`i${it.id}`} type="button" onClick={() => pick(it.id, it.tally_name, "item")}
                        className="row-hover flex w-full flex-col rounded-md px-2 py-1.5 text-left">
                        <span className="truncate">{it.tally_name}</span>
                        <span className="text-[11px] faint">{it.parent ?? "—"}{it.uom ? ` · ${it.uom}` : ""}{it.hsn ? ` · HSN ${it.hsn}` : ""}</span>
                      </button>
                    ))}
                    <p className="px-2 pb-1 pt-1.5 text-[11.5px] font-semibold faint">Ledgers</p>
                  </>
                )}
                {!loading && results.length === 0 && !canCreate && (
                  <p className="px-2 py-1 muted">
                    {q ? "No ledger matches that." : "No ledgers here - tick “search every ledger”, or sync in Settings."}
                  </p>
                )}
                {!loading && results.map((l) => (
                  <button key={l.id} type="button" onClick={() => pick(l.id, l.tally_name)}
                    className={`row-hover flex w-full flex-col rounded-md px-2 py-1.5 text-left ${
                      l.id === value ? "bg-[var(--active)]" : ""}`}>
                    <span className="truncate">{l.tally_name}</span>
                    <span className="text-[11px] faint">{l.parent ?? "—"}{l.gstin ? ` · ${l.gstin}` : ""}</span>
                  </button>
                ))}
                {canCreate && (
                  <button type="button" onClick={openCreate}
                    className="row-hover mt-0.5 flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left font-medium text-[var(--ink-strong)]">
                    <Icon name="plus" /><span className="truncate">Create “{qTrim}” in Tally…</span>
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
