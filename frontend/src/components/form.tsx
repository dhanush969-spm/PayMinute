// Form checks: say exactly which field needs fixing, next to the field and in an alert.
import { AlertTriangle } from "lucide-react";

export type Errors = Record<string, string>;

export const amountError = (raw: string, label = "an amount", max = 20000): string | null => {
  if (!raw.trim()) return `Enter ${label}.`;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return `Enter ${label} greater than 0.`;
  if (n > max) return `The most you can enter is ${max.toLocaleString()}.`;
  if (!/^\d+(\.\d{1,2})?$/.test(raw.trim())) return "Use at most 2 decimal places.";
  return null;
};

export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export const payeeError = (raw: string, who = "the recipient's"): string | null => {
  const v = raw.trim();
  if (!v) return `Enter ${who} phone number or PayPal email.`;
  if (/[@a-z]/i.test(v)) return EMAIL_RE.test(v) ? null : "That PayPal email doesn't look right.";
  return (v.match(/\d/g)?.length ?? 0) >= 7 ? null : "Enter the full phone number with its country code, e.g. +1 408 555 0134.";
};

/** Keep only the errors that are set. */
export function collect(checks: Record<string, string | null | false | undefined>): Errors {
  const out: Errors = {};
  for (const [k, v] of Object.entries(checks)) if (v) out[k] = v;
  return out;
}

/** Show the problems: alert with the first one and focus its field. Returns true when there are none. */
export function report(errors: Errors, toast: (m: string, k?: "info" | "error") => void, form?: HTMLElement | null): boolean {
  const keys = Object.keys(errors);
  if (!keys.length) return true;
  toast(keys.length > 1 ? `${errors[keys[0]]} (+${keys.length - 1} more to fix)` : errors[keys[0]], "error");
  const el = form?.querySelector<HTMLElement>(`[data-field="${keys[0]}"]`);
  el?.focus();
  return false;
}

/** Red border on a field with an error. */
export const invalid = (msg?: string) => (msg ? " !border-red-500 !bg-red-50/40" : "");

export function FieldError({ msg }: { msg?: string }) {
  if (!msg) return null;
  return <div className="text-xs text-red-700 flex items-start gap-1 mt-1" role="alert"><AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />{msg}</div>;
}

export function FormAlert({ msg, onClose }: { msg?: string | null; onClose?: () => void }) {
  if (!msg) return null;
  return (
    <div className="p-3 rounded-2xl border border-red-200 bg-red-50 text-sm text-red-800 flex items-start gap-2" role="alert">
      <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" /><span className="flex-1">{msg}</span>
      {onClose && <button type="button" className="gbtn text-xs text-red-700" onClick={onClose}>Dismiss</button>}
    </div>
  );
}
