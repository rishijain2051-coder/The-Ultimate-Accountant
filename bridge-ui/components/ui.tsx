"use client";

import { useEffect, useState } from "react";
import type { FieldMeta, Severity, Validation } from "@/lib/api";

/** How sure the app is of an entry. Quiet: most scans read below 90%, so
 *  only a sure one is coloured; problems are said by the checks, in red. */
export function Confidence({ value, label = false }: { value: number; label?: boolean }) {
  const pct = Math.round(value * 100);
  return (
    <span className={`mono text-[11.5px] ${value >= 0.9 ? "text-[var(--good)]" : "faint"}`}
      title={`The app is ${pct}% sure of this entry`}>
      {pct}%{label ? " sure" : ""}
    </span>
  );
}

const PATHS = {
  check: "M3.5 8.5l3 3 6-7",
  alert: "M8 4.5v4M8 11.2v.3M8 1.8l6.5 11.4H1.5z",
  x: "M4 4l8 8M12 4l-8 8",
  undo: "M5.5 5.5H10a3 3 0 010 6H6.5M5.5 5.5L8 3M5.5 5.5L8 8",
  chevron: "M4 6l4 4 4-4",
  plus: "M8 3.5v9M3.5 8h9",
} as const;

/** The few marks the screens use, drawn in one stroke. */
export function Icon({ name, className = "", title }: { name: keyof typeof PATHS; className?: string; title?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round"
      strokeLinejoin="round" className={`icon ${className}`} aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}>
      {title && <title>{title}</title>}
      <path d={PATHS[name]} />
    </svg>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  regex: "Read directly off the page",
  llm: "Filled in by the local AI model - check it",
  ocr: "Read by OCR from a scan",
  inferred: "Worked out by arithmetic",
  user: "You set this",
  computed: "Computed",
  none: "Not found",
};

/** Wrap a field label. Hover shows where the value came from and how sure. */
export function Evidence({ meta, children }: { meta?: FieldMeta | null; children: React.ReactNode }) {
  if (!meta) return <>{children}</>;
  const pct = Math.round((meta.confidence ?? 0) * 100);
  return (
    <span className="evi">
      <span className="inline-flex items-center gap-1">
        {children}
        <span className={`inline-block h-1.5 w-1.5 rounded-full ${
          meta.source === "llm" ? "bg-[var(--warn)]" : meta.confidence >= 0.9 ? "bg-[var(--good)]" : "bg-[var(--faint)]"
        }`} />
      </span>
      <span className="evi-pop">
        <span className="block font-bold">{SOURCE_LABEL[meta.source] ?? meta.source} · {pct}%</span>
        {meta.evidence ? (
          <span className="mono mt-1 block whitespace-pre-wrap break-words muted">“{meta.evidence}”</span>
        ) : (
          <span className="mt-1 block muted">No source line recorded.</span>
        )}
      </span>
    </span>
  );
}

export function SeverityDot({ severity }: { severity: Severity }) {
  const map = { ok: "var(--good)", warn: "var(--warn)", error: "var(--bad)" } as const;
  return <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: map[severity] }} />;
}

export function Checks({ items }: { items: Validation[] }) {
  const [showOk, setShowOk] = useState(false);
  const shown = items.filter((v) => !["duplicate", "similar_bill"].includes(v.code));
  const bad = shown.filter((v) => v.severity !== "ok");
  const good = shown.filter((v) => v.severity === "ok");
  if (!shown.length) return <p className="muted">No checks ran.</p>;

  return (
    <div className="space-y-1.5">
      {bad.length === 0 && (
        <p className="flex items-center gap-2 font-semibold text-[var(--good)]">
          <SeverityDot severity="ok" /> All {good.length} checks passed
        </p>
      )}
      {[...bad, ...(showOk ? good : [])].map((v, i) => (
        <div key={`${v.code}-${i}`} className="flex gap-2">
          <span className="pt-1.5"><SeverityDot severity={v.severity} /></span>
          <span className={v.severity === "ok" ? "muted" : ""}>
            {v.message}
            {v.expected != null && v.severity !== "ok" && (
              <span className="mono ml-1 faint">(expected {v.expected}{v.actual != null && <>, found {v.actual}</>})</span>
            )}
          </span>
        </div>
      ))}
      {good.length > 0 && (
        <button className="link text-[12px]" onClick={() => setShowOk(!showOk)}>
          {showOk ? "Hide" : "Show"} {good.length} passing check{good.length === 1 ? "" : "s"}
        </button>
      )}
    </div>
  );
}

export function Banner({ tone, children, onClose }: {
  tone: "info" | "good" | "warn" | "bad"; children: React.ReactNode; onClose?: () => void;
}) {
  return (
    <div className={`note note-${tone}`}>
      {tone !== "info" && <Icon name={tone === "good" ? "check" : "alert"} className="mt-[3px] text-[var(--tone)]" />}
      <div className="min-w-0 flex-1">{children}</div>
      {onClose && (
        <button onClick={onClose} className="mt-0.5 rounded muted hover:text-[var(--ink)]" aria-label="Dismiss">
          <Icon name="x" />
        </button>
      )}
    </div>
  );
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    fn()
      .then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e) => { if (live) setError(e?.message ?? String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { data, error, loading, reload: () => setTick((t) => t + 1), setData };
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 muted">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[var(--edge-strong)] border-t-[var(--ink-strong)]" />
      {label}…
    </span>
  );
}
