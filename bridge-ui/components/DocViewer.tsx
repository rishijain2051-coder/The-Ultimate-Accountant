"use client";

import { api } from "@/lib/api";
import { useEffect, useState } from "react";

const ZOOMS = [
  { key: "fit", label: "Fit" },
  { key: "125", label: "125%" },
  { key: "175", label: "175%" },
] as const;
type Zoom = (typeof ZOOMS)[number]["key"];

/**
 * The source document, as page images rendered by the backend. No browser
 * PDF plugin involved, so it shows the same everywhere.
 */
export function DocViewer({ documentId, filename }: { documentId: number; filename?: string | null }) {
  const [count, setCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState<Zoom>("fit");

  useEffect(() => {
    setCount(null); setError(null);
    api.pageCount(documentId).then((r) => setCount(r.count)).catch((e) => setError(e.message));
  }, [documentId]);

  const width = zoom === "fit" ? "100%" : `${zoom}%`;

  return (
    <div className="panel flex flex-col overflow-hidden">
      <div className="section-title">
        <span className="truncate">{filename ?? "Document"}</span>
        <span className="flex shrink-0 items-center gap-2">
          <span className="tabs">
            {ZOOMS.map((z) => (
              <button key={z.key} onClick={() => setZoom(z.key)} aria-pressed={zoom === z.key}
                className="tab !px-2 !py-0.5 text-[12px]">
                {z.label}
              </button>
            ))}
          </span>
          <a className="btn btn-ghost !px-2 !py-0.5 !text-[12px]" href={api.documentUrl(documentId)}
            target="_blank" rel="noreferrer" title="Open the original file in a new window">Original</a>
        </span>
      </div>
      <div className="h-[calc(100vh-150px)] min-h-[560px] overflow-auto bg-[var(--hover)] p-3">
        {error && <p className="p-4 text-[var(--bad)]">{error}</p>}
        {count === null && !error && <p className="p-4 muted">Loading pages…</p>}
        <div className="space-y-3" style={{ width }}>
          {Array.from({ length: count ?? 0 }, (_, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={i} src={api.pageUrl(documentId, i + 1)} alt={`Page ${i + 1}`} loading={i === 0 ? "eager" : "lazy"}
              className="block w-full rounded border border-[var(--edge)] bg-white" />
          ))}
        </div>
        {count !== null && count > 1 && <p className="mt-2 text-center text-[11.5px] faint">{count} pages</p>}
      </div>
    </div>
  );
}
