export type Direction = "in" | "out";

export interface RiskReason { code: string; label: string; detail: string; weight: number }
export interface Risk {
  score: number;
  level: "low" | "medium" | "high";
  reasons: RiskReason[];
  duplicates?: { transaction_id: string; minutes_ago: number; amount: number }[];
  recommendation: string;
  model?: { type: string; anomaly: number | null; trained_on: number; min_samples: number };
  suggested_category?: string;
}

export interface Insight {
  state: "done" | "final" | "info" | "in_progress" | "overdue";
  key?: string;
  title: string;
  explain: string;
  likely_outcome?: string;
  estimate?: string | null;
  eta_from?: string | null;
  eta_to?: string | null;
  elapsed?: string;
  learned_from_history?: boolean;
  actions: string[];
  can_cancel?: boolean;
  can_review?: boolean;
  paypal?: PayPalDetail | null;
}

export interface PayPalDetail {
  checked_at?: string; transaction_status?: string | null; batch_status?: string | null;
  error_name?: string | null; error_message?: string | null; error_info?: string | null;
  time_processed?: string | null; check_error?: string | null; check_error_name?: string | null; debug_id?: string | null;
  capture_status?: string | null; capture_reason?: string | null; report_status?: string | null; report_error?: string | null;
  gross?: number | null; fee?: number | null; net?: number | null; settled_by?: string | null;
}

export interface Tx {
  id: string;
  kind: "checkout" | "payout" | "invoice" | "refund" | "synced";
  direction: Direction;
  amount: number;
  currency: string;
  counterparty: string | null;
  counterparty_email: string | null;
  counterparty_phone: string | null;
  counterparty_country: string | null;
  memo: string | null;
  category: string | null;
  category_source: string | null;
  status: string;
  status_reason: string | null;
  paypal_order_id: string | null;
  paypal_authorization_id: string | null;
  paypal_capture_id: string | null;
  paypal_payout_batch_id: string | null;
  paypal_payout_item_id: string | null;
  paypal_invoice_id: string | null;
  paypal_transaction_id: string | null;
  risk_score: number | null;
  risk_level: "low" | "medium" | "high" | null;
  risk_reasons: RiskReason[];
  source: "app" | "paypal_sync" | "agent";
  created_at: string;
  status_changed_at: string | null;
  insight?: Insight | null;
  paypal_detail?: PayPalDetail | null;
  mine?: boolean; // false: another PayMinute user made this payment to (or from) you
  history?: { status: string; reason: string | null; source: string; at: string }[];
}

export interface PayPalUser {
  name: string; email: string | null; payer_id: string | null; verified: boolean;
  phone: string | null; phone_source: "paypal" | "user" | null;
}

export interface PhoneCheck {
  input: string; valid: boolean; e164: string | null; display: string; country?: string; place?: string;
  line_type?: string; network?: string | null; score?: number; level: "low" | "medium" | "high"; verdict: string;
  payable: boolean;
  paypal: { status: "verified" | "linked" | "not_on_paypal" | "unknown"; label: string; detail: string; name?: string | null; payer_id?: string };
  signals: RiskReason[]; ai_summary?: string | null;
}

export interface AppConfig {
  mode: "sandbox";
  configured: boolean;
  auth_required: boolean;
  user: PayPalUser | null;
  session?: { idle_minutes: number; expires_at: number | null; ended: string | null };
  login_redirect_uri: string;
  phone_region: string;
  currency: string;
  locale?: string;
  business_name: string;
  paypal_client_id: string | null;
  paypal_sdk_url: string;
  llm: { provider: string; model: string | null; online: boolean };
  webhook_configured: boolean;
  categories: { expense: string[]; income: string[] };
  studio?: { license_key: string | null; ai_models: string[] };
  integrations?: { mcp_servers: { name: string; transport: string; tools: number | null; error: string | null }[]; kernel: boolean };
}

export interface Budget { category: string; limit: number; spent: number; forecast: number; percent: number; state: "ok" | "at_risk" | "over"; remaining: number }
export interface Recommendation { kind: string; severity: "high" | "medium" | "low"; title: string; body: string }
export interface MonthSummary {
  month: string; money_in: number; money_out: number; net: number; prev_money_in: number; prev_money_out: number;
  by_category: { category: string; amount: number; previous: number }[]; day_of_month: number; days_in_month: number;
}
export interface Forecast { money_out: number; money_in: number; net: number; confidence: string }

export interface AgentAction {
  id: string; tool: string; args: Record<string, unknown>; summary: string; risk: Risk | null;
  status: "pending" | "executed" | "rejected" | "failed";
}
export interface AgentStep { tool: string; source: string; ok: boolean; queued?: boolean; error?: string; action?: AgentAction }

export class ApiError extends Error {
  status: number;
  data: any;
  constructor(status: number, message: string, data: any) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) {
    let msg = data?.detail ?? (res.status >= 500 && !text
      ? "Can't reach the PayMinute server. Start the backend on port 8000."
      : res.status >= 500 ? "Server error. Check the backend terminal for details." : res.statusText);
    // FastAPI validation errors: name the field that's wrong
    if (Array.isArray(msg)) msg = msg.map((d: any) => {
      const f = String(d.loc?.[d.loc.length - 1] ?? "").replace(/_/g, " ");
      return f && f !== "body" ? `${f}: ${String(d.msg).replace(/^Value error, /, "")}` : d.msg;
    }).join("; ");
    // session ended: let the app show the login screen
    if (res.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new CustomEvent("payminute:logged-out", { detail: String(msg) }));
    throw new ApiError(res.status, String(msg), data);
  }
  return data as T;
}

export const api = {
  get: <T,>(p: string) => request<T>("GET", p),
  post: <T,>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T,>(p: string, b?: unknown) => request<T>("PUT", p, b ?? {}),
  patch: <T,>(p: string, b?: unknown) => request<T>("PATCH", p, b ?? {}),
  del: <T,>(p: string) => request<T>("DELETE", p),
};

export function newRef(): string {
  return (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/-/g, "");
}

// name, else phone, else email
export function partyLabel(tx: Pick<Tx, "counterparty" | "counterparty_email" | "counterparty_phone">): string {
  return tx.counterparty || tx.counterparty_phone || tx.counterparty_email || "Unknown";
}
export function partyKey(tx: Pick<Tx, "counterparty" | "counterparty_email" | "counterparty_phone">): string {
  return tx.counterparty_phone || tx.counterparty_email || tx.counterparty || "?";
}
