"use client";

import { api, type Company, type PhoneStatus, type VoucherTypeOption } from "@/lib/api";
import { Banner, Icon, Spinner, useAsync } from "@/components/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

type Step = "files" | "type" | "reading";
type Row = { name: string; state: string; doc?: number | null; entry?: number | null };

const GROUPS: { title: string; hint: string; parents: string[] }[] = [
  { title: "Bills", hint: "An invoice with GST", parents: ["Purchase", "Sales", "Debit Note", "Credit Note"] },
  { title: "Bank & cash", hint: "Money in or out", parents: ["Payment", "Receipt", "Contra"] },
  { title: "Adjustments", hint: "Anything else", parents: ["Journal"] },
  { title: "Orders & notes", hint: "Before the bill", parents: ["Purchase Order", "Sales Order", "Receipt Note", "Delivery Note"] },
];

export default function UploadPage() {
  const router = useRouter();
  const health = useAsync(() => api.health(), []);
  const companies = useAsync(() => api.companies(), []);
  const [companyId, setCompanyId] = useState<number | undefined>();
  const types = useAsync(() => api.voucherTypes(companyId), [companyId]);

  const [step, setStep] = useState<Step>("files");
  const [files, setFiles] = useState<File[]>([]);
  const [chosen, setChosen] = useState<VoucherTypeOption | null>(null);
  const [dragging, setDragging] = useState(false);
  const [eachPage, setEachPage] = useState(false);
  const [progress, setProgress] = useState<Row[]>([]);
  const [note, setNote] = useState<{ tone: "warn" | "bad"; text: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  // Leaving this screen stops the waiting here; the reading goes on, and
  // the Review screen picks each bill up as it is ready.
  const here = useRef(true);
  useEffect(() => { here.current = true; return () => { here.current = false; }; }, []);

  const grouped = useMemo(() => {
    const all = types.data?.types ?? [];
    return GROUPS.map((g) => ({ ...g, items: all.filter((t) => g.parents.includes(t.parent)) }))
      .filter((g) => g.items.length);
  }, [types.data]);
  const hasPdf = files.some((f) => /\.pdf$/i.test(f.name));

  const addFiles = (list: File[]) => {
    const ok = list.filter((f) => /\.(pdf|png|jpe?g|tiff?|bmp|webp)$/i.test(f.name));
    if (!ok.length) {
      setNote({ tone: "warn", text: "Those files aren't PDFs or images." });
      return;
    }
    setNote(null);
    setFiles((prev) => [...prev, ...ok]);
    setStep("type");
  };

  const read = async () => {
    if (!chosen || !files.length) return;
    setStep("reading");
    setProgress(files.map((f) => ({ name: f.name, state: "Uploading" })));
    let rows: Row[];
    try {
      const res = await api.upload(files, { companyId, voucherType: chosen.name, voucherParent: chosen.parent,
                                            eachPage: eachPage && hasPdf });
      rows = res.results.map((r) => ({
        name: r.filename,
        doc: r.ok && !r.duplicate_file ? r.document_id ?? null : null,
        entry: r.duplicate_file ? r.entry_id ?? null : null,
        state: !r.ok ? `Rejected: ${r.error}` : r.duplicate_file ? "Already uploaded" : "Reading",
      }));
    } catch (e) {
      setNote({ tone: "bad", text: (e as Error).message });
      setStep("type");
      return;
    }
    setProgress(rows);

    // Wait here while you watch; each bill can be reviewed as soon as it is
    // ready. A scan can take minutes on a PC without a GPU, so no time limit.
    while (here.current) {
      const waiting = rows.filter((r) => r.doc && r.state === "Reading").map((r) => r.doc!);
      if (!waiting.length) break;
      await new Promise((r) => setTimeout(r, 1500));
      let now;
      try { now = new Map((await api.documents(waiting)).map((d) => [d.id, d])); } catch { continue; }
      rows = rows.map((r) => {
        const d = r.doc && r.state === "Reading" ? now.get(r.doc) : undefined;
        if (!d) return r;
        if (d.status === "failed") return { ...r, state: `Failed: ${d.error}` };
        return d.entry_id ? { ...r, entry: d.entry_id, state: "Ready" } : r;
      });
      if (here.current) setProgress(rows);
    }
    if (!here.current) return;  // gone to Review already

    const ready = rows.filter((r) => r.entry).map((r) => r.entry!);
    if (ready.length === 1) router.push(`/review/?id=${ready[0]}`);
    else if (ready.length) router.push("/review/");
    else setNote({ tone: "bad", text: "Nothing could be read from those files." });
  };

  const reset = () => {
    setFiles([]); setChosen(null); setStep("files"); setProgress([]); setNote(null); setEachPage(false);
  };
  const readyCount = progress.filter((p) => p.entry).length;
  const stillReading = progress.filter((p) => p.state === "Reading" || p.state === "Uploading").length;

  return (
    <div className="stagger mx-auto max-w-4xl space-y-5">
      {!health.loading && !health.data?.tally.connected && (
        <Banner tone="warn">
          <b>Tally isn&apos;t connected.</b> You can still upload and review. To post, open Tally and turn on{" "}
          <i>F1 &gt; Settings &gt; Connectivity &gt; Client/Server configuration &gt; TallyPrime acts as: Both</i>
          {" "}(port 9000), then <Link className="link" href="/settings/">sync in Settings</Link>.
        </Banner>
      )}
      {note && <Banner tone={note.tone} onClose={() => setNote(null)}>{note.text}</Banner>}

      <Steps step={step} />

      {step === "files" && (
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(Array.from(e.dataTransfer.files)); }}
          className={`dots grid place-items-center rounded-[var(--radius-panel)] border border-dashed px-8 py-24 text-center transition-colors ${
            dragging ? "border-[var(--ink-strong)] bg-[var(--hover)]" : "border-[var(--edge-strong)] bg-[var(--panel)]"}`}>
          <input ref={input} type="file" multiple hidden accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp"
            onChange={(e) => { addFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
          <h1 className="title max-w-lg">Drop an invoice, bill or bank advice</h1>
          <p className="mt-3 muted">PDF or photo · one file or a batch · nothing leaves this computer</p>
          <button className="btn btn-primary mt-7" onClick={() => input.current?.click()}>Choose files</button>
          {health.data?.watch.enabled && (
            <p className="mono mt-6 text-[11px] faint">or drop them into {health.data.watch.folder}</p>
          )}
        </div>
      )}
      {step === "files" && <Phone />}

      {step !== "files" && (
        <div className="panel flex items-center gap-2 px-4 py-3">
          <span className="shrink-0 font-semibold">{files.length} file{files.length === 1 ? "" : "s"}:</span>
          <span className="min-w-0 truncate muted" title={files.map((f) => f.name).join("\n")}>
            {files.slice(0, 6).map((f) => f.name).join(", ")}{files.length > 6 ? `, and ${files.length - 6} more` : ""}
          </span>
          {step === "type" && <button className="btn btn-ghost ml-auto shrink-0" onClick={reset}>Change</button>}
        </div>
      )}

      {step === "type" && (
        <div className="panel p-6">
          <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="heading">What is this document?</h2>
              <p className="mt-1 muted">This decides the voucher type in Tally and which side each amount posts to.</p>
            </div>
            {(companies.data?.length ?? 0) > 1 && (
              <label className="min-w-72">
                <span className="label">Company</span>
                <select className="field" value={companyId ?? ""}
                  onChange={(e) => setCompanyId(e.target.value ? Number(e.target.value) : undefined)}>
                  <option value="">From your GSTIN on each bill</option>
                  {companies.data!.map((c: Company) => (
                    <option key={c.id} value={c.id}>{c.tally_name}{c.gstin ? ` · ${c.gstin}` : ""}</option>
                  ))}
                </select>
                <span className="mt-1 block text-[11.5px] faint">
                  {companyId ? "A bill made out to another of your companies' GSTINs still goes to that company."
                             : "A bill without your GSTIN (a kachcha bill) asks you which company, on review."}
                </span>
              </label>
            )}
          </div>

          {hasPdf && (
            <label className="mb-5 flex items-start gap-2.5 rounded-lg border hairline bg-[var(--panel-2)] p-3.5">
              <input type="checkbox" className="mt-0.5 h-4 w-4" checked={eachPage}
                onChange={(e) => setEachPage(e.target.checked)} />
              <span>
                <b>Each page is a separate bill</b>
                <span className="block text-[12px] muted">
                  For a scan of several bills in one PDF. Leave it off when one bill runs over several pages.
                </span>
              </span>
            </label>
          )}

          {types.loading ? <Spinner /> : (
            <div className="space-y-6">
              {grouped.map((g) => (
                <div key={g.title}>
                  <p className="mb-2.5 text-[12.5px]">
                    <span className="font-semibold text-[var(--ink-strong)]">{g.title}</span> <span className="muted">· {g.hint}</span>
                  </p>
                  <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
                    {g.items.map((t) => {
                      const on = chosen?.name === t.name;
                      return (
                        <button key={t.name} onClick={() => setChosen(t)} aria-pressed={on} className="option">
                          <span className="font-semibold text-[var(--ink-strong)]">{t.name}</span>
                          <span className="text-[12px] muted">
                            {t.custom ? `Your Tally type · works like ${t.parent}` : t.label.split(" - ")[1] ?? t.label}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
              {types.data?.source === "default" && (
                <p className="text-[12px] faint">
                  Showing Tally&apos;s standard types. Sync from Settings to see your own custom voucher types here.
                </p>
              )}
            </div>
          )}

          <div className="mt-6 flex items-center justify-end gap-3 border-t hairline pt-5">
            {chosen && <span className="muted">Will be entered as <b className="text-[var(--ink)]">{chosen.name}</b></span>}
            <button className="btn btn-primary" disabled={!chosen} onClick={read}>
              Read {files.length === 1 ? "document" : `${files.length} documents`}
            </button>
          </div>
        </div>
      )}

      {step === "reading" && (
        <div className="panel p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="heading">{stillReading ? "Reading…" : "Done"}</h2>
              {readyCount > 0 && stillReading > 0 && (
                <p className="muted">Start on the ready ones now. The rest keep being read and join the list as they finish.</p>
              )}
            </div>
            {readyCount > 0 && stillReading > 0 && (
              <Link className="btn btn-primary" href="/review/">Review the {readyCount} ready</Link>
            )}
          </div>
          <ul className="space-y-2">
            {progress.map((p, i) => (
              <li key={i} className="flex items-center justify-between gap-3">
                <span className="truncate">{p.name}</span>
                {p.state === "Reading" || p.state === "Uploading" ? <Spinner label={p.state} />
                  : p.state === "Ready" ? <span className="chip chip-good">Ready</span>
                  : p.state === "Already uploaded" ? <span className="chip">Already uploaded</span>
                  : <span className="min-w-0 truncate text-right text-[var(--bad)]" title={p.state}>{p.state}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** A code for the phone: it opens a page on the same Wi-Fi where each bill is
 *  photographed, said what it is and sent here (backend services/phone.py). */
function Phone() {
  const s = useAsync(() => api.phone(), []);
  const [error, setError] = useState<string | null>(null);
  const d = s.data;
  const on = !!d?.on;
  // While the code is up: what has come from the phone, and whether it still works.
  useEffect(() => {
    if (!on) return;
    const t = setInterval(s.reload, 3000);
    return () => clearInterval(t);
  }, [on, s.reload]);
  const run = (fn: () => Promise<PhoneStatus>) => {
    setError(null);
    fn().then(s.setData, (e) => setError((e as Error).message));
  };
  const ready = (d?.sent ?? []).filter((x) => x.state === "ready").length;

  return (
    <div className="panel p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-xl">
          <h2 className="heading">Send bills from your phone</h2>
          <p className="mt-1 muted">
            Scan the code with your phone&apos;s camera. On the page it opens, photograph a bill, say what it is and
            send it: it is read here as if you had uploaded it, and waits in Review.
          </p>
        </div>
        {!on && <button className="btn btn-primary" onClick={() => run(api.phoneStart)}>Show the code</button>}
      </div>
      {(error || d?.problem) && <div className="mt-4"><Banner tone="bad">{error ?? d?.problem}</Banner></div>}
      {on && d?.qr && d.url && (
        <div className="mt-5 flex flex-wrap items-start gap-6">
          {/* Dark on white in either theme: that is what a phone's camera reads. */}
          <svg viewBox={`0 0 ${d.qr.size} ${d.qr.size}`} role="img" aria-label="QR code for your phone"
            className="h-52 w-52 shrink-0 rounded-lg border hairline bg-white p-2" shapeRendering="crispEdges">
            <path d={d.qr.path} fill="#111111" />
          </svg>
          <div className="min-w-0 flex-1 basis-64 space-y-3">
            <div>
              <span className="label">Or type this into the phone&apos;s browser</span>
              <span className="mono select-all break-all text-[var(--ink-strong)]">{d.url}</span>
            </div>
            <p className="text-[12.5px] muted">
              The phone must be on the same Wi-Fi as this computer. If Windows asks whether to let the app on
              private networks, allow it. This code works until {d.until?.slice(11, 16)}, or until you stop it.
            </p>
            <div className="flex flex-wrap gap-2">
              <button className="btn" onClick={() => run(api.phoneStart)} title="A new code: this one stops working">
                New code
              </button>
              <button className="btn btn-ghost" onClick={() => run(api.phoneStop)}>Stop</button>
            </div>
          </div>
        </div>
      )}
      {!!d?.sent.length && (
        <div className="mt-6 border-t hairline pt-4">
          <div className="mb-1 flex items-center justify-between gap-3">
            <span className="font-semibold text-[var(--ink-strong)]">From your phone</span>
            {ready > 0 && <Link className="link text-[12.5px]" href="/review/">Review {ready} ready</Link>}
          </div>
          <ul className="divide-y divide-[var(--edge)]">
            {d.sent.map((x) => (
              <li key={x.id} className="flex items-center justify-between gap-3 py-2">
                <span className="min-w-0 truncate">
                  {x.type ?? "Bill"} <span className="mono text-[12px] faint">{x.at}</span>
                </span>
                {x.state === "ready" && x.entry_id ? (
                  <Link className="chip chip-good" href={`/review/?id=${x.entry_id}`}>Ready</Link>
                ) : x.state === "reading" ? <span className="chip chip-info">Reading</span>
                  : x.state === "failed" ? <span className="chip chip-bad" title={x.error ?? ""}>Not read</span>
                  : <span className="chip">{x.state}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Steps({ step }: { step: Step }) {
  const order: Step[] = ["files", "type", "reading"];
  const labels = ["Add document", "What is it?", "Review"];
  const at = order.indexOf(step);
  return (
    <ol className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
      {labels.map((l, i) => (
        <li key={l} className="flex items-center gap-3">
          {i > 0 && <span className="h-px w-8 bg-[var(--edge-strong)]" aria-hidden />}
          <span className={`flex items-center gap-2 whitespace-nowrap ${i === at ? "font-medium text-[var(--ink-strong)]" : "muted"}`}
            aria-current={i === at ? "step" : undefined}>
            {i < at ? <Icon name="check" className="text-[var(--good)]" />
              : <span className="mono text-[11.5px]">{String(i + 1).padStart(2, "0")}</span>}
            {l}
          </span>
        </li>
      ))}
    </ol>
  );
}
