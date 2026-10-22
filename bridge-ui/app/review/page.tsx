"use client";

import { api, money, shortDate, type EntrySummary } from "@/lib/api";
import { Banner, Confidence, Spinner, useAsync } from "@/components/ui";
import { EntryPanel } from "@/components/EntryPanel";
import { Boundary } from "@/components/Boundary";
import { Suspense, useEffect, useState } from "react";

const FILTERS = [
  { key: "pending", label: "To review" },
  { key: "approved", label: "Exported" },
  { key: "drafted", label: "Drafts in Tally" },
  { key: "posted", label: "Posted" },
  { key: "rejected", label: "Rejected" },
];
// How long a rejected bill stays in the list, with Undo, before it leaves.
const UNDO_SECONDS = 15;

export default function ReviewPage() {
  return <Suspense fallback={<Spinner />}><Review /></Suspense>;
}

function Review() {
  const [status, setStatus] = useState("pending");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [banner, setBanner] = useState<{ tone: "good" | "warn" | "bad"; text: string } | null>(null);

  const stats = useAsync(() => api.stats(), [status]);
  const reading = stats.data?.reading ?? 0;
  const checking = stats.data?.ai_checking ?? 0;
  // While a batch is read, and the AI checks it, bills stay in the order they
  // were given (page order), so the list does not reshuffle as each one
  // joins it or its check ends.
  const batch = reading > 0 || checking > 0;
  const list = useAsync(() => api.entries({ status, q, sort: batch ? "upload" : "confidence", limit: 300 }),
    [status, q, batch]);
  const companies = useAsync(() => api.companies(), []);
  const companyName = new Map((companies.data ?? []).map((c) => [c.id, c.tally_name]));

  // Deep link from the upload screen: /review/?id=12 opens it full width.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("id");
    if (id) { setSelected(Number(id)); setCollapsed(true); }
  }, []);

  useEffect(() => {
    if (selected === null && list.data?.length) setSelected(list.data[0].id);
  }, [list.data, selected]);

  const afterChange = () => { list.reload(); stats.reload(); };

  // Rejected here in the last UNDO_SECONDS: still shown in its place, with
  // Undo. The reject itself is saved at once; Undo takes it back.
  const [undo, setUndo] = useState<{ row: EntrySummary; index: number; until: number }[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [nonce, setNonce] = useState(0); // reopens the entry panel after an Undo
  useEffect(() => {
    if (!undo.length) return;
    const t = setInterval(() => {
      const n = Date.now();
      setNow(n);
      setUndo((u) => u.filter((x) => x.until > n));
    }, 1000);
    return () => clearInterval(t);
  }, [undo.length]);
  const rejected = (row: EntrySummary) => {
    const n = Date.now();
    const index = Math.max(0, (list.data ?? []).findIndex((r) => r.id === row.id));
    setNow(n);
    setUndo((u) => [...u.filter((x) => x.row.id !== row.id), { row, index, until: n + UNDO_SECONDS * 1000 }]);
  };
  const undoReject = async (id: number) => {
    try {
      await api.restore(id);
      setUndo((u) => u.filter((x) => x.row.id !== id));
      afterChange();
      setSelected(id);
      setNonce((k) => k + 1);
    } catch (e) { setBanner({ tone: "bad", text: (e as Error).message }); }
  };
  const rows = [...(list.data ?? [])];
  for (const u of undo) {
    if (!rows.some((r) => r.id === u.row.id)) rows.splice(Math.min(u.index, rows.length), 0, u.row);
  }

  // While a batch is still being read, each bill joins the list as it is
  // ready. The open entry is left alone, unsaved edits included.
  useEffect(() => {
    if (!batch) return;
    const t = setInterval(afterChange, 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch]);

  const exportClean = async () => {
    const ids = stats.data?.clean_ids ?? [];
    if (!ids.length) return;
    if (!confirm(`Export ${ids.length} entries worth ${money(stats.data?.clean_value)} as Tally XML?\n\n` +
      `These scored ${Math.round((stats.data?.threshold ?? 0.9) * 100)}% or higher with no failed check and no ` +
      `duplicate. Anything else is skipped and stays here.`)) return;
    try {
      const r = await api.approveBatch(ids, "export");
      setBanner({ tone: r.approved === r.total ? "good" : "warn",
        text: `${r.approved} of ${r.total} exported.${r.approved < r.total ? " Open the rest to see why they were skipped." : ""}` });
      afterChange();
    } catch (e) { setBanner({ tone: "bad", text: (e as Error).message }); }
  };

  return (
    <div className="stagger mx-auto max-w-[1800px] space-y-4">
      {banner && <Banner tone={banner.tone} onClose={() => setBanner(null)}>{banner.text}</Banner>}

      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-ghost" onClick={() => setCollapsed((c) => !c)} title="Show or hide the list">
          {collapsed ? "Show list" : "Hide list"}
        </button>
        <div className="tabs scroll-x max-w-full">
          {FILTERS.map((f) => (
            <button key={f.key} onClick={() => { setStatus(f.key); setSelected(null); setUndo([]); }}
              aria-pressed={status === f.key} className="tab">
              {f.label}
              {f.key === "pending" && !!stats.data?.pending && <span className="mono ml-1.5 text-[11.5px] faint">{stats.data.pending}</span>}
            </button>
          ))}
        </div>
        <input className="field max-w-72" placeholder="Search number, party or GSTIN…" value={q}
          onChange={(e) => setQ(e.target.value)} />
        {!!stats.data?.clean && status === "pending" && (
          <button className="btn ml-auto" onClick={exportClean}>
            Export {stats.data.clean} clean · {money(stats.data.clean_value)}
          </button>
        )}
      </div>

      <div className={`grid gap-3 ${collapsed ? "" : "xl:grid-cols-[320px_1fr]"}`}>
        {!collapsed && (
          <aside className="panel max-h-[calc(100vh-150px)] overflow-auto">
            {status === "pending" && batch && (
              <div className="border-b hairline bg-[var(--info-soft)] px-3.5 py-2.5">
                {reading > 0 && <Spinner label={`Reading ${reading} more`} />}
                {reading > 0 && <p className="mt-0.5 text-[12px] muted">Each one joins this list when it is ready.</p>}
                {checking > 0 && (
                  <p className="mt-0.5 text-[12px] muted">
                    The AI is checking {checking} bill{checking === 1 ? "" : "s"} in the background. Review them meanwhile:
                    what you change stays yours.
                  </p>
                )}
              </div>
            )}
            {status === "pending" && stats.data?.failed.map((f) => (
              <div key={f.id} className="border-b hairline bg-[var(--bad-soft)] px-3.5 py-2.5 text-[12.5px]">
                <div className="truncate font-semibold" title={f.filename}>{f.filename}</div>
                <div className="line-clamp-2 text-[var(--bad)]" title={f.error ?? ""}>Could not be read: {f.error}</div>
                <div className="mt-1.5 flex gap-2">
                  <button className="btn !py-0.5 !text-[12px]"
                    onClick={() => api.reprocess(f.id).then(afterChange, (e) => setBanner({ tone: "bad", text: e.message }))}>
                    Try again
                  </button>
                  <button className="btn btn-ghost !py-0.5 !text-[12px]" title="Take it off this list. Uploading it again reads it again."
                    onClick={() => api.dismiss(f.id).then(afterChange, (e) => setBanner({ tone: "bad", text: e.message }))}>
                    Set aside
                  </button>
                </div>
              </div>
            ))}
            {list.loading && !list.data && <p className="p-4"><Spinner /></p>}
            {list.error && <p className="p-4 text-[var(--bad)]">{list.error}</p>}
            {!list.loading && !rows.length && !reading && (
              <p className="p-5 muted">Nothing here. Upload documents from the Upload screen.</p>
            )}
            <Boundary what="list">
            <ul className="stagger">
              {rows.map((e) => {
                const u = undo.find((x) => x.row.id === e.id);
                return u ? (
                  <li key={e.id} className="flex items-center justify-between gap-2 border-b hairline bg-[var(--bad-soft)] px-3.5 py-2.5">
                    <div className="min-w-0">
                      <div className="mono truncate font-bold line-through">{e.invoice_no ?? `#${e.id}`}</div>
                      <div className="truncate text-[12px] muted">
                        Rejected · leaves this list in {Math.max(1, Math.ceil((u.until - now) / 1000))} s
                      </div>
                    </div>
                    <button className="btn !py-0.5 !text-[12px]" onClick={() => undoReject(e.id)}>Undo</button>
                  </li>
                ) : (
                  <Row key={e.id} entry={e} active={e.id === selected} onClick={() => setSelected(e.id)}
                    company={(companies.data?.length ?? 0) > 1 ? (companyName.get(e.company_id ?? -1) ?? null) : undefined} />
                );
              })}
            </ul>
            </Boundary>
          </aside>
        )}

        <section className="min-w-0">
          {selected ? (
            <Boundary what="bill" key={`${selected}-${nonce}`}>
            <EntryPanel entryId={selected} onChanged={afterChange} onRejected={rejected}
              onNext={() => {
                const rows = list.data ?? [];
                const i = rows.findIndex((r) => r.id === selected);
                const next = rows[i + 1] ?? rows.find((r) => r.id !== selected);
                if (next) setSelected(next.id);
              }} />
            </Boundary>
          ) : (
            <div className="panel dots grid place-items-center p-16 muted">
              {reading ? "The first bill opens here as soon as it is read." : "Select an entry to review."}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

/** company: the name to show when there are several companies (null: none
 *  chosen yet); undefined when there is only one, so nothing to tell apart. */
function Row({ entry, active, onClick, company }: {
  entry: EntrySummary; active: boolean; onClick: () => void; company?: string | null;
}) {
  return (
    <li>
      <button onClick={onClick}
        className={`w-full border-b hairline px-3.5 py-2.5 text-left ${active ? "bg-[var(--active)]" : "row-hover"}`}>
        <div className="flex items-baseline justify-between gap-2">
          <span className="mono truncate font-semibold text-[var(--ink-strong)]">{entry.invoice_no ?? `#${entry.id}`}</span>
          <Confidence value={entry.confidence} />
        </div>
        <div className="truncate">{entry.party_ledger_name ?? entry.party_name_raw ?? "Unknown party"}</div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px]">
          <span className="mono font-semibold">{money(entry.grand_total, entry.currency)}</span>
          <span className="faint">{shortDate(entry.invoice_date)}</span>
          <span className="faint">{entry.voucher_type}</span>
          {company !== undefined && (
            company ? <span className="faint">{company}</span> : <span className="chip chip-warn">Choose company</span>
          )}
          {entry.error_count > 0 && (
            <span className="chip chip-bad">{entry.error_count} issue{entry.error_count === 1 ? "" : "s"}</span>
          )}
          {(entry.ai_state === "waiting" || entry.ai_state === "reading") && <span className="chip chip-info">AI checking</span>}
        </div>
      </button>
    </li>
  );
}
