"use client";

import {
  api, duplicatesOf, money, shortDate,
  type ApproveMode, type ApproveResult, type EntryDetail, type EntrySummary, type FieldMeta,
  type Preview, type VoucherTypeOption,
} from "@/lib/api";
import { Banner, Checks, Confidence, Icon, Spinner, useAsync } from "@/components/ui";
import { DocViewer } from "@/components/DocViewer";
import { TallyVoucher, amt, type Draft, type LineEdit } from "@/components/TallyVoucher";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

export function EntryPanel({ entryId, onChanged, onNext, onRejected }: {
  entryId: number; onChanged: () => void; onNext: () => void;
  /** A reject went through: the list can keep the bill a moment, with Undo. */
  onRejected?: (row: EntrySummary) => void;
}) {
  const [id, setId] = useState(entryId);
  const entry = useAsync(() => api.entry(id), [id]);
  const companies = useAsync(() => api.companies(), []);
  const here = useRef(true);
  useEffect(() => { here.current = true; return () => { here.current = false; }; }, []);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [types, setTypes] = useState<VoucherTypeOption[]>([]);
  const [allowPost, setAllowPost] = useState<boolean | null>(null);
  const [educational, setEducational] = useState(false);

  const [draft, setDraft] = useState<Draft>({});
  const [lineEdits, setLineEdits] = useState<Record<number, LineEdit>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ApproveResult | null>(null);
  const [note, setNote] = useState<{ tone: "good" | "warn" | "bad"; text: string } | null>(null);
  const [xml, setXml] = useState<string | null>(null);
  const [ackDuplicate, setAckDuplicate] = useState(false);
  const [ackErrors, setAckErrors] = useState(false);

  const e = entry.data;
  const loadPreview = useCallback(() => api.preview(id).then(setPreview).catch(() => setPreview(null)), [id]);

  useEffect(() => { loadPreview(); }, [loadPreview]);
  useEffect(() => {
    api.health().then((h) => { setAllowPost(h.tally.allow_post); setEducational(!!h.tally.educational); }).catch(() => {});
  }, []);
  useEffect(() => {
    if (e?.company_id !== undefined) api.voucherTypes(e?.company_id ?? undefined).then((r) => setTypes(r.types)).catch(() => {});
  }, [e?.company_id]);

  const refresh = (fresh: EntryDetail) => { entry.setData(fresh); loadPreview(); onChanged(); };
  const dirty = Object.keys(draft).length > 0 || Object.keys(lineEdits).length > 0;
  const dirtyNow = useRef(dirty);
  useEffect(() => { dirtyNow.current = dirty; }, [dirty]);

  const meta = (e?.field_meta ?? {}) as Record<string, FieldMeta>;
  const aiRead = Array.isArray(meta._llm_filled?.value) ? (meta._llm_filled.value as string[]) : [];
  // The AI's check of the bill, in the background.
  const ai = (meta._ai?.value ?? null) as { state?: string; changed?: string[]; differs?: string[] } | null;
  const checking = ai?.state === "waiting" || ai?.state === "reading";
  // The bill updates by itself when the AI's check ends. Not while you are
  // changing it: your save brings the AI's changes in with yours.
  useEffect(() => {
    if (!checking) return;
    const t = setInterval(async () => {
      const fresh = await api.entry(id).catch(() => null);
      if (!fresh || fresh.ai_state === "waiting" || fresh.ai_state === "reading") return;
      clearInterval(t);
      if (!dirtyNow.current) refresh(fresh);
      else setNote({ tone: "warn", text: "The AI has finished checking this bill. Save your changes to see what it changed as well." });
    }, 3000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checking, id]);
  const dup = duplicatesOf(e);
  const exactDupes = (dup?.findings ?? []).filter((f) => f.kind === "exact");
  const otherDupes = (dup?.findings ?? []).filter((f) => f.kind !== "exact");
  const errors = (e?.validations ?? []).filter((v) => v.severity === "error" && !["duplicate", "similar_bill"].includes(v.code));
  // Why the bill is in the company it is in: your GSTIN on it, your choice...
  const companyNote = typeof meta._company?.value === "string" ? meta._company.value : "";
  const inTally = e?.status === "posted" || e?.status === "drafted";

  const val = (k: string) => (k in draft ? draft[k] : (e as unknown as Record<string, unknown>)?.[k]) as string | null;
  const set = (k: string, v: unknown) => setDraft((d) => ({ ...d, [k]: v === "" ? null : v }));
  const unset = (k: string) => setDraft(({ [k]: _drop, ...rest }) => rest); // eslint-disable-line @typescript-eslint/no-unused-vars
  const setLine = (lid: number, patch: LineEdit) => setLineEdits((x) => ({ ...x, [lid]: { ...x[lid], ...patch } }));
  const dropLine = (lid: number) => setLineEdits(({ [lid]: _drop, ...rest }) => rest); // eslint-disable-line @typescript-eslint/no-unused-vars
  const keepLine = (lid: number) => setLineEdits((x) => {
    const { delete: _d, ...rest } = x[lid] ?? {}; // eslint-disable-line @typescript-eslint/no-unused-vars
    const { [lid]: _drop, ...others } = x; // eslint-disable-line @typescript-eslint/no-unused-vars
    return Object.keys(rest).length ? { ...others, [lid]: rest } : others;
  });

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setNote(null);
    try { await fn(); } catch (err) { setNote({ tone: "bad", text: (err as Error).message }); }
    finally { setBusy(null); }
  };

  const save = () => run("save", async () => {
    const lines = Object.entries(lineEdits).flatMap(([lid, p]) => {
      const { matched_name: _drop, ledger_name: _shown, ...rest } = p; // eslint-disable-line @typescript-eslint/no-unused-vars
      for (const k of ["amount", "quantity", "rate"] as const) if (rest[k] === "") rest[k] = null;
      if (Number(lid) >= 0) return [{ id: Number(lid), ...rest }];
      // Added here: created by the save, unless it was left empty.
      return !rest.ledger_id && !rest.stock_item_id && !amt(rest.amount) ? [] : [rest];
    });
    const { party_ledger_name: _n, tax_ledger_names: _t, ...body } = draft; // eslint-disable-line @typescript-eslint/no-unused-vars
    const fresh = await api.updateEntry(id, { ...body, ...(lines.length ? { lines } : {}), learn: true });
    setDraft({}); setLineEdits({});
    refresh(fresh);
    setNote({ tone: "good", text: "Saved. Your corrections are remembered for the next bill like this." });
  });

  const changeType = (name: string) => {
    if (!e || name === e.voucher_type) return;
    if (!confirm(`Re-read this document as ${name}?\n\nThe entry is rebuilt from the document for the new type; ` +
      `unsaved edits here are discarded. Ledger corrections you saved are kept.`)) return;
    run("retype", async () => {
      await api.reprocess(e.document_id, name);
      // It can wait behind a batch that is still being read: no time limit.
      while (here.current) {
        await new Promise((r) => setTimeout(r, 1500));
        const d = await api.document(e.document_id);
        if (d.entry_id) { setDraft({}); setLineEdits({}); setId(d.entry_id); onChanged(); return; }
        if (d.status === "failed") throw new Error(d.error ?? "Re-reading failed");
      }
    });
  };

  const changeCompany = (cid: number) => {
    if (!e || !cid || cid === e.company_id) return;
    if (dirty) { setNote({ tone: "warn", text: "Save your changes first." }); return; }
    const to = companies.data?.find((c) => c.id === cid)?.tally_name ?? "that company";
    if (e.company_id && !confirm(`Move this bill into the books of ${to}?\n\nIts ledgers are matched again ` +
      `from ${to}'s ledgers; ledgers you picked here are cleared.`)) return;
    run("company", async () => refresh(await api.updateEntry(id, { company_id: cid, learn: false })));
  };

  const approve = (mode: ApproveMode, dry = false) => {
    if (!e) return;
    if (dirty) { setNote({ tone: "warn", text: "Save your changes first." }); return; }
    const early = checking ? "\n\nThe AI has not finished checking this bill: anything it reads differently " +
                             "would only show afterwards." : "";
    if (!dry && mode !== "export") {
      const what = mode === "draft" ? "as a DRAFT (optional voucher - it will not affect your books yet)"
                                    : "and POST it to your books";
      if (!confirm(`${e.voucher_type} ${e.invoice_no ?? ""} for ${money(e.grand_total)}\n\n` +
                   `Send this to Tally ${what}?${early}`)) return;
    } else if (!dry && early && !confirm(`Export this bill now?${early}`)) return;
    run(mode, async () => {
      setResult(null); setXml(null);
      const r = await api.approve(id, { mode, dry_run: dry, override_duplicate: ackDuplicate, override_errors: ackErrors });
      setResult(r);
      if (dry && r.xml) setXml(r.xml);
      if (r.ok && !dry) { const fresh = await api.entry(id); refresh(fresh); }
      else if (!r.ok) { const fresh = await api.entry(id); entry.setData(fresh); }
    });
  };

  if (entry.loading && !e) return <div className="panel p-8"><Spinner /></div>;
  if (entry.error) return <div className="panel p-8 text-[var(--bad)]">{entry.error}</div>;
  if (!e) return null;

  return (
    <div className="space-y-3">
      {/* ---------------- header ---------------- */}
      <div className="panel flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3.5">
        <div className="min-w-0 flex-1 basis-80">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <span className="mono text-[18px] font-semibold text-[var(--ink-strong)]">{e.invoice_no ?? `Entry #${e.id}`}</span>
            <span className={`chip ${inTally ? "chip-good" : e.status === "rejected" ? "chip-bad" : ""}`}>
              {{ pending: "To review", approved: "Exported", drafted: "Draft in Tally", posted: "Posted", rejected: "Rejected" }[e.status] ?? e.status}
            </span>
            <Confidence value={e.confidence} label />
            {e.document?.ocr_used && <span className="text-[12px] muted">Scanned, read by OCR</span>}
            {aiRead.length > 0 && !ai && (
              <span className="chip chip-warn" title={`Read by the local AI: ${aiRead.join(", ")}. Check each one on the bill.`}>
                AI read {aiRead.length} field{aiRead.length > 1 ? "s" : ""}
              </span>
            )}
            {checking && (
              <span className="chip chip-info" title="The local AI is reading this bill too. What it fills in or reads differently shows here when it is done; what you change stays yours.">
                AI checking
              </span>
            )}
            {ai?.state === "done" && (() => {
              const changed = ai.changed ?? [], differs = ai.differs ?? [];
              const title = `Filled in or corrected by the local AI: ${changed.join(", ") || "nothing"}. ` +
                `Read differently, see Checks: ${differs.join(", ") || "nothing"}. Check them on the bill.`;
              return changed.length || differs.length ? (
                <span className="chip chip-warn" title={title}>
                  {changed.length ? `AI changed ${changed.length}` : ""}{changed.length && differs.length ? ", " : ""}
                  {differs.length ? `${differs.length} differ${differs.length === 1 ? "s" : ""}` : ""}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-[12px] text-[var(--good)]" title="The local AI read the bill the same way.">
                  <Icon name="check" /> AI checked
                </span>
              );
            })()}
            {ai?.state === "failed" && (
              <span className="chip chip-warn" title="The local AI did not answer. The bill is as the OCR read it.">AI check failed</span>
            )}
          </div>
          <div className="mt-0.5 truncate muted">
            {e.party_name_raw ?? "Unknown party"} · {money(e.grand_total, e.currency)} · {shortDate(e.invoice_date)}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {((companies.data?.length ?? 0) > 1 || (!e.company_id && !!companies.data?.length)) && (
            <label className="flex items-center gap-2" title={companyNote}>
              <span className="label !mb-0">Company</span>
              <select className={`field !w-auto max-w-56 font-semibold ${e.company_id ? "" : "!border-[var(--warn)]"}`}
                value={e.company_id ?? ""} disabled={inTally || !!busy}
                onChange={(ev) => changeCompany(Number(ev.target.value))}>
                {!e.company_id && <option value="">Choose…</option>}
                {companies.data!.map((c) => <option key={c.id} value={c.id}>{c.tally_name}</option>)}
              </select>
            </label>
          )}
          <label className="flex items-center gap-2">
            <span className="label !mb-0">Voucher type</span>
            <select className="field !w-auto font-semibold" value={e.voucher_type} disabled={inTally || !!busy}
              onChange={(ev) => changeType(ev.target.value)}>
              {!types.some((t) => t.name === e.voucher_type) && <option>{e.voucher_type}</option>}
              {types.map((t) => <option key={t.name} value={t.name}>{t.name}{t.custom ? ` (${t.parent})` : ""}</option>)}
            </select>
          </label>
          <button className="btn" disabled={!!busy || inTally}
            onClick={() => run("rematch", async () => refresh(await api.rematch(id)))}>Re-match</button>
          {/* The main colour only when there is something to save. */}
          <button className={`btn ${dirty ? "btn-primary" : "btn-ghost"}`} onClick={save} disabled={!dirty || !!busy}>
            {busy === "save" ? "Saving…" : dirty ? "Save changes" : "Saved"}
          </button>
          <button className="btn btn-ghost" onClick={onNext}>Next</button>
        </div>
      </div>

      {note && <Banner tone={note.tone} onClose={() => setNote(null)}>{note.text}</Banner>}
      {busy === "retype" && <Banner tone="info"><Spinner label="Re-reading the document" /></Banner>}
      {!e.company_id && !inTally && !!companies.data?.length && (
        <Banner tone="warn"><b>Which company&apos;s books?</b> {companyNote} Choose it in <i>Company</i>, above.</Banner>
      )}
      {e.company_id && (meta._company?.confidence ?? 1) < 1 && !inTally && (
        <Banner tone="warn">{companyNote}</Banner>
      )}
      {e.status === "rejected" && (
        <Banner tone="warn">
          <span className="mr-2">Rejected.</span>
          <button className="btn !py-0.5 !text-[12px]" disabled={!!busy}
            onClick={() => run("restore", async () => { await api.restore(id); refresh(await api.entry(id)); })}>
            Put it back
          </button>
        </Banner>
      )}
      {inTally && (
        <Banner tone="good">
          {e.status === "drafted"
            ? <>In Tally as an <b>optional (draft) voucher</b> - it shows in the Day Book and does not touch your books until you mark it regular there.</>
            : <>Posted to Tally{e.tally_voucher_id ? ` - voucher id ${e.tally_voucher_id}` : ""}.</>}
          {" "}To change it now, alter the voucher inside Tally.
        </Banner>
      )}

      {/* ---------------- side by side ---------------- */}
      <div className="grid gap-3 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="xl:sticky xl:top-[62px] xl:self-start">
          <DocViewer documentId={e.document_id} filename={e.document?.filename} />
        </div>

        <div className="space-y-3">
          <TallyVoucher e={e} preview={preview} meta={meta} draft={draft} lineEdits={lineEdits}
            val={val} set={set} unset={unset} setLine={setLine} dropLine={dropLine} keepLine={keepLine}
            inTally={inTally} dirty={dirty} checking={checking}
            registration={companies.data?.find((c) => c.id === e.company_id)?.gst_registration}
            onSave={save} onOptional={() => approve("draft")}
            onCreateParty={() => run("master", async () => {
              const r = await api.createMaster({ entry_id: id, kind: "ledger" });
              setNote({ tone: "good", text: r.message });
              refresh(await api.entry(id));
            })} />

          {/* duplicates */}
          <div className="panel">
            <div className="section-title">
              <span>Duplicate check</span>
              <button className="btn !py-1 !text-[12px]" disabled={!!busy || !e.company_id}
                onClick={() => run("dup", async () => refresh(await api.checkDuplicates(id)))}>
                {busy === "dup" ? "Checking Tally…" : "Check Tally again"}
              </button>
            </div>
            <div className="space-y-2 p-4">
              <p className="text-[12.5px] muted">
                {dup?.tally_checked
                  ? <>Searched <b className="text-[var(--ink)]">{dup.tally_scanned}</b> voucher{dup.tally_scanned === 1 ? "" : "s"} in Tally
                      {dup.tally_window && <> from {shortDate(dup.tally_window[0])} to {shortDate(dup.tally_window[1])}</>}
                      , plus every entry in this app.</>
                  : <><span className="text-[var(--warn)]">Tally was not checked</span> - {dup?.tally_error ?? "not yet run"}.
                      {" "}Only this app&apos;s own entries were compared. It is checked again automatically before posting.</>}
              </p>
              {!dup?.findings.length && dup?.tally_checked && (
                <p className="font-semibold text-[var(--good)]">No duplicates or similar bills found.</p>
              )}
              {[...exactDupes, ...otherDupes].map((f, i) => (
                <div key={i} className={`note flex-col !gap-0 ${f.kind === "exact" ? "note-bad" : "note-warn"}`}>
                  <div className="flex items-baseline gap-2">
                    <span className={`font-semibold ${f.kind === "exact" ? "text-[var(--bad)]" : "text-[var(--warn)]"}`}>
                      {f.kind === "exact" ? "Likely duplicate" : f.kind === "reference" ? "Same bill number" : "Similar bill"}
                    </span>
                    <span className="text-[12px] muted">{f.source === "tally" ? "in Tally" : "in this app"}</span>
                  </div>
                  <p className="mt-1">{f.reason}</p>
                  {(f.amount || f.party) && (
                    <p className="mono mt-0.5 text-[12px] muted">{f.party} · {money(f.amount)} · {shortDate(f.date)}</p>
                  )}
                </div>
              ))}
              {exactDupes.length > 0 && !inTally && (
                <label className="flex items-start gap-2 pt-1 font-semibold">
                  <input type="checkbox" className="mt-0.5" checked={ackDuplicate} onChange={(x) => setAckDuplicate(x.target.checked)} />
                  I&apos;ve checked - this is a different bill. Let me post it anyway.
                </label>
              )}
            </div>
          </div>

          {/* checks */}
          <div className="panel">
            <div className="section-title"><span>Checks</span></div>
            <div className="p-4">
              <Checks items={e.validations ?? []} />
              {errors.length > 0 && !inTally && (
                <label className="mt-3 flex items-start gap-2 text-[12.5px]">
                  <input type="checkbox" className="mt-0.5" checked={ackErrors} onChange={(x) => setAckErrors(x.target.checked)} />
                  Post anyway - I&apos;ve verified this against the paper copy.
                </label>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ---------------- act ---------------- */}
      {!inTally && e.status !== "rejected" && (
        <div className="panel panel-shadow sticky bottom-3 z-30 flex flex-wrap items-center gap-2 p-3">
          <button className="btn btn-primary" disabled={!!busy || dirty} onClick={() => approve("post")}>
            {busy === "post" ? "Posting…" : "Post to Tally"}
          </button>
          <button className="btn" disabled={!!busy || dirty} onClick={() => approve("draft")}
            title="An optional voucher: visible in Tally's Day Book, excluded from your books until you mark it regular">
            {busy === "draft" ? "Sending…" : "Save as draft in Tally"}
          </button>
          <button className="btn" disabled={!!busy || dirty} onClick={() => approve("export")}>
            {busy === "export" ? "Exporting…" : "Export XML"}
          </button>
          <button className="btn btn-ghost" disabled={!!busy} onClick={() => approve("export", true)}>Show XML</button>
          {allowPost === false && (
            <span className="text-[12px] muted">
              Writing to Tally is off - turn it on in <Link className="link" href="/settings/">Settings</Link>.
            </span>
          )}
          {educational && (val("invoice_date") ?? "") !== "" && ![1, 2, 31].includes(Number(String(val("invoice_date")).slice(8, 10))) && (
            <span className="text-[12px] text-[var(--warn)]">
              This Tally is in Educational Mode: it takes vouchers dated only the 1st, 2nd or 31st, so this date will be refused.
            </span>
          )}
          <button className="btn btn-ghost btn-danger ml-auto" disabled={!!busy}
            onClick={() => {
              const reason = prompt("Reject this entry? Reason (optional):");
              if (reason === null) return;
              run("reject", async () => {
                onRejected?.(await api.reject(id, reason));
                refresh(await api.entry(id));  // shown as Rejected, if it was the last one
                onNext();
              });
            }}>Reject</button>
        </div>
      )}

      {result && (
        <Banner tone={result.ok ? "good" : result.needs_ack ? "warn" : "bad"} onClose={() => setResult(null)}>
          <div className="font-semibold">{result.message}</div>
          {result.blocked_by.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-5">{result.blocked_by.map((b, i) => <li key={i}>{b}</li>)}</ul>
          )}
          {result.needs_ack && !ackDuplicate && (
            <label className="mt-2 flex items-start gap-2 font-semibold">
              <input type="checkbox" className="mt-0.5" checked={ackDuplicate}
                onChange={(x) => setAckDuplicate(x.target.checked)} />
              I understand - post it anyway, then press the button again.
            </label>
          )}
          {result.export_path && <div className="mono mt-1 text-[11.5px]">{result.export_path}</div>}
        </Banner>
      )}

      {xml && (
        <div className="panel overflow-hidden">
          <div className="section-title"><span>Exactly what Tally would receive</span>
            <button className="btn btn-ghost !py-1" onClick={() => setXml(null)}>Close</button></div>
          <pre className="mono max-h-96 overflow-auto p-4 text-[11.5px]">{xml}</pre>
        </div>
      )}
    </div>
  );
}
