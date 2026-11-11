import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BarChart3, ChevronRight, Sparkles, TrendingUp } from "lucide-react";
import { api, Budget, Forecast, MonthSummary, Recommendation } from "../api";
import { money } from "../format";
import { useApp } from "../components/ui";

interface Data { summary: MonthSummary; forecast: Forecast; budgets: Budget[]; recommendations: Recommendation[] }
const BAR = ["#1a73e8", "#188038", "#e37400", "#9334e6", "#d93025", "#007b83", "#5f6368"];

export default function GInsights() {
  const { version, toast } = useApp();
  const [d, setD] = useState<Data | null>(null);
  const [coach, setCoach] = useState<{ summary: string | null; tips?: string[] } | null>(null);
  useEffect(() => { api.get<Data>("/insights").then(setD).catch((e) => toast(e.message, "error")); }, [version, toast]);

  async function ask() {
    try { setCoach(await api.get("/insights/coach")); } catch (e) { toast((e as Error).message, "error"); }
  }

  if (!d) return <div className="text-sm text-slate-500 p-4">Loading…</div>;
  const s = d.summary;
  const cats = s.by_category.slice(0, 6);
  const total = cats.reduce((a, c) => a + c.amount, 0) || 1;
  const onTrack = d.forecast.net >= 0;

  return (
    <div className="space-y-4">
      <h2 className="m-0 text-base text-black flex items-center gap-2 lg:hidden"><TrendingUp className="w-5 h-5 text-amber-600" /> Spending this month</h2>
      <div className="grid gap-4 lg:grid-cols-2 lg:items-start lg:!mt-0">
      <section className="p-5 rounded-3xl bg-white border border-slate-200 space-y-4">
        <div className="flex justify-between items-center text-xs text-slate-500">
          <span>{s.month} spend</span>
          <span className={`px-2.5 py-0.5 rounded-full border ${onTrack ? "text-emerald-700 bg-emerald-50 border-emerald-200" : "text-red-700 bg-red-50 border-red-200"}`}>{onTrack ? "On track" : "Spending > income"}</span>
        </div>
        <div className="text-3xl text-black">{money(s.money_out)}</div>
        <div className="text-xs text-slate-500">Forecast by month end: {money(d.forecast.money_out)} spent · {money(d.forecast.net, { sign: true })} net</div>
        <div className="w-full h-3 bg-slate-100 rounded-full overflow-hidden flex">
          {cats.map((c, i) => <div key={c.category} style={{ width: `${(100 * c.amount) / total}%`, background: BAR[i % BAR.length] }} title={c.category} />)}
        </div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs text-black">
          {cats.map((c, i) => (
            <span key={c.category} className="flex items-center gap-1.5 min-w-0">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: BAR[i % BAR.length] }} />
              <span className="truncate">{c.category}</span><span className="text-slate-500 ml-auto">{Math.round((100 * c.amount) / total)}%</span>
            </span>
          ))}
        </div>
      </section>

      <div className="space-y-4">
      <section className="p-4 rounded-2xl bg-blue-50 border border-blue-200 flex items-start gap-3">
        <Sparkles className="w-5 h-5 text-[#1a73e8] shrink-0 mt-0.5" />
        <div className="space-y-1.5 min-w-0">
          <div className="text-sm text-[#174ea6]">AI budget insights</div>
          {coach ? (
            <>
              {coach.summary && <div className="text-sm text-[#174ea6]">{coach.summary}</div>}
              {(coach.tips ?? []).map((t) => <div key={t} className="text-xs text-[#1a3a7a]">• {t}</div>)}
              {!coach.tips?.length && d.recommendations.slice(0, 3).map((r) => <div key={r.title} className="text-xs text-[#1a3a7a]">• {r.title}: {r.body}</div>)}
            </>
          ) : (
            <>
              {d.recommendations.slice(0, 2).map((r) => <div key={r.title} className="text-xs text-[#1a3a7a]">• {r.title}. {r.body}</div>)}
              <button className="gbtn text-xs text-[#1a73e8]" onClick={ask}>Get personalised AI advice</button>
            </>
          )}
        </div>
      </section>
      </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2 lg:items-start">
      {d.budgets.length > 0 && (
        <section className="bg-white rounded-3xl border border-slate-200 p-4 space-y-3">
          <div className="text-sm text-black">Budgets</div>
          {d.budgets.map((b) => (
            <div key={b.category} className="space-y-1">
              <div className="flex justify-between text-xs"><span className="text-black">{b.category}</span><span className="text-slate-500">{money(b.spent)} / {money(b.limit)}</span></div>
              <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                <div className="h-full rounded-full" style={{ width: `${Math.min(100, b.percent)}%`, background: b.state === "over" ? "#d93025" : b.state === "at_risk" ? "#e37400" : "#1a73e8" }} />
              </div>
              {b.state !== "ok" && <div className="text-[11px] text-amber-700">Forecast {money(b.forecast)} by month end</div>}
            </div>
          ))}
        </section>
      )}

      <section className="bg-white rounded-3xl border border-slate-200 overflow-hidden">
        <Link to="/insights/full" className="flex items-center gap-3 px-4 py-3.5 text-black no-underline hover:bg-slate-50">
          <TrendingUp className="w-5 h-5 text-slate-600" /><span className="flex-1 text-sm">Edit budgets, recurring payments, charts</span><ChevronRight className="w-4 h-4 text-slate-400" />
        </Link>
        <Link to="/studio" className="flex items-center gap-3 px-4 py-3.5 text-black no-underline hover:bg-slate-50 border-t border-slate-100">
          <BarChart3 className="w-5 h-5 text-slate-600" /><span className="flex-1 text-sm">Studio dashboards (AG Studio)</span><ChevronRight className="w-4 h-4 text-slate-400" />
        </Link>
      </section>
      </div>
    </div>
  );
}
