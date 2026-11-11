let currency = "USD";
let locale = "en-US";
export function setCurrency(c: string, l?: string) { currency = c; if (l) locale = l; }
export function getLocale() { return locale; }
export function currencySymbol(cur = currency): string {
  const part = new Intl.NumberFormat(locale, { style: "currency", currency: cur }).formatToParts(0).find((p) => p.type === "currency");
  return part?.value ?? cur;
}

export function money(n: number, opts: { sign?: boolean; cur?: string; compact?: boolean } = {}): string {
  const f = new Intl.NumberFormat(locale, {
    style: "currency",
    currency: opts.cur || currency,
    maximumFractionDigits: opts.compact && Math.abs(n) >= 1000 ? 0 : 2,
    minimumFractionDigits: opts.compact && Math.abs(n) >= 1000 ? 0 : 2,
  }).format(Math.abs(n));
  if (!opts.sign) return n < 0 ? `−${f}` : f;
  return n < 0 ? `−${f}` : `+${f}`;
}

export function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const y = new Date(); y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString(locale, { weekday: "short", month: "short", day: "numeric", year: d.getFullYear() !== today.getFullYear() ? "numeric" : undefined });
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString(locale, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function relative(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return `${Math.round(diff / 86400)} d ago`;
}

export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  const parts = name.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

const STATUS_LABEL: Record<string, string> = {
  COMPLETED: "Completed", SUCCESS: "Completed", PENDING: "Pending", UNCLAIMED: "Unclaimed", ONHOLD: "On hold",
  HELD_FOR_REVIEW: "Held for review", AUTHORIZED: "Authorized", CREATED: "Awaiting buyer", FAILED: "Failed",
  DENIED: "Denied", VOIDED: "Voided", RETURNED: "Returned", REFUNDED: "Refunded", BLOCKED: "Blocked",
  DRAFT: "Draft", SENT: "Invoice sent", CANCELLED: "Cancelled", PAID: "Paid", REVERSED: "Reversed",
};
export function statusLabel(s: string): string { return STATUS_LABEL[s] ?? s.charAt(0) + s.slice(1).toLowerCase(); }

export function statusTone(s: string): "success" | "warn" | "danger" | "info" | "" {
  if (["COMPLETED", "SUCCESS", "PAID"].includes(s)) return "success";
  if (["PENDING", "UNCLAIMED", "ONHOLD", "HELD_FOR_REVIEW", "AUTHORIZED"].includes(s)) return "warn";
  if (["FAILED", "DENIED", "BLOCKED", "REVERSED"].includes(s)) return "danger";
  if (["SENT", "CREATED", "DRAFT"].includes(s)) return "info";
  return "";
}

export function kindLabel(kind: string, direction: string): string {
  if (kind === "payout") return "Sent with PayPal Payouts";
  if (kind === "checkout") return "PayPal Checkout";
  if (kind === "invoice") return "PayPal invoice";
  if (kind === "synced") return direction === "in" ? "PayPal activity" : "PayPal activity";
  return kind;
}
