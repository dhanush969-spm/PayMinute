// Shared pieces for the Buy, Sell and Checkout pages.
import { useEffect, useState } from "react";
import { BadgeCheck } from "lucide-react";
import { api } from "../api";

export interface Trust { score: number; label: string; level: string }
export interface Features { specs: Record<string, number>; condition: string; condition_score: number; extras: string[]; flaws: string[] }
export interface Listing {
  id: string; title: string; description: string | null; price: number; seller_name: string | null; status: string;
  sold_price?: number | null; floor?: number; trust?: Trust;
}
export interface Candidate extends Listing {
  score: number; quality: number; relevance: number; trust: Trust; features: Features; pros: string[]; cons: string[]; reachable: boolean;
  fit?: number; has?: string[]; lacks?: string[];
}
export interface PriceHint { price: number; low: number; high: number; basis: string; comparables: number; condition?: string }
export interface Parsed {
  request: { title: string | null; details: string | null; price: number | null; floor?: number | null; wanted: string[] };
  flaws: string[]; condition: string; suggestion: PriceHint | null;
}
export interface Msg { agent: "buyer" | "seller"; action: string; amount: number; text: string }
export interface Quote {
  id: string; listing_id: string; title: string; seller_name: string | null; pay_to: string; list_price: number; price: number;
  status: "open" | "paid" | "expired" | "failed"; expires_at: string; transaction_id: string | null;
  risk?: { score: number; level: "low" | "medium" | "high"; reasons: { code: string; label: string }[]; recommendation: string };
  recipient?: { type: string; verified?: string | null };
}

export const field = "w-full bg-slate-50 border border-slate-200 rounded-2xl px-4 py-2.5 text-sm text-black outline-none focus:border-[#1a73e8]";
export const primary = "gbtn px-4 py-2.5 rounded-full bg-[#1a73e8] text-white text-sm disabled:opacity-50 shrink-0";
export const decimal = (v: string) => v.replace(/[^0-9.]/g, "");

export function TrustBadge({ trust }: { trust?: Trust }) {
  if (!trust) return null;
  const good = trust.label === "Verified by PayPal";
  const bad = trust.level === "high";
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded-full border inline-flex items-center gap-1 ${good ? "bg-emerald-50 text-emerald-700 border-emerald-200" : bad ? "bg-red-50 text-red-700 border-red-200" : "bg-slate-50 text-slate-600 border-slate-200"}`}>
      {good && <BadgeCheck className="w-3 h-3" />}{trust.label}
    </span>
  );
}

export function Transcript({ messages }: { messages: Msg[] }) {
  return (
    <div className="space-y-2">
      {messages.map((m, i) => (
        <div key={i} className={`flex ${m.agent === "buyer" ? "justify-end" : "justify-start"}`}>
          <div className={`max-w-[80%] px-3.5 py-2 rounded-2xl text-sm ${m.agent === "buyer" ? "bg-[#1a73e8] text-white" : "bg-slate-100 text-black"}`}>
            <div className="text-[10px] opacity-75">{m.agent === "buyer" ? "Your agent" : "Seller's agent"}</div>{m.text}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Read a one-line description as the user types (debounced): item, features, condition, price and an AI price. */
export function parsePrompt(prompt: string, role: "buy" | "sell"): Promise<Parsed> {
  return api.post<Parsed>("/market/parse", { prompt, role });
}

export function Chips({ items, tone = "slate" }: { items: string[]; tone?: "slate" | "green" | "red" }) {
  const cls = tone === "green" ? "bg-emerald-50 text-emerald-800" : tone === "red" ? "bg-red-50 text-red-700" : "bg-slate-100 text-slate-700";
  return <div className="flex flex-wrap gap-1.5">{items.map((x) => <span key={x} className={`text-[11px] px-2 py-0.5 rounded-full ${cls}`}>{x}</span>)}</div>;
}

/** Price field with quick "+5% / +10%" buttons, so the AI suggestion is easy to raise. */
export function PriceInput({ value, onChange, label, dataField, error }: { value: string; onChange: (v: string) => void; label: string; dataField: string; error?: string }) {
  const bump = (pct: number) => { const n = Number(value); if (n > 0) onChange(String(Math.round(n * (1 + pct) * 100) / 100)); };
  return (
    <div className="flex items-center gap-2">
      <input data-field={dataField} className={field + (error ? " !border-red-500 !bg-red-50/40" : "")} inputMode="decimal" placeholder={label} aria-label={label}
        value={value} onChange={(e) => onChange(decimal(e.target.value))} />
      <button type="button" className="gbtn text-xs px-2.5 py-2 rounded-full bg-slate-100 text-black shrink-0" onClick={() => bump(0.05)}>+5%</button>
      <button type="button" className="gbtn text-xs px-2.5 py-2 rounded-full bg-slate-100 text-black shrink-0" onClick={() => bump(0.1)}>+10%</button>
    </div>
  );
}

/** Show an agent conversation one message at a time with a short typing pause, like a live chat. */
export function usePaced(messages: Msg[] | undefined) {
  const [count, setCount] = useState(0);
  useEffect(() => { setCount(0); }, [messages]);
  useEffect(() => {
    if (!messages || count >= messages.length) return;
    const m = messages[count];
    const wait = count === 0 ? 600 : Math.min(2400, 800 + m.text.length * 14);
    const t = setTimeout(() => setCount((c) => c + 1), wait);
    return () => clearTimeout(t);
  }, [messages, count]);
  const total = messages?.length ?? 0;
  return { shown: (messages ?? []).slice(0, count), typingWho: count < total ? messages![count].agent : null, finished: count >= total };
}

export function TypingDots({ who }: { who: "buyer" | "seller" }) {
  return (
    <div className={`flex ${who === "buyer" ? "justify-end" : "justify-start"}`}>
      <div className="px-3.5 py-2 rounded-2xl bg-slate-100 text-slate-500 text-sm flex gap-1" aria-label={`${who === "buyer" ? "Your agent" : "Seller's agent"} is typing`}>
        <span className="animate-bounce">•</span><span className="animate-bounce [animation-delay:150ms]">•</span><span className="animate-bounce [animation-delay:300ms]">•</span>
      </div>
    </div>
  );
}
