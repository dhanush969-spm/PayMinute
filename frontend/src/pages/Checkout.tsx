import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { BadgeCheck, CheckCircle2, Clock, Lock, ShieldAlert, ShieldCheck } from "lucide-react";
import { ApiError, api } from "../api";
import { money } from "../format";
import { useApp } from "../components/ui";
import { Quote } from "./Market";
import { FormAlert } from "../components/form";

const TONE = { low: "bg-emerald-50 text-emerald-800 border-emerald-200", medium: "bg-amber-50 text-amber-900 border-amber-200", high: "bg-red-50 text-red-800 border-red-200" };

function left(iso: string, now: number) {
  const s = Math.max(0, Math.floor((new Date(iso).getTime() - now) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// Seller and amount are locked on the server; this page only shows them and pays the quote by id.
export default function Checkout() {
  const { quoteId = "" } = useParams();
  const { toast, bump, openTx } = useApp();
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paying, setPaying] = useState(false);
  const [confirmRisk, setConfirmRisk] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    api.get<Quote>(`/market/quotes/${encodeURIComponent(quoteId)}`).then(setQuote).catch((e) => setError((e as Error).message));
  }, [quoteId]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);

  const expired = quote && (quote.status === "expired" || (quote.status === "open" && new Date(quote.expires_at).getTime() <= now));

  async function pay(acknowledge: boolean) {
    if (!quote) return;
    setFailure(null);
    setPaying(true);
    try {
      const r = await api.post<{ outcome: string; summary: string; quote: Quote }>(`/market/quotes/${quote.id}/pay`, { acknowledge_risk: acknowledge });
      setQuote({ ...quote, ...r.quote });
      setConfirmRisk(false);
      toast(r.summary, r.outcome === "paid" ? "info" : "error");
      bump();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.data?.paypal?.risk) setConfirmRisk(true);
      else if (e instanceof ApiError && e.status === 410) setQuote({ ...quote, status: "expired" });
      else { const msg = `Payment not sent: ${(e as Error).message}`; setFailure(msg); toast(msg, "error"); }
    } finally {
      setPaying(false);
    }
  }

  if (error) return <div className="bg-white rounded-3xl border border-slate-200 p-5 text-sm space-y-3 max-w-lg mx-auto"><div className="text-black">{error}</div><Link to="/buy" className="text-[#1a73e8] text-sm">Back to Buy</Link></div>;
  if (!quote) return <div className="skeleton rounded-3xl max-w-4xl mx-auto" style={{ height: 320 }} />;

  if (quote.status === "paid") {
    return (
      <section className="bg-white rounded-3xl border border-slate-200 p-6 text-center space-y-3 max-w-lg mx-auto">
        <CheckCircle2 className="w-14 h-14 text-emerald-600 mx-auto" />
        <div className="text-lg text-black">Paid {money(quote.price)}</div>
        <div className="text-sm text-slate-600">“{quote.title}” from {quote.seller_name || "the seller"}, through PayPal.</div>
        {quote.transaction_id && <button className="gbtn text-sm text-[#1a73e8]" onClick={() => openTx(quote.transaction_id!)}>View payment</button>}
        <div><Link to="/buy" className="text-sm text-slate-600">Buy something else</Link></div>
      </section>
    );
  }

  const saved = quote.list_price - quote.price;
  const risk = quote.risk;
  return (
    <div className="grid gap-4 max-w-5xl mx-auto lg:grid-cols-[minmax(0,1fr)_380px] lg:items-start">
      <section className="bg-white rounded-3xl border border-slate-200 p-5 lg:p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="text-sm text-black">Order summary</div>
          {!expired && <span className="text-xs text-slate-500 flex items-center gap-1"><Clock className="w-3.5 h-3.5" /> Price held {left(quote.expires_at, now)}</span>}
        </div>
        <div>
          <div className="text-base text-black">{quote.title}</div>
          <div className="text-xs text-slate-500">Agreed by your agent and the seller's agent</div>
        </div>
        <div className="rounded-2xl bg-slate-50 border border-slate-200 divide-y divide-slate-200 text-sm">
          <div className="flex justify-between items-center gap-2 px-4 py-3">
            <span className="text-slate-600">Pay to</span>
            <span className="text-black flex items-center gap-1.5 min-w-0">
              <Lock className="w-3.5 h-3.5 text-slate-400 shrink-0" aria-label="Locked" />
              <span className="truncate">{quote.seller_name || "Seller"} · {quote.pay_to}</span>
            </span>
          </div>
          {quote.recipient?.verified && (
            <div className="flex justify-between items-center px-4 py-2.5 text-xs">
              <span className="text-slate-600">PayPal check</span>
              <span className="text-emerald-700 flex items-center gap-1"><BadgeCheck className="w-3.5 h-3.5" /> {quote.recipient.verified}</span>
            </div>
          )}
          <div className="flex justify-between items-center px-4 py-3">
            <span className="text-slate-600">Amount</span>
            <span className="text-right">
              {saved > 0 && <span className="text-xs text-slate-400 line-through mr-2">{money(quote.list_price)}</span>}
              <span className="text-xl text-black inline-flex items-center gap-1"><Lock className="w-3.5 h-3.5 text-slate-400" />{money(quote.price)}</span>
            </span>
          </div>
          {saved > 0 && <div className="px-4 py-2.5 text-xs text-emerald-700">Your agent saved you {money(saved)}</div>}
        </div>
      </section>

      <section className="bg-white rounded-3xl border border-slate-200 p-5 lg:p-6 space-y-4 lg:sticky lg:top-24">
        <div className="flex items-baseline justify-between">
          <span className="text-sm text-slate-600">Total</span>
          <span className="text-2xl text-black">{money(quote.price)}</span>
        </div>
        {risk && (
          <div className={`p-3 rounded-2xl border text-sm space-y-1 ${TONE[risk.level]}`}>
            <div className="flex items-center gap-2">
              {risk.level === "low" ? <ShieldCheck className="w-4 h-4" /> : <ShieldAlert className="w-4 h-4" />}
              AI Guard: {risk.level === "low" ? "looks safe" : risk.level === "medium" ? "double-check" : "high risk"} · {Math.round(risk.score * 100)}/100
            </div>
            {risk.reasons.length > 0 && <div className="text-xs opacity-90">{risk.reasons.slice(0, 4).map((r) => r.label).join(" · ")}</div>}
          </div>
        )}

        <FormAlert msg={failure} onClose={() => setFailure(null)} />
        {expired ? (
          <div className="space-y-2 text-center">
            <div className="text-sm text-slate-700">This price hold expired, so nothing was paid.</div>
            <Link to="/buy" className="gbtn inline-block px-4 py-2.5 rounded-full bg-[#1a73e8] text-white text-sm no-underline">Find it again</Link>
          </div>
        ) : confirmRisk ? (
          <div className="space-y-2">
            <div className="text-sm text-red-800">AI Guard flagged this payment. Only continue if you trust this seller.</div>
            <div className="grid grid-cols-2 gap-2">
              <button className="gbtn py-3 bg-slate-100 text-black rounded-full text-sm" onClick={() => setConfirmRisk(false)}>Go back</button>
              <button className="gbtn py-3 bg-red-600 text-white rounded-full text-sm disabled:opacity-50" disabled={paying} onClick={() => pay(true)}>{paying ? "Paying…" : "Pay anyway"}</button>
            </div>
          </div>
        ) : (
          <button className="gbtn w-full py-3.5 bg-[#1a73e8] text-white rounded-full text-sm disabled:opacity-50" disabled={paying || quote.status !== "open"} onClick={() => pay(false)}>
            {paying ? "Paying…" : `Pay ${money(quote.price)} with PayPal`}
          </button>
        )}
        <div className="text-[11px] text-slate-500 text-center">
          The seller and amount are fixed on PayMinute's server for this checkout and can't be edited here. Paid with PayPal Payouts in the sandbox.
        </div>
      </section>
    </div>
  );
}
