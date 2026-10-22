"use client";

import { api, type Health } from "@/lib/api";
import { useEffect, useState } from "react";

/** Always-visible answer to "is this thing actually connected to anything?" */
export function StatusBar() {
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    const load = () => {
      api.health().then((h) => { setHealth(h); setDown(false); }).catch(() => setDown(true));
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  if (down) return <span className="chip chip-bad">App backend offline</span>;
  if (!health) return null;

  // A dot says whether it is there: filled when it is, a ring when not.
  const part = (ok: boolean, label: string, title: string) => (
    <span className="inline-flex items-center gap-1.5 text-[12px] muted" title={title}>
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${ok ? "bg-[var(--good)]" : "border border-[var(--faint)]"}`} />
      {label}
    </span>
  );

  return (
    <div className="flex items-center gap-4">
      {health.tally.connected && health.tally.educational ? (
        <span className="chip chip-warn"
          title={`${health.tally.url}: Educational Mode, not licensed. It takes vouchers dated only the 1st, 2nd or 31st of a month.`}>
          Tally: Educational
        </span>
      ) : part(health.tally.connected, "Tally",
        health.tally.connected
          ? `Connected to ${health.tally.url} · ${(health.tally.company_names ?? []).join(", ")}`
          : health.tally.error ?? "Not connected")}
      {part(health.ollama.available, "AI",
        health.ollama.available ? `${health.ollama.model} ready` : health.ollama.reason ?? "Not available")}
      {part(health.ocr.available, "OCR", health.ocr.available ? "Local OCR ready for scans" : "OCR not installed")}
    </div>
  );
}
