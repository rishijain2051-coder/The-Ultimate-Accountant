"use client";

import { shortDate } from "@/lib/api";
import { bank, type Listed } from "@/lib/bank";
import { BankStatement } from "@/components/BankStatement";
import { Boundary } from "@/components/Boundary";
import { Banner, Spinner, useAsync } from "@/components/ui";
import { Suspense, useEffect, useRef, useState } from "react";

export default function BankPage() {
  return <Suspense fallback={<Spinner />}><Bank /></Suspense>;
}

const reading = (s: Listed) => s.status === "queued" || s.status === "processing";

function Bank() {
  const list = useAsync(() => bank.list(), []);
  const [selected, setSelected] = useState<number | null>(null);
  const [banner, setBanner] = useState<{ tone: "info" | "bad"; text: string } | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const busy = (list.data ?? []).some(reading);

  // A scan takes a minute or two to read: look again until it is done.
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(list.reload, 3000);
    return () => clearInterval(t);
  }, [busy, list.reload]);

  // The newest statement read, until you pick one.
  const shown = selected ?? (list.data ?? []).find((s) => s.id !== null)?.id ?? null;

  const upload = async (f: File | undefined) => {
    if (!f) return;
    setBanner(null);
    try {
      const r = await bank.upload(f);
      setBanner({ tone: "info", text: r.message });
      if (r.statement_id) setSelected(r.statement_id);
      list.reload();
    } catch (e) {
      setBanner({ tone: "bad", text: (e as Error).message });
    } finally {
      if (file.current) file.current.value = "";
    }
  };

  return (
    <div className="stagger mx-auto grid max-w-[1800px] gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="panel h-fit">
        <div className="section-title">
          <span className="heading !text-[17px]">Bank statements</span>
          <button className="btn btn-primary !px-2.5 !py-1 !text-[12px]" onClick={() => file.current?.click()}>
            Upload
          </button>
          <input ref={file} type="file" accept=".pdf,application/pdf" className="hidden"
            onChange={(e) => upload(e.target.files?.[0])} />
        </div>
        <div className="divide-y divide-[var(--edge)]">
          {list.loading && !list.data && <p className="p-4"><Spinner /></p>}
          {list.data?.length === 0 && (
            <p className="p-4 text-[12.5px] muted">
              Upload a statement&apos;s PDF, downloaded or scanned. Every line is read as the bank printed it, and
              posted to Tally only when you approve it.
            </p>
          )}
          {(list.data ?? []).map((s) => (
            <button key={s.document_id} disabled={s.id === null}
              onClick={() => s.id !== null && setSelected(s.id)}
              className={`block w-full px-4 py-3 text-left ${s.id !== null && s.id === shown ? "bg-[var(--active)]" : "row-hover"}`}>
              <span className="block truncate font-semibold text-[var(--ink-strong)]">{s.id !== null ? `${s.bank ?? "Statement"} · ${s.account_no ?? "?"}` : s.filename}</span>
              {s.id !== null ? (
                <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12px] muted">
                  {shortDate(s.period_from)} – {shortDate(s.period_to)} · {s.count} rows
                  {s.by_status.pending > 0 && <span className="chip chip-warn">{s.by_status.pending} to post</span>}
                </span>
              ) : (
                <span className={`block text-[12px] ${s.status === "failed" ? "text-[var(--bad)]" : "muted"}`}>
                  {reading(s) ? "Reading… a scan takes a minute or two" : s.error ?? s.status}
                </span>
              )}
            </button>
          ))}
        </div>
      </aside>
      <section className="min-w-0 space-y-3">
        {banner && <Banner tone={banner.tone} onClose={() => setBanner(null)}>{banner.text}</Banner>}
        {list.error && <Banner tone="bad">{list.error}</Banner>}
        {shown !== null ? (
          <Boundary key={shown} what="statement"><BankStatement id={shown} onChanged={list.reload} /></Boundary>
        ) : (
          !list.loading && <p className="panel dots p-16 text-center muted">No statement open.</p>
        )}
      </section>
    </div>
  );
}
