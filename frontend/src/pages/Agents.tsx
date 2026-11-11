import { FormEvent, useEffect, useState } from "react";
import { BriefcaseBusiness, CalendarClock, Check, Handshake, Pause, Play, Settings2, ShoppingBag, Store, Zap } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { TypingDots, usePaced } from "./Market";
import { api } from "../api";
import { money } from "../format";
import { useApp } from "../components/ui";
import { Errors, FieldError, amountError, collect, invalid, payeeError, report } from "../components/form";

interface Job { id: string; title: string; payee: string; payee_name: string | null; amount: number; status: string; detail: string | null }
interface Schedule { id: string; payee: string; payee_name: string | null; amount: number; every: string; next_run: string | null; active: number; last_result: string | null; note: string | null }
interface Run { id: number; agent: string; outcome: string; summary: string; at: string }
interface Settings { auto_pay_max: number; daily_limit: number; allow_medium_risk: boolean; enabled: boolean }
interface Overview { settings: Settings; spent_today: number; jobs: Job[]; schedules: Schedule[]; runs: Run[] }
interface Msg { agent: "buyer" | "seller"; action: string; amount: number; text: string }
interface PayResult { outcome: string; summary: string; transcript?: Msg[]; price?: number | null; deal_id?: string }

const TABS = [
  { key: "jobs", label: "Jobs", icon: BriefcaseBusiness },
  { key: "schedules", label: "Scheduled", icon: CalendarClock },
  { key: "deal", label: "Agent deal", icon: Handshake },
  { key: "settings", label: "Limits", icon: Settings2 },
] as const;
const TONE: Record<string, string> = {
  paid: "bg-emerald-50 text-emerald-800 border-emerald-200", needs_approval: "bg-amber-50 text-amber-900 border-amber-200",
  blocked: "bg-red-50 text-red-800 border-red-200", failed: "bg-red-50 text-red-800 border-red-200",
  no_deal: "bg-slate-50 text-slate-700 border-slate-200", open: "bg-blue-50 text-[#1558b0] border-blue-200",
};
const field = "w-full bg-slate-50 border border-slate-200 rounded-2xl px-4 py-2.5 text-sm text-black outline-none focus:border-[#1a73e8]";
const primary = "gbtn px-4 py-2.5 rounded-full bg-[#1a73e8] text-white text-sm disabled:opacity-50 shrink-0 whitespace-nowrap";

function Pill({ status }: { status: string }) {
  return <span className={`text-[11px] px-2 py-0.5 rounded-full border ${TONE[status] ?? TONE.no_deal}`}>{status.replace("_", " ")}</span>;
}

export default function Agents() {
  const { toast, bump, version } = useApp();
  const [params] = useSearchParams();
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>((params.get("tab") as "jobs") || "jobs");
  const [data, setData] = useState<Overview | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState({ title: "", payee: "", amount: "" });
  const [sch, setSch] = useState({ payee: "", amount: "", every: "weekly", start: "", note: "" });
  const [deal, setDeal] = useState({ item: "", seller: "", budget: "" });
  const [dealResult, setDealResult] = useState<PayResult | null>(null);
  const paced = usePaced(dealResult?.transcript);
  const [limits, setLimits] = useState<Settings | null>(null);

  const load = () => api.get<Overview>("/agents").then((d) => { setData(d); setLimits(d.settings); }).catch((e) => toast(e.message, "error"));
  useEffect(() => { load(); }, [version]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run<T>(fn: () => Promise<T>, done?: (r: T) => void) {
    setBusy(true);
    try { const r = await fn(); done?.(r); await load(); bump(); }
    catch (e) { toast((e as Error).message, "error"); }
    finally { setBusy(false); }
  }

  const [err, setErr] = useState<Errors>({});
  const clear = (k: string) => err[k] && setErr({ ...err, [k]: "" });
  const check = (e: FormEvent, errors: Errors) => { setErr(errors); return report(errors, toast, e.currentTarget as HTMLElement); };
  const addJob = (e: FormEvent) => {
    e.preventDefault();
    if (!check(e, collect({ jobTitle: !job.title.trim() && "Enter what the job is, e.g. Logo design.", jobPayee: payeeError(job.payee, "the contractor's"), jobAmount: amountError(job.amount) }))) return;
    run(() => api.post("/agents/jobs", { title: job.title.trim(), payee: job.payee.trim(), amount: Number(job.amount) }), () => { setJob({ title: "", payee: "", amount: "" }); toast("Job added. Mark it complete when the work is done."); });
  };
  const complete = (j: Job, approve = false) => run(() => api.post<PayResult>(`/agents/jobs/${j.id}/complete${approve ? "?approve=true" : ""}`), (r) => toast(r.summary, r.outcome === "paid" ? undefined : "error"));
  const addSch = (e: FormEvent) => {
    e.preventDefault();
    if (!check(e, collect({
      schPayee: payeeError(sch.payee), schAmount: amountError(sch.amount),
      schStart: sch.start && new Date(sch.start).getTime() < Date.now() - 60000 && "The first payment date is in the past. Pick a future time or leave it empty.",
    }))) return;
    run(() => api.post("/agents/schedules", { payee: sch.payee, amount: parseFloat(sch.amount), every: sch.every,
      start: sch.start ? new Date(sch.start).toISOString() : null, note: sch.note || null }), () => setSch({ ...sch, payee: "", amount: "", note: "" }));
  };
  const schAction = (s: Schedule, action: "pause" | "resume" | "run") => run(() => api.post(`/agents/schedules/${s.id}/${action}`));
  const startDeal = (e: FormEvent) => {
    e.preventDefault();
    if (!check(e, collect({ dealItem: !deal.item.trim() && "Enter what you're buying.", dealSeller: payeeError(deal.seller, "the seller's"), dealBudget: amountError(deal.budget, "your budget") }))) return;
    setDealResult(null); run(() => api.post<PayResult>("/agents/deals", { item: deal.item.trim(), seller: deal.seller.trim(), budget: Number(deal.budget) }), setDealResult);
  };
  const approveDeal = () => dealResult?.deal_id && run(() => api.post<PayResult>("/agents/pay", { kind: "deal", ref: dealResult.deal_id! }),
    (r) => setDealResult({ ...dealResult, outcome: r.outcome, summary: r.summary }));
  const saveLimits = () => {
    if (!limits) return;
    const errors = collect({
      limAuto: !(limits.auto_pay_max >= 0) && "Enter the automatic payment limit (0 or more).",
      limDaily: !(limits.daily_limit >= 0) ? "Enter the daily limit (0 or more)." : limits.daily_limit < limits.auto_pay_max && "The daily limit can't be lower than the per-payment limit.",
    });
    setErr(errors);
    if (!report(errors, toast, document.getElementById("limits"))) return;
    run(() => api.put("/agents/settings", limits), () => toast("Limits saved"));
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
      <section className="bg-white rounded-3xl border border-slate-200 p-4 flex gap-3 items-start">
        <div className="p-2.5 rounded-2xl bg-purple-50 text-purple-700"><Zap className="w-5 h-5" /></div>
        <div className="text-sm text-slate-700">
          <div className="text-black">Payment agents</div>
          Agents buy and pay through PayPal on your behalf: they shop and check out for you, pay a contractor when the job is done,
          run scheduled payments and settle prices they negotiated with other agents. Every payment gets the same phone and risk checks as a manual one. Low-risk payments within your limits go out
          automatically; anything else waits for you.
          {data && <div className="text-xs text-slate-500 mt-1">Agents spent {money(data.spent_today)} today of your {money(data.settings.daily_limit)} daily limit.</div>}
        </div>
      </section>

      <div className="grid grid-cols-2 gap-3 lg:contents">
        <Link to="/buy" className="bg-white rounded-3xl border border-slate-200 p-4 no-underline text-black hover:border-[#1a73e8] flex flex-col">
          <div className="w-10 h-10 rounded-2xl bg-blue-50 text-[#1a73e8] flex items-center justify-center"><ShoppingBag className="w-5 h-5" /></div>
          <div className="text-sm mt-3">Buy with an agent</div>
          <div className="text-xs text-slate-500">Compares listings, bargains, opens a locked checkout</div>
          <div className="text-xs text-[#1a73e8] mt-auto pt-3">Open →</div>
        </Link>
        <Link to="/sell" className="bg-white rounded-3xl border border-slate-200 p-4 no-underline text-black hover:border-emerald-600 flex flex-col">
          <div className="w-10 h-10 rounded-2xl bg-emerald-50 text-emerald-700 flex items-center justify-center"><Store className="w-5 h-5" /></div>
          <div className="text-sm mt-3">Sell with an agent</div>
          <div className="text-xs text-slate-500">Your agent bargains and never goes under your lowest price</div>
          <div className="text-xs text-emerald-700 mt-auto pt-3">Open →</div>
        </Link>
      </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-start">
      <div className="space-y-4 min-w-0">

      <div className="flex gap-2 overflow-x-auto pb-1">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`gbtn shrink-0 px-4 py-1.5 rounded-full text-sm border flex items-center gap-1.5 ${tab === t.key ? "bg-blue-50 border-blue-200 text-[#1558b0]" : "bg-white border-slate-200 text-black"}`}>
            <t.icon className="w-4 h-4" /> {t.label}
          </button>
        ))}
      </div>

      {tab === "jobs" && (
        <div className="grid gap-4 xl:grid-cols-2 xl:items-start">
          <form onSubmit={addJob} noValidate className="bg-white rounded-3xl border border-slate-200 p-4 space-y-2.5">
            <div className="text-sm text-black">New job: pay when it's done</div>
            <div><input data-field="jobTitle" className={field + invalid(err.jobTitle)} placeholder="What's the job? e.g. Logo design" value={job.title} onChange={(e) => { setJob({ ...job, title: e.target.value }); clear("jobTitle"); }} aria-label="Job title" /><FieldError msg={err.jobTitle} /></div>
            <div><input data-field="jobPayee" className={field + invalid(err.jobPayee)} placeholder="Contractor's phone or PayPal email" value={job.payee} onChange={(e) => { setJob({ ...job, payee: e.target.value }); clear("jobPayee"); }} aria-label="Contractor" /><FieldError msg={err.jobPayee} /></div>
            <div className="flex gap-2 items-start">
              <div className="flex-1"><input data-field="jobAmount" className={field + invalid(err.jobAmount)} inputMode="decimal" placeholder="Amount" value={job.amount} onChange={(e) => { setJob({ ...job, amount: e.target.value.replace(/[^0-9.]/g, "") }); clear("jobAmount"); }} aria-label="Amount" /><FieldError msg={err.jobAmount} /></div>
              <button className={primary} disabled={busy}>Add job</button>
            </div>
          </form>
          <section className="bg-white rounded-3xl border border-slate-200 divide-y divide-slate-100">
            {!data?.jobs.length ? <div className="p-4 text-sm text-slate-500">No jobs yet.</div> : data.jobs.map((j) => (
              <div key={j.id} className="p-4 space-y-1.5">
                <div className="flex justify-between gap-2 items-center">
                  <div className="text-sm text-black truncate">{j.title}</div><Pill status={j.status} />
                </div>
                <div className="text-xs text-slate-500">{money(j.amount)} to {j.payee_name || j.payee}</div>
                {j.detail && <div className="text-xs text-slate-600">{j.detail}</div>}
                {j.status === "open" && <button className={primary} disabled={busy} onClick={() => complete(j)}><Check className="w-4 h-4 inline -mt-0.5" /> Mark complete and pay</button>}
                {j.status === "needs_approval" && <button className={`${primary} bg-amber-600`} disabled={busy} onClick={() => complete(j, true)}>Approve and pay {money(j.amount)}</button>}
              </div>
            ))}
          </section>
        </div>
      )}

      {tab === "schedules" && (
        <div className="grid gap-4 xl:grid-cols-2 xl:items-start">
          <form onSubmit={addSch} noValidate className="bg-white rounded-3xl border border-slate-200 p-4 space-y-2.5">
            <div className="text-sm text-black">Schedule a payment</div>
            <div><input data-field="schPayee" className={field + invalid(err.schPayee)} placeholder="Phone or PayPal email" value={sch.payee} onChange={(e) => { setSch({ ...sch, payee: e.target.value }); clear("schPayee"); }} aria-label="Payee" /><FieldError msg={err.schPayee} /></div>
            <div className="flex gap-2 items-start">
              <div className="flex-1"><input data-field="schAmount" className={field + invalid(err.schAmount)} inputMode="decimal" placeholder="Amount" value={sch.amount} onChange={(e) => { setSch({ ...sch, amount: e.target.value.replace(/[^0-9.]/g, "") }); clear("schAmount"); }} aria-label="Amount" /><FieldError msg={err.schAmount} /></div>
              <select className={field} value={sch.every} onChange={(e) => setSch({ ...sch, every: e.target.value })} aria-label="How often">
                <option value="once">Once</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
              </select>
            </div>
            <div><input data-field="schStart" className={field + invalid(err.schStart)} type="datetime-local" value={sch.start} onChange={(e) => { setSch({ ...sch, start: e.target.value }); clear("schStart"); }} aria-label="First payment (empty = now)" /><FieldError msg={err.schStart} /></div>
            <input className={field} placeholder="Note (optional)" value={sch.note} onChange={(e) => setSch({ ...sch, note: e.target.value })} aria-label="Note" />
            <button className={primary} disabled={busy}>Schedule</button>
            <div className="text-[11px] text-slate-500">Leave the date empty to make the first payment at the next check (within a minute).</div>
          </form>
          <section className="bg-white rounded-3xl border border-slate-200 divide-y divide-slate-100">
            {!data?.schedules.length ? <div className="p-4 text-sm text-slate-500">No scheduled payments.</div> : data.schedules.map((s) => (
              <div key={s.id} className="p-4 space-y-1.5">
                <div className="flex justify-between items-center gap-2">
                  <div className="text-sm text-black">{money(s.amount)} {s.every} to {s.payee_name || s.payee}</div>
                  <Pill status={s.active ? "open" : "no_deal"} />
                </div>
                <div className="text-xs text-slate-500">{s.next_run ? `Next: ${new Date(s.next_run).toLocaleString()}` : "Finished"}{s.note ? ` · ${s.note}` : ""}</div>
                {s.last_result && <div className="text-xs text-slate-600">Last run: {s.last_result}</div>}
                {s.last_result?.startsWith("Didn't pay") && !s.last_result.includes("isn't linked") && (
                  <button className={`${primary} bg-amber-600`} disabled={busy}
                    onClick={() => run(() => api.post<PayResult>("/agents/pay", { kind: "schedule", ref: s.id }),
                      (r) => toast(r.summary, r.outcome === "paid" ? undefined : "error"))}>Pay {money(s.amount)} now</button>
                )}
                <div className="flex gap-2">
                  {s.active ? <button className="gbtn text-xs px-3 py-1.5 rounded-full bg-slate-100 text-black" disabled={busy} onClick={() => schAction(s, "pause")}><Pause className="w-3.5 h-3.5 inline -mt-0.5" /> Pause</button>
                    : s.next_run && <button className="gbtn text-xs px-3 py-1.5 rounded-full bg-slate-100 text-black" disabled={busy} onClick={() => schAction(s, "resume")}><Play className="w-3.5 h-3.5 inline -mt-0.5" /> Resume</button>}
                  {s.next_run && <button className="gbtn text-xs px-3 py-1.5 rounded-full bg-blue-50 text-[#1558b0]" disabled={busy} onClick={() => schAction(s, "run")}>Run now</button>}
                </div>
              </div>
            ))}
          </section>
        </div>
      )}

      {tab === "deal" && (
        <div className="grid gap-4 xl:grid-cols-2 xl:items-start">
          <form onSubmit={startDeal} noValidate className="bg-white rounded-3xl border border-slate-200 p-4 space-y-2.5">
            <div className="text-sm text-black">Let your agent negotiate and pay</div>
            <div className="text-xs text-slate-500">Your buyer agent trades offers with the seller's agent, never goes above your budget, and pays the agreed price through PayPal. The seller's agent is simulated inside PayMinute.</div>
            <div><input data-field="dealItem" className={field + invalid(err.dealItem)} placeholder="What are you buying? e.g. Logo design" value={deal.item} onChange={(e) => { setDeal({ ...deal, item: e.target.value }); clear("dealItem"); }} aria-label="Item" /><FieldError msg={err.dealItem} /></div>
            <div><input data-field="dealSeller" className={field + invalid(err.dealSeller)} placeholder="Seller's phone or PayPal email" value={deal.seller} onChange={(e) => { setDeal({ ...deal, seller: e.target.value }); clear("dealSeller"); }} aria-label="Seller" /><FieldError msg={err.dealSeller} /></div>
            <div className="flex gap-2 items-start">
              <div className="flex-1"><input data-field="dealBudget" className={field + invalid(err.dealBudget)} inputMode="decimal" placeholder="Your budget" value={deal.budget} onChange={(e) => { setDeal({ ...deal, budget: e.target.value.replace(/[^0-9.]/g, "") }); clear("dealBudget"); }} aria-label="Budget" /><FieldError msg={err.dealBudget} /></div>
              <button className={primary} disabled={busy}>Start</button>
            </div>
          </form>
          {dealResult && (
            <section className="bg-white rounded-3xl border border-slate-200 p-4 space-y-2">
              {paced.shown.map((m, i) => (
                <div key={i} className={`flex ${m.agent === "buyer" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[80%] px-3.5 py-2 rounded-2xl text-sm ${m.agent === "buyer" ? "bg-[#1a73e8] text-white" : "bg-slate-100 text-black"}`}>
                    <div className="text-[10px] opacity-75">{m.agent === "buyer" ? "Your agent" : "Seller agent"}</div>{m.text}
                  </div>
                </div>
              ))}
              {paced.typingWho && <TypingDots who={paced.typingWho} />}
              {paced.finished && <div className={`p-3 rounded-2xl border text-sm ${TONE[dealResult.outcome] ?? TONE.no_deal}`}>{dealResult.summary}</div>}
              {paced.finished && dealResult.outcome === "needs_approval" && dealResult.price && <button className={`${primary} bg-amber-600`} disabled={busy} onClick={approveDeal}>Approve and pay {money(dealResult.price)}</button>}
            </section>
          )}
        </div>
      )}

      {tab === "settings" && limits && (
        <section id="limits" className="bg-white rounded-3xl border border-slate-200 p-4 space-y-3 text-sm max-w-xl">
          <label className="flex items-center justify-between gap-3"><span>Agents can pay automatically</span>
            <input type="checkbox" checked={limits.enabled} onChange={(e) => setLimits({ ...limits, enabled: e.target.checked })} /></label>
          <label className="block space-y-1"><span>Automatic payment limit (per payment)</span>
            <input data-field="limAuto" className={field + invalid(err.limAuto)} inputMode="decimal" value={limits.auto_pay_max} onChange={(e) => { setLimits({ ...limits, auto_pay_max: parseFloat(e.target.value) || 0 }); clear("limAuto"); }} /><FieldError msg={err.limAuto} /></label>
          <label className="block space-y-1"><span>Daily limit for all agents</span>
            <input data-field="limDaily" className={field + invalid(err.limDaily)} inputMode="decimal" value={limits.daily_limit} onChange={(e) => { setLimits({ ...limits, daily_limit: parseFloat(e.target.value) || 0 }); clear("limDaily"); }} /><FieldError msg={err.limDaily} /></label>
          <label className="flex items-center justify-between gap-3"><span>Also pay medium-risk payments automatically</span>
            <input type="checkbox" checked={limits.allow_medium_risk} onChange={(e) => setLimits({ ...limits, allow_medium_risk: e.target.checked })} /></label>
          <button className={primary} disabled={busy} onClick={saveLimits}>Save limits</button>
        </section>
      )}

      </div>

      <section className="bg-white rounded-3xl border border-slate-200 overflow-hidden lg:sticky lg:top-24 lg:max-h-[calc(100dvh-120px)] lg:overflow-y-auto">
        <div className="px-4 pt-4 pb-1 text-sm text-black">Agent activity</div>
        {!data?.runs.length ? <div className="p-4 text-sm text-slate-500">Nothing yet.</div> : data.runs.map((r) => (
          <div key={r.id} className="px-4 py-2.5 border-t border-slate-100 first-of-type:border-0 text-xs space-y-0.5">
            <div className="flex justify-between"><span className="text-slate-500">{new Date(r.at).toLocaleString()} · {r.agent}</span><Pill status={r.outcome} /></div>
            <div className="text-slate-700">{r.summary}</div>
          </div>
        ))}
      </section>
      </div>
    </div>
  );
}
