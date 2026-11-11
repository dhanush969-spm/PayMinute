import { FormEvent, useEffect, useRef, useState } from "react";
import { AtSign, BadgeCheck, CheckCircle2, CircleHelp, Lock, RefreshCw, SendHorizontal, ShieldAlert, ShieldCheck, X } from "lucide-react";
import { api, ApiError, emailAllowed, emailRule, newRef, Risk, Tx } from "../api";
import { currencySymbol, money } from "../format";
import { InsightCard, useApp } from "../components/ui";
import { Errors, FieldError, FormAlert, amountError, collect, invalid, payeeError, report } from "../components/form";
import Avatar from "./Avatar";

export interface SendPrefill { to?: string; name?: string; amount?: string; note?: string }

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const TONE = {
  low: "bg-emerald-50 border-emerald-200 text-emerald-900",
  medium: "bg-amber-50 border-amber-200 text-amber-900",
  high: "bg-red-50 border-red-200 text-red-900",
};

export default function SendSheet({ open, prefill, onClose }: { open: boolean; prefill: SendPrefill; onClose: () => void }) {
  const { config, toast, bump } = useApp();
  const [to, setTo] = useState("");
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [risk, setRisk] = useState<Risk | null>(null);
  const [checking, setChecking] = useState(false);
  const [blocker, setBlocker] = useState(false);
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState<Tx | null>(null);
  const ref = useRef(newRef());
  const formRef = useRef<HTMLFormElement>(null);
  const [err, setErr] = useState<Errors>({});
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const t = prefill.to ?? "";
    setTo(t); setName(prefill.name ?? ""); setAmount(prefill.amount ?? ""); setNote(prefill.note ?? "");
    setRisk(null); setDone(null); setBlocker(false); setErr({}); setFailure(null); ref.current = newRef();
  }, [open, prefill]);

  const amt = parseFloat(amount);
  const receiver = to.trim();
  // PayMinute pays PayPal sandbox emails only (…@personal.example.com)
  const domains = config?.email_domains ?? ["personal.example.com"];
  const receiverOk = EMAIL.test(receiver) && emailAllowed(receiver, domains);
  const valid = receiverOk && amt > 0 && amt <= 20000;

  // then score the whole payment
  useEffect(() => {
    if (!open || !valid) { setRisk(null); setChecking(false); return; }
    setChecking(true);
    const t = setTimeout(() => {
      api.post<Risk>("/payouts/assess", { receiver, amount: amt, note: note || null, name: name || null })
        .then(setRisk).catch(() => setRisk(null)).finally(() => setChecking(false));
    }, 450);
    return () => clearTimeout(t);
  }, [open, receiver, amount, note, name, valid, amt]);

  async function send(acknowledge: boolean) {
    setSending(true);
    try {
      const res = await api.post<{ transaction: Tx }>("/payouts", {
        receiver, amount: amt, note: note || null, name: name || null, client_ref: ref.current, acknowledge_risk: acknowledge,
      });
      setDone(res.transaction);
      setBlocker(false);
      bump();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.data?.paypal?.risk) { setRisk(e.data.paypal.risk); setBlocker(true); }
      else { const msg = `Payment not sent: ${(e as Error).message}`; setFailure(msg); toast(msg, "error"); setBlocker(false); }
    } finally {
      setSending(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    setFailure(null);
    const errors = collect({
      to: payeeError(receiver, domains),
      amount: amountError(amount),
    });
    setErr(errors);
    if (!report(errors, toast, formRef.current)) return;
    if (checking) { toast("Still checking this payment. Try again in a moment.", "error"); return; }
    if (!valid) return;
    if (risk?.level === "high") { setBlocker(true); return; }
    send(false);
  }

  if (!open) return null;
  const tone = risk ? TONE[risk.level] : "";
  const shownTo = receiver;
  const placeholder = `PayPal email, e.g. priya@${domains[0] ?? "personal.example.com"}`;
  const who = risk?.email_check;

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-end sm:items-center justify-center" role="dialog" aria-modal="true" aria-label="Pay">
      <div className="bg-white w-full max-w-md rounded-t-3xl sm:rounded-3xl p-6 space-y-4 shadow-xl max-h-[92vh] overflow-y-auto">
        <div className="flex justify-between items-center">
          <h3 className="m-0 text-base text-black flex items-center gap-2"><SendHorizontal className="w-5 h-5 text-[#1a73e8]" /> Pay with PayPal</h3>
          <button className="gbtn p-1 text-slate-500" onClick={onClose} aria-label="Close"><X className="w-5 h-5" /></button>
        </div>

        {done ? (
          <div className="py-4 text-center space-y-3">
            <CheckCircle2 className="w-16 h-16 text-emerald-600 mx-auto" />
            <div className="text-2xl text-black">{money(done.amount)}</div>
            <div className="text-sm text-slate-600">sent to {done.counterparty} · PayPal status: {done.status}</div>
            {done.insight && <div className="text-left"><InsightCard insight={done.insight} /></div>}
            <button className="gbtn w-full py-3.5 bg-[#1a73e8] text-white rounded-full text-sm" onClick={onClose}>Done</button>
          </div>
        ) : blocker && risk ? (
          <div className="space-y-4">
            <div className="w-12 h-12 rounded-full bg-red-50 text-red-600 flex items-center justify-center mx-auto"><Lock className="w-6 h-6" /></div>
            <div className="text-center">
              <div className="text-base text-black">PayMinute stopped this payment</div>
              <div className="text-sm text-slate-600">{risk.recommendation}</div>
            </div>
            <div className="bg-slate-50 p-4 rounded-2xl border border-slate-200 space-y-2 text-sm">
              <div className="flex justify-between"><span className="text-slate-600">Risk</span><span className="text-red-600">{risk.level.toUpperCase()} ({Math.round(risk.score * 100)}/100)</span></div>
              <div className="flex justify-between"><span className="text-slate-600">To</span><span className="text-black break-all">{shownTo}</span></div>
              <ul className="m-0 pl-4 pt-2 border-t border-slate-200 text-red-700 space-y-1">
                {risk.reasons.map((r) => <li key={r.code}>{r.label}: <span className="text-slate-700">{r.detail}</span></li>)}
              </ul>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <button className="gbtn py-3 bg-slate-100 text-black rounded-full text-sm" onClick={() => setBlocker(false)}>Go back</button>
              <button className="gbtn py-3 bg-red-600 text-white rounded-full text-sm disabled:opacity-50" disabled={sending} onClick={() => send(true)}>
                {sending ? "Sending…" : "I trust them, send"}
              </button>
            </div>
          </div>
        ) : (
          <form ref={formRef} onSubmit={submit} noValidate className="space-y-4">
            <div className="flex items-center gap-3">
              <Avatar name={name || receiver || "?"} seed={receiver || name} size={44} />
              <div className="flex-1 min-w-0 space-y-2">
                <div className={`flex items-center bg-slate-50 border border-slate-200 rounded-2xl px-3 focus-within:border-[#1a73e8]${invalid(err.to)}`}>
                  <AtSign className="w-4 h-4 text-slate-500" />
                  <input className="flex-1 bg-transparent border-0 px-2 py-3 text-sm text-black outline-none"
                    type="text" inputMode="email" autoComplete="off" autoCapitalize="off" spellCheck={false}
                    placeholder={placeholder}
                    data-field="to" value={to} onChange={(e) => { setTo(e.target.value); setErr({ ...err, to: "" }); }} aria-label="Recipient PayPal email" autoFocus />
                </div>
                <input className="w-full bg-slate-50 border border-slate-200 rounded-2xl px-4 py-2.5 text-sm text-black outline-none focus:border-[#1a73e8]"
                  placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} aria-label="Recipient name" />
              </div>
            </div>
            <FieldError msg={err.to} />
            {receiver && !err.to && (
              <div className="text-xs text-slate-500" aria-live="polite">
                {!EMAIL.test(receiver) ? `Keep typing the PayPal email (${emailRule(domains)})…`
                  : !receiverOk ? `Only PayPal sandbox emails (${emailRule(domains)}) can be paid.`
                  : who ? <span className="flex items-start gap-1.5">{who.status === "on_payminute" ? <BadgeCheck className="w-4 h-4 text-emerald-700 shrink-0" /> : <CircleHelp className="w-4 h-4 shrink-0" />}<span><span className="text-black">{who.label}.</span> {who.detail}</span></span>
                  : "Paying a PayPal sandbox account by email"}
              </div>
            )}

            <div className="text-center py-2">
              <div className="inline-flex items-baseline gap-1 text-black">
                <span className="text-3xl text-slate-500">{currencySymbol()}</span>
                <input className="w-44 text-center text-5xl bg-transparent border-0 outline-none text-black" inputMode="decimal" placeholder="0"
                  data-field="amount" value={amount} onChange={(e) => { setAmount(e.target.value.replace(/[^0-9.]/g, "")); setErr({ ...err, amount: "" }); }} aria-label={`Amount in ${config?.currency}`} />
              </div>
              <div className="flex justify-center"><FieldError msg={err.amount} /></div>
            </div>
            <input className="w-full bg-slate-50 border border-slate-200 rounded-full px-4 py-3 text-sm text-black outline-none focus:border-[#1a73e8]"
              placeholder="Add a note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} aria-label="Note" />

            {checking && !risk && (
              <div className="p-3 bg-slate-50 rounded-2xl border border-slate-200 text-sm text-slate-600 flex items-center gap-2"><RefreshCw className="w-4 h-4 animate-spin text-[#1a73e8]" /> Checking this payment…</div>
            )}
            {risk && (
              <div className={`p-3.5 rounded-2xl border text-sm space-y-1 ${tone}`} aria-live="polite">
                <div className="flex items-center gap-2">
                  {risk.level === "low" ? <ShieldCheck className="w-4 h-4" /> : <ShieldAlert className="w-4 h-4" />}
                  Payment risk: {risk.level === "low" ? "looks safe" : risk.level === "medium" ? "double-check" : "high"} · {Math.round(risk.score * 100)}/100
                </div>
                {risk.reasons.length > 0 && <div className="text-xs opacity-90">{risk.reasons.map((r) => r.label).join(" · ")}</div>}
                {risk.model && (
                  <div className="text-[11px] opacity-75">
                    {risk.model.anomaly == null
                      ? `Rule checks · the ML model learns your pattern after ${risk.model.min_samples} payments (${risk.model.trained_on} so far)`
                      : `Rule checks + ML model trained on your last ${risk.model.trained_on} payments`}
                  </div>
                )}
              </div>
            )}
            <FormAlert msg={failure} onClose={() => setFailure(null)} />
            <button type="submit" disabled={sending}
              className="gbtn w-full py-3.5 bg-[#1a73e8] text-white rounded-full text-sm disabled:opacity-50">
              {sending ? "Paying…" : checking ? "Checking…" : valid ? `Pay ${money(amt)}` : "Pay"}
            </button>
            <div className="text-[11px] text-slate-500 text-center">
              Sent with PayPal Payouts to a PayPal sandbox email. AI Guard checks the payment before any money moves.
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
