"use client";

import { catchError, type ErrorInfo } from "next/error";

/** One part of the screen that failed shows why, with Try again, instead of
 *  taking the whole page down with it ("This page couldn't load"). */
function Fallback({ what }: { what: string }, { error, reset }: ErrorInfo) {
  return (
    <div className="panel space-y-2 p-5">
      <p className="font-semibold text-[var(--bad)]">This {what} could not be shown.</p>
      <p className="mono text-[12px] muted">{error instanceof Error ? error.message : String(error)}</p>
      <button className="btn" onClick={() => reset()}>Try again</button>
    </div>
  );
}

export const Boundary = catchError(Fallback);
