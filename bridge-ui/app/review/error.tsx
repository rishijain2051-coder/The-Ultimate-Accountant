"use client";

import { useEffect } from "react";

// The review screen as a whole, if something outside a bill's own panel fails.
export default function ReviewError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => { console.error(error); }, [error]);
  return (
    <div className="mx-auto max-w-2xl space-y-3 panel p-6">
      <p className="text-base font-bold text-[var(--bad)]">The review screen could not be shown.</p>
      <p className="mono text-[12px] muted">{error.message}</p>
      <button className="btn btn-primary" onClick={() => retry()}>Try again</button>
    </div>
  );
}
