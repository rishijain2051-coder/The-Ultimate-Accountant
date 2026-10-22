// Bank statements: routers/bank.py.

import { req, type Candidate } from "@/lib/api";

export interface RowLedger {
  ledger_id: number;
  name: string;
  amount: string;
}

export interface InTally {
  type: string | null;
  number: string | null;
  date: string | null;
  narration: string | null;
  ledgers: [string, string][];
  /** One line of a voucher that joins several (a payment with its charges). */
  part: boolean;
}

export type RowStatus = "pending" | "in_tally" | "posted" | "skipped";

export interface BankRow {
  id: number;
  row_no: number;
  serial: string | null;
  date: string | null;
  value_date: string | null;
  details: string | null;
  ref: string | null;
  /** Written by hand on the printed statement. */
  note: string | null;
  /** + money in, - money out. */
  amount: string | null;
  balance: string | null;
  page: number;
  /** Where the row is on its page, as fractions of the page's height. */
  top: number;
  bottom: number;
  /** ok: the running balance agrees; fixed: set by the balances; bad: check it. */
  check: "ok" | "fixed" | "first" | "bad";
  read_notes: string[];
  voucher_type: string | null;
  ledgers: RowLedger[];
  narration: string | null;
  confidence: number;
  reason: string | null;
  candidates: Candidate[];
  status: RowStatus;
  in_tally: InTally | null;
  posted_at: string | null;
}

export interface Statement {
  id: number;
  document_id: number;
  filename: string;
  status: string;
  error: string | null;
  bank: string | null;
  account_no: string | null;
  holder: string | null;
  period_from: string | null;
  period_to: string | null;
  opening: string | null;
  company_id: number | null;
  company: string | null;
  bank_ledger_id: number | null;
  bank_ledger: string | null;
  pages: number;
  tally_checked_at: string | null;
  tally_error: string | null;
  count: number;
  debits: string;
  credits: string;
  closing: string | null;
  by_status: Record<RowStatus, number>;
  unsure: number;
  rows?: BankRow[];
}

/** A statement still being read has no id yet: only its document's. */
export type Listed = Statement | { id: null; document_id: number; filename: string; status: string; error: string | null };

export interface PostResult {
  ok: boolean;
  row: number;
  message: string;
}

const send = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const bank = {
  list: () => req<Listed[]>("/api/bank/statements"),
  get: (id: number) => req<Statement>(`/api/bank/statements/${id}`),
  upload: (file: File, companyId?: number | null) => {
    const fd = new FormData();
    fd.append("file", file);
    if (companyId) fd.append("company_id", String(companyId));
    return req<{ document_id: number; statement_id: number | null; status: string; message: string }>(
      "/api/bank/statements", { method: "POST", body: fd });
  },
  setAccount: (id: number, body: { company_id?: number; bank_ledger_id?: number }) =>
    req<Statement>(`/api/bank/statements/${id}`, send("PATCH", body)),
  check: (id: number) => req<Statement>(`/api/bank/statements/${id}/check`, { method: "POST" }),
  createLedger: (id: number, body: { name: string; parent?: string; duty_head?: string }) =>
    req<{ id: number; name: string; message: string }>(`/api/bank/statements/${id}/ledgers`, send("POST", body)),
  updateRow: (id: number, body: { voucher_type?: string; ledgers?: { ledger_id: number; amount: string }[];
    narration?: string; skip?: boolean }) => req<Statement>(`/api/bank/rows/${id}`, send("PATCH", body)),
  postRow: (id: number) => req<{ result: PostResult; statement: Statement }>(`/api/bank/rows/${id}/post`, { method: "POST" }),
  post: (id: number, rowIds: number[]) =>
    req<{ results: PostResult[]; posted: number; statement: Statement }>(
      `/api/bank/statements/${id}/post`, send("POST", { row_ids: rowIds })),
};

/** Paise, so splits add up exactly. */
export const paise = (v: string | number | null | undefined) => Math.round(Number(v ?? 0) * 100);
export const rupees = (p: number) => (p / 100).toFixed(2);
