// Typed client for the FastAPI backend (app/bridge), mounted at /bridge.
// In dev the UI runs on :3000 and the app on :8770. Once built the UI is
// served by the app itself, so everything is same-origin.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ??
  (typeof window !== "undefined" && window.location.port === "3000" ? "http://localhost:8770/bridge" : "/bridge");

export type Severity = "ok" | "warn" | "error";

export interface FieldMeta {
  value: unknown;
  confidence: number;
  source: "regex" | "llm" | "ocr" | "inferred" | "user" | "computed" | "none";
  evidence: string | null;
}

export interface Validation {
  code: string;
  severity: Severity;
  message: string;
  expected: string | null;
  actual: string | null;
}

export interface Candidate {
  ledger_id?: number;
  stock_item_id?: number;
  name: string;
  gstin?: string | null;
  hsn?: string | null;
  score: number;
}

export interface DuplicateFinding {
  source: "local" | "tally";
  kind: "exact" | "reference" | "similar";
  confidence: number;
  reason: string;
  entry_id?: number;
  voucher_number?: string | null;
  voucher_type?: string | null;
  date?: string | null;
  amount?: string | null;
  party?: string | null;
  reference?: string | null;
}

export interface Duplicates {
  findings: DuplicateFinding[];
  tally_checked: boolean;
  tally_error: string | null;
  tally_scanned: number;
  tally_window: [string, string] | null;
}

export interface EntryLine {
  id: number;
  line_no: number;
  description_raw: string | null;
  /** The item's description in Tally, under its name ("SMALL", "5.5 FT"). */
  description: string | null;
  hsn_sac: string | null;
  quantity: string | null;
  uom: string | null;
  rate: string | null;
  amount: string | null;
  gst_rate: number | null;
  stock_item_id: number | null;
  ledger_id: number | null;
  match_confidence: number;
  match_reason: string | null;
  candidates: Candidate[] | null;
  matched_name: string | null;
}

export interface EntrySummary {
  id: number;
  document_id: number;
  company_id: number | null;
  voucher_type: string;
  invoice_no: string | null;
  invoice_date: string | null;
  party_name_raw: string | null;
  party_gstin: string | null;
  party_ledger_id: number | null;
  party_ledger_name: string | null;
  party_confidence: number;
  grand_total: string | null;
  currency: string;
  confidence: number;
  confidence_label: string;
  status: string;
  error_count: number;
  warning_count: number;
  duplicate_of_id: number | null;
  filename: string | null;
  created_at: string;
  /** The AI's check in the background: waiting, reading, done, failed; null: none. */
  ai_state?: string | null;
}

export interface EntryDetail extends EntrySummary {
  due_date: string | null;
  own_gstin: string | null;
  place_of_supply: string | null;
  place_of_supply_name: string | null;
  supply_type: string | null;
  taxable_total: string | null;
  cgst: string | null;
  sgst: string | null;
  igst: string | null;
  cess: string | null;
  round_off: string | null;
  narration: string | null;
  party_match_reason: string | null;
  party_candidates: Candidate[] | null;
  field_meta: Record<string, FieldMeta> | null;
  validations: Validation[] | null;
  duplicate_reason: string | null;
  tally_voucher_id: string | null;
  export_path: string | null;
  posted_at: string | null;
  lines: EntryLine[];
  document: {
    id: number; filename: string; pages: number; has_text_layer: boolean; ocr_used: boolean; status: string;
  } | null;
}

export interface PreviewRow {
  side: "Dr" | "Cr";
  ledger: string;
  ledger_id: number | null;
  amount: string;
  is_party: boolean;
  missing: boolean;
  stock_item?: string;
  quantity?: string;
  /** What the row is, so its ledger can be changed in the right place. */
  role: "party" | "line" | "tax" | "round";
  kind?: "cgst" | "sgst" | "igst" | "cess";
  line_ids?: number[];
  /** Where a positive amount of it sits: on the other side it is saved negative. */
  natural: "Dr" | "Cr";
  /** An item's rate and unit as posted; a tax's rate, as Tally shows it beside the tax. */
  rate?: string;
  unit?: string;
  pct?: string;
}

export interface Preview {
  ok: boolean;
  company: string;
  voucher_type: string;
  behaves_as: string;
  date?: string;
  reference?: string | null;
  voucher_number?: string | null;
  narration?: string | null;
  /** The natural side of lines, taxes and the round-off: where a row added here goes. */
  line_side?: "Dr" | "Cr";
  rows: PreviewRow[];
  total_dr?: string;
  total_cr?: string;
  balanced: boolean;
  missing: string[];
  error?: string;
}

export interface VoucherTypeOption {
  name: string;
  parent: string;
  label: string;
  shape: "invoice" | "settlement" | "transfer" | "free";
  custom: boolean;
}

export interface Health {
  app: string;
  database: { engine: string; backups: { file: string; created_at: string }[] };
  tally: { connected: boolean; url: string; companies?: number; company_names?: string[]; error?: string; allow_post: boolean;
           educational?: boolean | null; busy?: boolean };
  ollama: { available: boolean; model?: string; reason?: string | null; setup?: AiSetup };
  ocr: { engine: string; available: boolean };
  watch: { enabled: boolean; folder: string };
  counts: Record<string, number>;
}

export interface Company {
  id: number;
  tally_name: string;
  gstin: string | null;
  state_code: string | null;
  /** The GST registration (tax unit) the company's vouchers are kept under. */
  gst_registration?: string | null;
  last_synced_at: string | null;
  ledger_count: number;
  stock_count: number;
}

export interface Ledger {
  id: number;
  tally_name: string;
  parent: string | null;
  gstin: string | null;
}

export interface StockItem {
  id: number;
  tally_name: string;
  parent: string | null;
  uom: string | null;
  hsn: string | null;
}

export interface ApproveResult {
  ok: boolean;
  entry_id: number;
  mode: string;
  status: string;
  message: string;
  export_path: string | null;
  tally_voucher_id: string | null;
  xml: string | null;
  blocked_by: string[];
  needs_ack: boolean;
}

export type ApproveMode = "export" | "draft" | "post";

export interface DocStatus {
  id: number;
  status: string;
  error: string | null;
  entry_id: number | null;
  filename: string;
}

/** The app's one AI, fetched when asked for (backend services/ai_setup.py, app/backend/ai.py). */
export interface AiSetup {
  can_install: boolean; running: boolean; folder: string; model: string | null;
  step: string | null; percent: number | null; error: string | null;
  /** Another app's copy, when the app runs that one (app/backend/ai.py). */
  shared: { app: string; folder: string } | null;
}

/** Bills sent from a phone with the current code (backend services/phone.py). */
export interface PhoneSent {
  id: number; name: string; type: string | null; at: string | null;
  state: "reading" | "ready" | "failed" | "set aside"; entry_id: number | null; error: string | null;
}
export interface PhoneStatus {
  on: boolean; url: string | null; until: string | null;
  qr: { size: number; path: string } | null; problem: string | null; sent: PhoneSent[];
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: init?.body instanceof FormData
        ? init?.headers
        : { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    // The most common failure by far: the Python process is not running.
    throw new ApiError("Cannot reach the app's backend. Start it from the Start menu shortcut.", 0);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* keep the status line */ }
    throw new ApiError(detail, res.status);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => req<Health>("/api/health"),
  settings: () => req<Record<string, unknown>>("/api/settings"),
  saveSettings: (body: Record<string, unknown>) =>
    req<Record<string, unknown>>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  backup: () => req<{ ok: boolean; file?: string; message: string }>("/api/backup", { method: "POST" }),
  aiSetup: () => req<AiSetup>("/api/ai/setup"),
  aiInstall: () => req<AiSetup>("/api/ai/install", { method: "POST" }),
  phone: () => req<PhoneStatus>("/api/phone"),
  phoneStart: () => req<PhoneStatus>("/api/phone/start", { method: "POST" }),
  phoneStop: () => req<PhoneStatus>("/api/phone/stop", { method: "POST" }),

  companies: () => req<Company[]>("/api/tally/companies"),
  discover: () => req<{ found: number; added: string[]; message: string }>(
    "/api/tally/companies/discover", { method: "POST" }),
  sync: (id: number) => req<{ message: string }>(`/api/tally/companies/${id}/sync`, { method: "POST" }),
  voucherTypes: (companyId?: number) =>
    req<{ source: string; types: VoucherTypeOption[] }>(
      `/api/tally/voucher-types${companyId ? `?company_id=${companyId}` : ""}`),
  ledgers: (companyId: number, q = "", parent = "") =>
    req<Ledger[]>(`/api/tally/companies/${companyId}/ledgers?q=${encodeURIComponent(q)}` +
                  `&parent=${encodeURIComponent(parent)}&limit=60`),
  stockItems: (companyId: number, q = "") =>
    req<StockItem[]>(`/api/tally/companies/${companyId}/stock-items?q=${encodeURIComponent(q)}&limit=60`),

  upload: (files: File[], opts: { companyId?: number; voucherType?: string; voucherParent?: string;
                                  eachPage?: boolean }) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    if (opts.companyId) fd.append("company_id", String(opts.companyId));
    if (opts.voucherType) fd.append("voucher_type", opts.voucherType);
    if (opts.voucherParent) fd.append("voucher_parent", opts.voucherParent);
    if (opts.eachPage) fd.append("each_page", "true");
    return req<{ queued: number; results: { filename: string; ok: boolean; document_id?: number;
      entry_id?: number | null; message?: string; error?: string; duplicate_file?: boolean }[] }>(
      "/api/documents/upload", { method: "POST", body: fd });
  },
  document: (id: number) => req<DocStatus>(`/api/documents/${id}`),
  documents: (ids: number[]) => req<DocStatus[]>(`/api/documents/status?ids=${ids.join(",")}`),
  dismiss: (id: number) => req<{ status: string }>(`/api/documents/${id}/dismiss`, { method: "POST" }),
  documentUrl: (id: number) => `${API_BASE}/api/documents/${id}/file`,
  pageCount: (id: number) => req<{ count: number; kind: "pdf" | "image" }>(`/api/documents/${id}/pages`),
  pageUrl: (id: number, n: number) => `${API_BASE}/api/documents/${id}/page/${n}`,
  reprocess: (id: number, voucherType?: string) =>
    req<{ document_id: number; status: string }>(
      `/api/documents/${id}/reprocess${voucherType ? `?voucher_type=${encodeURIComponent(voucherType)}` : ""}`,
      { method: "POST" }),

  entries: (params: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][]);
    return req<EntrySummary[]>(`/api/entries?${qs}`);
  },
  entry: (id: number) => req<EntryDetail>(`/api/entries/${id}`),
  preview: (id: number) => req<Preview>(`/api/entries/${id}/preview`),
  /** The party's balance in Tally on the bill's date; null when Tally does not answer. */
  balance: (id: number) => req<{ balance: string | null; side?: "Dr" | "Cr"; error?: string }>(`/api/entries/${id}/balance`),
  stats: () => req<{ pending: number; clean: number; needs_review: number; posted: number; drafted: number;
                     approved: number; clean_ids: number[]; clean_value: string; threshold: number;
                     reading: number; ai_checking: number; failed: { id: number; filename: string; error: string | null }[] }>(
    "/api/entries/stats"),
  updateEntry: (id: number, body: Record<string, unknown>) =>
    req<EntryDetail>(`/api/entries/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  rematch: (id: number) => req<EntryDetail>(`/api/entries/${id}/rematch`, { method: "POST" }),
  checkDuplicates: (id: number) => req<EntryDetail>(`/api/entries/${id}/check-duplicates`, { method: "POST" }),
  reject: (id: number, reason = "") =>
    req<EntrySummary>(`/api/entries/${id}/reject?reason=${encodeURIComponent(reason)}`, { method: "POST" }),
  /** Undo a reject: back to review, or to exported if it was. */
  restore: (id: number) => req<EntrySummary>(`/api/entries/${id}/restore`, { method: "POST" }),
  approve: (id: number, body: { mode: ApproveMode; dry_run?: boolean; override_duplicate?: boolean;
                                 override_errors?: boolean }) =>
    req<ApproveResult>(`/api/entries/${id}/approve`, json(body)),
  approveBatch: (ids: number[], mode: ApproveMode) =>
    req<{ total: number; approved: number; results: ApproveResult[] }>(
      "/api/entries/approve-batch", json({ entry_ids: ids, req: { mode } })),
  createMaster: (body: Record<string, unknown>) =>
    req<{ ok: boolean; kind: "ledger" | "stock"; id: number; name: string; message: string }>(
      "/api/entries/create-master", json(body)),
};

export const money = (v: string | number | null | undefined, currency = "INR") => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  const as = (code: string) =>
    new Intl.NumberFormat("en-IN", { style: "currency", currency: code, maximumFractionDigits: 2 }).format(n);
  // Not a currency code (a "₹" copied off a bill once took the whole review screen down): rupees.
  try { return as(currency); } catch { return as("INR"); }
};

export const num = (v: string | number | null | undefined) => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isNaN(n) ? String(v) : new Intl.NumberFormat("en-IN", { maximumFractionDigits: 4 }).format(n);
};

export const shortDate = (v: string | null | undefined) => {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isNaN(d.getTime())
    ? v
    : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
};

export const duplicatesOf = (e: EntryDetail | null): Duplicates | null =>
  ((e?.field_meta?._duplicates?.value as Duplicates | undefined) ?? null);
