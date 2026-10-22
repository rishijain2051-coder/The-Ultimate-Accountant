"use client";

import { API_BASE, api, shortDate } from "@/lib/api";
import { Banner, Spinner, useAsync } from "@/components/ui";
import { useEffect, useRef, useState } from "react";

export default function SettingsPage() {
  const health = useAsync(() => api.health(), []);
  const companies = useAsync(() => api.companies(), []);
  const settings = useAsync(() => api.settings(), []);
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ tone: "good" | "warn" | "bad"; text: string } | null>(null);

  useEffect(() => { if (settings.data) setForm(settings.data); }, [settings.data]);

  const run = async (key: string, fn: () => Promise<{ message: string }>) => {
    setBusy(key); setNote(null);
    try {
      const r = await fn();
      setNote({ tone: "good", text: r.message });
      companies.reload(); health.reload();
    } catch (e) { setNote({ tone: "bad", text: (e as Error).message }); }
    finally { setBusy(null); }
  };

  const save = (patch?: Record<string, unknown>) => run("save", async () => {
    const next = { ...form, ...patch };
    setForm(await api.saveSettings(next));
    return { message: "Saved. Changes apply immediately." };
  });

  const f = (k: string) => String(form[k] ?? "");
  const connected = !!health.data?.tally.connected;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <h1 className="title rise">Settings</h1>
      <p className="muted">
        For the Invoice Bridge. Updates, Assurance Console&apos;s data folder, and closing the app are
        in <a className="link" href="/console/#/settings">Assurance Console&apos;s Settings</a>.
      </p>
      {note && <Banner tone={note.tone} onClose={() => setNote(null)}>{note.text}</Banner>}

      <div className="stagger grid gap-4 md:grid-cols-2">
      <Card wide title="Tally" status={connected ? (
        <span className="flex items-center gap-2">
          <span className="mono text-[12px] muted">{health.data?.tally.url}</span><span className="chip chip-good">Connected</span>
        </span>
      ) : <span className="chip">Not connected</span>}>
          {!connected && health.data?.tally.error && <p className="muted">{health.data.tally.error}</p>}

          <div className="grid grid-cols-[1fr_140px] gap-3">
            <label><span className="label">Host</span>
              <input className="field mono" value={f("tally_host")} onChange={(e) => setForm({ ...form, tally_host: e.target.value })} />
            </label>
            <label><span className="label">Port</span>
              <input className="field mono" value={f("tally_port")} onChange={(e) => setForm({ ...form, tally_port: e.target.value })} />
            </label>
          </div>

          <div className={`rounded-lg border p-3.5 ${form.tally_allow_post ? "border-transparent bg-[var(--good-soft)]" : "hairline"}`}>
            <label className="flex items-start gap-3">
              <input type="checkbox" className="mt-1 h-4 w-4" checked={!!form.tally_allow_post}
                onChange={(e) => save({ tally_allow_post: e.target.checked })} />
              <span>
                <b>Allow this app to write to Tally</b>
                <span className="mt-0.5 block muted">
                  Needed for “Post to Tally”, “Save as draft in Tally”, and creating missing ledgers. Every write
                  still asks you first. With this off the app only reads from Tally and exports XML files.
                </span>
              </span>
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button className="btn" onClick={() => save()} disabled={busy === "save"}>Save connection</button>
            <button className="btn btn-primary" disabled={busy === "discover"}
              onClick={() => run("discover", () => api.discover())}>
              {busy === "discover" ? "Looking…" : "Discover companies"}
            </button>
          </div>

          {companies.loading ? <Spinner /> : (
            <div className="overflow-hidden rounded-lg border hairline">
              <table className="tbl">
                <thead><tr><th>Company</th><th>GSTIN</th><th className="text-right">Ledgers</th><th className="text-right">Items</th><th>Synced</th><th /></tr></thead>
                <tbody>
                  {companies.data?.map((c) => (
                    <tr key={c.id}>
                      <td className="font-semibold">{c.tally_name}</td>
                      <td className="mono">{c.gstin ?? "—"}</td>
                      <td className="mono text-right">{c.ledger_count}</td>
                      <td className="mono text-right">{c.stock_count}</td>
                      <td className="muted">{c.last_synced_at ? shortDate(c.last_synced_at) : "never"}</td>
                      <td className="text-right">
                        <button className="btn !py-1" disabled={busy === `sync${c.id}`}
                          onClick={() => run(`sync${c.id}`, () => api.sync(c.id))}>
                          {busy === `sync${c.id}` ? "Syncing…" : "Sync"}
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!companies.data?.length && (
                    <tr><td colSpan={6} className="muted">No companies yet. Open your company in Tally, then “Discover companies”.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-[12px] faint">
            Sync copies ledgers, stock items and your voucher types to this computer, so reviewing and matching
            work with Tally closed, and learns from your own vouchers which item you buy from whom. Sync again
            after adding masters in Tally.
          </p>
      </Card>

      <Card title="Bills in Tally" status={form.bill_links ? <span className="chip chip-good">Linked</span> : <span className="chip">Off</span>}>
          <p className="muted">
            Tally cannot keep a file inside a voucher, so the voucher gets a link to its bill instead: it shows
            under the narration, and <kbd>Ctrl+Alt+O</kbd> opens the bill.
          </p>
          <ol className="list-decimal space-y-1 pl-5 text-[12.5px]">
            <li>
              <a className="link" href={`${API_BASE}/api/bill-links/addon`}>Save the Tally add-on</a>
              {" "}and copy it to the computer where you work in Tally.
            </li>
            <li>In Tally: F1 (Help) &gt; TDL &amp; Add-On &gt; F4 Manage Local TDLs. Set “Load TDL files on startup”
              to Yes and add the file&apos;s full path.</li>
            <li>Turn links on here. Bills posted from then on carry their link.</li>
          </ol>
          <label className="flex items-start gap-3 rounded-lg border hairline p-3">
            <input type="checkbox" className="mt-1 h-4 w-4" checked={!!form.bill_links}
              onChange={(e) => save({ bill_links: e.target.checked })} />
            <span>
              <b>Link each bill from its voucher</b>
              <span className="mt-0.5 block muted">
                This computer serves the bills to your network at{" "}
                <span className="mono">{f("bill_link_base") || "…"}</span>, read-only, each under a link that cannot
                be guessed. It must be on when a bill is opened from Tally. Windows may ask once to allow the app on
                your private network: allow it.
              </span>
            </span>
          </label>
          {typeof form.bill_links_problem === "string" && (
            <p className="text-[12.5px] text-[var(--bad)]">Links cannot be served: {form.bill_links_problem}.</p>
          )}
      </Card>

      <Card title="Local AI model" status={health.data?.ollama.available ? (
        <span className="flex items-center gap-2">
          <span className="mono text-[12px] muted">{health.data.ollama.model}</span><span className="chip chip-good">Ready</span>
        </span>
      ) : <span className="chip">Not available</span>}>
          <p className="muted">
            Optional. The AI only reads: it copies off the page what the rules could not find (handwriting and
            Hindi included), exactly as written, and reads each scan a second time to check the OCR. It never
            translates, calculates, guesses, answers questions or decides anything, never overrides a value read by
            the rules, and everything it read is marked in amber for you to confirm.
          </p>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!form.llm_enabled} onChange={(e) => setForm({ ...form, llm_enabled: e.target.checked })} />
            Use the AI to read what the rules miss
          </label>
          {health.data?.ollama.setup?.can_install || health.data?.ollama.setup?.running ? (
            <AiInstall onDone={health.reload} />
          ) : health.data && !health.data.ollama.available && health.data.ollama.reason ? (
            <p className="text-[12px] faint">{health.data.ollama.reason}</p>
          ) : health.data?.ollama.setup?.shared && (
            <p className="text-[12px] faint">
              Shared with {health.data.ollama.setup.shared.app}: this app runs the AI reader it downloaded
              (<span className="mono">{health.data.ollama.setup.shared.folder}</span>), so it is on this computer
              once, not twice.
            </p>
          )}
          <p className="text-[12px] faint">
            The same AI is Assurance Console&apos;s AI checker: one download serves both.
          </p>
      </Card>

      <Card title="Review & watched folder">
          <label className="block max-w-xs"><span className="label">Count an entry as clean at or above</span>
            <input className="field mono" value={f("auto_clean_confidence")}
              onChange={(e) => setForm({ ...form, auto_clean_confidence: e.target.value })} />
          </label>
          <p className="text-[12px] faint">
            Clean entries can be exported together from Review. An entry with a failed check, a likely duplicate
            or anything the AI read is never clean, whatever it scores.
          </p>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!form.watch_enabled} onChange={(e) => setForm({ ...form, watch_enabled: e.target.checked })} />
            Read anything dropped into the watched folder
          </label>
          <p className="mono text-[11.5px] faint">{f("watch_dir")}</p>
      </Card>

      <Card wide title="Data & backup">
          <p className="muted">
            Everything lives in <span className="mono">{f("data_dir")}</span>: the database, the original documents
            and exported XML. The app backs up every six hours and keeps the last 14.
          </p>
          <button className="btn" disabled={busy === "backup"} onClick={() => run("backup", () => api.backup())}>
            {busy === "backup" ? "Backing up…" : "Back up now"}
          </button>
          <ul className="mono text-[11.5px] faint">
            {health.data?.database.backups?.map((b) => <li key={b.file}>{b.file} · {b.created_at}</li>)}
          </ul>
      </Card>
      </div>

      <div className="flex justify-end">
        <button className="btn btn-primary" onClick={() => save()} disabled={busy === "save"}>
          {busy === "save" ? "Saving…" : "Save settings"}
        </button>
      </div>
    </div>
  );
}

/** The AI reader, when setup could not get it: a button, the same download
 *  setup does, into the app's folder (backend services/ai_setup.py). */
function AiInstall({ onDone }: { onDone: () => void }) {
  const s = useAsync(() => api.aiSetup(), []);
  const d = s.data;
  const running = !!d?.running;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(s.reload, 2000);
    return () => clearInterval(t);
  }, [running, s.reload]);
  // Done: the card asks again, and says the AI is ready.
  const was = useRef(false);
  useEffect(() => {
    if (was.current && !running) onDone();
    was.current = running;
  }, [running, onDone]);
  if (!d || (!d.can_install && !running)) return null;

  return (
    <div className="rounded-lg border hairline bg-[var(--panel-2)] p-4">
      {running ? (
        <>
          <p className="font-medium text-[var(--ink-strong)]">
            {d.step ?? "Starting the download"}{d.percent !== null ? ` · ${Math.round(d.percent)}%` : "…"}
          </p>
          <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-[var(--neutral-soft)]">
            <div className="h-full rounded-full bg-[var(--ink-strong)] transition-[width] duration-500"
              style={{ width: `${d.percent ?? 2}%` }} />
          </div>
          <p className="mt-2.5 text-[12px] muted">
            Keep the app open until it is done: the AI switches on by itself. If the app is closed, press Install
            again and it carries on where it stopped.
          </p>
        </>
      ) : (
        <>
          <p className="font-medium text-[var(--ink-strong)]">The AI reader is not installed on this computer.</p>
          <p className="mt-1 text-[12.5px] muted">
            About 5 GB to download, once, into the app&apos;s folder (<span className="mono">{d.folder}</span>).
            The app keeps working meanwhile.
          </p>
          {d.error && <p className="mt-2 text-[12.5px] text-[var(--bad)]">{d.error}</p>}
          <button className="btn btn-primary mt-3" onClick={() => api.aiInstall().then(s.setData, () => {})}>
            {d.error ? "Try again" : "Install the AI"}
          </button>
        </>
      )}
    </div>
  );
}

/** One part of the settings, as a card: its name, how it stands, and what to set. */
function Card({ title, status, wide, children }: {
  title: string; status?: React.ReactNode; wide?: boolean; children: React.ReactNode;
}) {
  return (
    <section className={`panel ${wide ? "md:col-span-2" : ""}`}>
      <div className="flex flex-wrap items-center justify-between gap-2 px-6 pt-5">
        <h2 className="heading">{title}</h2>
        {status}
      </div>
      <div className="space-y-4 px-6 pb-6 pt-4">{children}</div>
    </section>
  );
}
