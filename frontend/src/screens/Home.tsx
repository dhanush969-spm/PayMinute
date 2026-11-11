import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BarChart3, Bot, ChevronRight, Clock, Download, PieChart, Search, SendHorizontal, ShieldAlert, ShieldCheck, ShoppingBag, Store, WalletCards, Zap } from "lucide-react";
import { api, Insight, MonthSummary, partyKey, Recommendation, Tx } from "../api";
import { money } from "../format";
import { useApp } from "../components/ui";
import Avatar from "./Avatar";
import { PayRow, useSheets } from "./shared";

interface HomeData { summary: MonthSummary; in_progress: { transaction: Tx; insight: Insight }[]; recent: Tx[]; recommendations: Recommendation[]; risk_blocked_count: number }

export default function GHome() {
  const { version, openTx, toast } = useApp();
  const { openSend } = useSheets();
  const [data, setData] = useState<HomeData | null>(null);
  const [all, setAll] = useState<Tx[]>([]);
  const [bal, setBal] = useState<{ total: number | null; available: number | null; withheld: number | null; currency: string } | null>(null);
  const [reqs, setReqs] = useState<{ open_count: number; open_total: number } | null>(null);

  useEffect(() => {
    api.get<HomeData>("/home").then(setData).catch((e) => toast(e.message, "error"));
    api.get<Tx[]>("/transactions?limit=400").then(setAll).catch(() => undefined);
    api.get<{ business: { balances: { currency: string; primary: boolean; total: number | null; available: number | null; withheld: number | null }[] } | null }>("/account")
      .then((a) => { const b = a.business?.balances ?? []; setBal(b.find((x) => x.primary) ?? b[0] ?? null); }).catch(() => undefined);
    api.get<{ open_count: number; open_total: number }>("/requests").then(setReqs).catch(() => undefined);
  }, [version, toast]);

  // people you pay most often
  const people = useMemo(() => {
    const m = new Map<string, { name: string; to: string; n: number }>();
    for (const t of all) {
      if (t.direction !== "out" || t.kind !== "payout" || !(t.counterparty_phone || t.counterparty_email)) continue;
      const k = partyKey(t).toLowerCase();
      const cur = m.get(k) ?? { name: t.counterparty || partyKey(t), to: partyKey(t), n: 0 };
      cur.n += 1;
      m.set(k, cur);
    }
    return [...m.values()].sort((a, b) => b.n - a.n).slice(0, 8);
  }, [all]);

  const s = data?.summary;
  const actions = [
    { label: "Pay", icon: SendHorizontal, tint: "bg-blue-50 text-[#1a73e8]", onClick: () => openSend() },
    { label: "Get paid", icon: Download, tint: "bg-amber-50 text-amber-700", to: "/get-paid" },
    { label: "Buy", icon: ShoppingBag, tint: "bg-emerald-50 text-emerald-700", to: "/buy" },
    { label: "AI Guard", icon: Bot, tint: "bg-purple-50 text-purple-700", to: "/assistant" },
  ];

  return (
    <div className="space-y-4">
      <button className="gbtn w-full bg-white p-3.5 rounded-full border border-slate-200 flex items-center gap-3 hover:bg-slate-50" onClick={() => openSend()}>
        <Search className="w-4 h-4 text-slate-500 ml-2" />
        <span className="text-sm text-slate-500">Pay any PayPal email (…@personal.example.com)</span>
      </button>

      <div className="grid gap-4 lg:grid-cols-3 lg:items-start">
      <div className="space-y-4 lg:col-span-2">

      <section className="rounded-3xl bg-white p-5 border border-slate-200 space-y-4">
        <div className="flex justify-between items-start">
          <div>
            <div className="text-xs text-slate-500 flex items-center gap-1"><WalletCards className="w-4 h-4 text-[#1a73e8]" /> {s?.month ?? "This month"} · net</div>
            <div className="text-3xl text-black mt-1">{s ? money(s.net, { sign: true }) : "—"}</div>
            <div className="text-xs text-slate-500 mt-1">{s ? <>In {money(s.money_in)} · Out {money(s.money_out)}</> : " "}</div>
            {bal && (
              <Link to="/account" className="text-xs text-[#1a73e8] no-underline mt-1.5 inline-block">
                PayPal balance {bal.available != null ? money(bal.available, { cur: bal.currency }) : "—"} available{bal.withheld ? ` · ${money(bal.withheld, { cur: bal.currency })} on hold` : ""}
              </Link>
            )}
          </div>
          <span className="bg-emerald-50 text-emerald-700 text-[11px] px-3 py-1 rounded-full border border-emerald-200 flex items-center gap-1">
            <ShieldCheck className="w-3.5 h-3.5" /> AI Shield on
          </span>
        </div>
        <div className="grid grid-cols-4 gap-2 pt-2 border-t border-slate-100">
          {actions.map((a) => {
            const inner = (
              <>
                <div className={`w-12 h-12 rounded-2xl flex items-center justify-center ${a.tint}`}><a.icon className="w-5 h-5" /></div>
                <span className="text-xs text-black">{a.label}</span>
              </>
            );
            return a.to ? (
              <Link key={a.label} to={a.to} className="flex flex-col items-center gap-1.5 p-2 rounded-2xl hover:bg-slate-50 no-underline">{inner}</Link>
            ) : (
              <button key={a.label} className="gbtn flex flex-col items-center gap-1.5 p-2 rounded-2xl hover:bg-slate-50" onClick={a.onClick}>{inner}</button>
            );
          })}
        </div>
      </section>

      {reqs && reqs.open_count > 0 && (
        <Link to="/account" className="flex items-center gap-3 p-4 rounded-3xl bg-white border border-slate-200 no-underline text-black hover:bg-slate-50">
          <div className="w-10 h-10 rounded-full bg-amber-50 text-amber-700 flex items-center justify-center shrink-0"><Clock className="w-5 h-5" /></div>
          <div className="flex-1 min-w-0">
            <div className="text-sm">{reqs.open_count} payment request{reqs.open_count > 1 ? "s" : ""} waiting to be paid</div>
            <div className="text-xs text-slate-500">{money(reqs.open_total)} in PayPal invoices you sent</div>
          </div>
          <ChevronRight className="w-4 h-4 text-slate-400" />
        </Link>
      )}

      {data && data.in_progress.length > 0 && (
        <section className="bg-white rounded-3xl border border-slate-200 overflow-hidden">
          <div className="px-4 pt-4 pb-1 text-sm text-black">Needs your attention</div>
          {data.in_progress.map(({ transaction: t, insight: i }) => (
            <button key={t.id} className="gbtn w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-50 text-left border-t border-slate-100 first-of-type:border-0" onClick={() => openTx(t.id)}>
              <div className={`w-10 h-10 rounded-full flex items-center justify-center shrink-0 ${t.status === "HELD_FOR_REVIEW" ? "bg-red-50 text-red-600" : "bg-blue-50 text-[#1a73e8]"}`}>
                {t.status === "HELD_FOR_REVIEW" ? <ShieldAlert className="w-5 h-5" /> : <Clock className="w-5 h-5" />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-sm text-black truncate">{i.title} · {t.counterparty}</div>
                <div className="text-xs text-slate-500 truncate">{money(t.amount)} · {i.estimate ?? i.likely_outcome}</div>
              </div>
              <ChevronRight className="w-4 h-4 text-slate-400" />
            </button>
          ))}
        </section>
      )}

      <section className="bg-white rounded-3xl border border-slate-200 overflow-hidden">
        <div className="flex justify-between items-center px-4 pt-4 pb-1">
          <h3 className="m-0 text-sm text-black">Recent activity</h3>
          <Link to="/activity" className="text-xs text-[#1a73e8] no-underline">Show all</Link>
        </div>
        {!data ? <div className="p-4 text-sm text-slate-500">Loading…</div> :
          data.recent.length === 0 ? <div className="p-4 text-sm text-slate-500">No payments yet.</div> :
          data.recent.slice(0, 8).map((t) => <PayRow key={t.id} tx={t} onOpen={openTx} />)}
      </section>

      </div>

      <div className="space-y-4">
      <section className="bg-white p-4 rounded-3xl border border-slate-200 space-y-3">
        <div className="flex justify-between items-center">
          <h3 className="m-0 text-sm text-black">People</h3>
          <button className="gbtn text-xs text-[#1a73e8]" onClick={() => openSend()}>New payment</button>
        </div>
        {people.length === 0 ? (
          <div className="text-sm text-slate-500">People you pay will appear here. <button className="gbtn text-[#1a73e8]" onClick={() => openSend()}>Pay someone</button></div>
        ) : (
          <div className="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-4 gap-y-4 gap-x-2">
            {people.map((p) => (
              <button key={p.to} className="gbtn flex flex-col items-center gap-1.5" onClick={() => openSend({ to: p.to, name: p.name === p.to ? "" : p.name })} title={p.to}>
                <Avatar name={p.name} seed={p.to} size={52} />
                <span className="text-xs text-black truncate w-full text-center">{p.name.startsWith("+") ? `…${p.name.slice(-4)}` : p.name.split(" ")[0]}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      <div className="p-4 rounded-2xl bg-emerald-50 border border-emerald-200 flex items-center gap-3">
        <div className="p-2.5 bg-emerald-100 rounded-2xl text-emerald-700 shrink-0"><ShieldCheck className="w-5 h-5" /></div>
        <div className="flex-1">
          <div className="text-sm text-emerald-900">AI Risk Shield is on</div>
          <div className="text-xs text-emerald-800">Payments only go to PayPal sandbox emails, and every payment is checked for scams, duplicates and unusual amounts before money moves{data && data.risk_blocked_count > 0 ? ` · ${data.risk_blocked_count} high-risk caught` : ""}.</div>
        </div>
      </div>

      <section className="bg-white rounded-3xl border border-slate-200 overflow-hidden">
        {[
          { to: "/buy", label: "Buy with an agent: compare, bargain, check out", icon: ShoppingBag },
          { to: "/sell", label: "Sell with an agent", icon: Store },
          { to: "/agents?tab=jobs", label: "Payment agents: jobs, schedules, deals", icon: Zap },
          { to: "/account", label: "PayPal account, balance & payment requests", icon: WalletCards },
          { to: "/insights", label: "Spending insights & budgets", icon: PieChart },
          { to: "/studio", label: "Studio dashboards (AG Studio)", icon: BarChart3 },
        ].map((l) => (
          <Link key={l.to} to={l.to} className="flex items-center gap-3 px-4 py-3.5 text-black no-underline hover:bg-slate-50 border-t border-slate-100 first:border-0">
            <l.icon className="w-5 h-5 text-slate-600" /><span className="flex-1 text-sm">{l.label}</span><ChevronRight className="w-4 h-4 text-slate-400" />
          </Link>
        ))}
      </section>
      </div>
      </div>
    </div>
  );
}
