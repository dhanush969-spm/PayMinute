import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Lightbulb, Loader2, Repeat, Sparkles, Wand2 } from "lucide-react";
import { api, Budget, Forecast, MonthSummary, Recommendation } from "../api";
import { currencySymbol, getLocale, money } from "../format";
import { Card, EmptyState, Skeleton, useApp } from "../components/ui";

interface InsightsData {
  summary: MonthSummary;
  forecast: Forecast;
  budgets: Budget[];
  suggested_budgets: Record<string, number>;
  recurring: { counterparty: string; category: string; cadence: string; typical_amount: number; monthly_cost: number; next_expected: string; occurrences: number }[];
  recommendations: Recommendation[];
  monthly_trend: { month: string; money_in: number; money_out: number }[];
}
interface Coach { source: "llm" | "rules"; summary: string | null; tips?: string[]; recommendations: Recommendation[] }

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#0d7c74";
}

export default function Insights() {
  const { config, version, toast, bump } = useApp();
  const [data, setData] = useState<InsightsData | null>(null);
  const [coach, setCoach] = useState<Coach | null>(null);
  const [coaching, setCoaching] = useState(false);
  const [editCat, setEditCat] = useState("");
  const [editLimit, setEditLimit] = useState("");

  useEffect(() => {
    api.get<InsightsData>("/insights").then(setData).catch((e) => toast(e.message, "error"));
  }, [version, toast]);

  useEffect(() => {
    if (location.hash === "#budgets") setTimeout(() => document.getElementById("budgets")?.scrollIntoView({ behavior: "smooth" }), 300);
  }, []);

  const colors = useMemo(() => ({ in: cssVar("--chart-in"), out: cssVar("--chart-out"), grid: cssVar("--line"), ink: cssVar("--ink-3") }), [data]);

  async function askCoach() {
    setCoaching(true);
    try { setCoach(await api.get<Coach>("/insights/coach")); }
    catch (e) { toast((e as Error).message, "error"); }
    finally { setCoaching(false); }
  }

  async function saveBudget(category: string, limit: number) {
    try {
      await api.put("/budgets", { category, monthly_limit: limit });
      toast(limit > 0 ? `Budget saved: ${category} ${money(limit)}/month` : `Removed ${category} budget`);
      setEditCat(""); setEditLimit("");
      bump();
    } catch (e) { toast((e as Error).message, "error"); }
  }

  async function applySuggested() {
    if (!data) return;
    for (const [c, v] of Object.entries(data.suggested_budgets)) await api.put("/budgets", { category: c, monthly_limit: v });
    toast("Applied suggested budgets");
    bump();
  }

  if (!data) return <div className="stack"><Skeleton h={40} w={240} /><Skeleton h={120} /><Skeleton h={260} /></div>;
  const s = data.summary;
  const maxCat = Math.max(1, ...s.by_category.map((c) => Math.max(c.amount, c.previous)));
  const trend = data.monthly_trend.map((m) => ({ ...m, label: new Date(m.month + "-15").toLocaleDateString(getLocale(), { month: "short" }) }));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Insights</h1>
          <p>{s.month}: where your money goes, what's coming, and how to stay on budget.</p>
        </div>
        <button className="btn primary" onClick={askCoach} disabled={coaching}>
          {coaching ? <Loader2 size={16} /> : <Wand2 size={16} />} Get AI advice
        </button>
      </div>

      <div className="stack">
        <div className="stat-tiles">
          <div className="card tile"><div className="k">Money in</div><div className="v num">{money(s.money_in, { compact: true })}</div><div className="d">Last month {money(s.prev_money_in, { compact: true })}</div></div>
          <div className="card tile"><div className="k">Money out</div><div className="v num">{money(s.money_out, { compact: true })}</div><div className="d">Last month {money(s.prev_money_out, { compact: true })}</div></div>
          <div className="card tile"><div className="k">Forecast spend</div><div className="v num">{money(data.forecast.money_out, { compact: true })}</div><div className="d">by month end · {data.forecast.confidence} confidence</div></div>
          <div className="card tile"><div className="k">Forecast net</div><div className="v num" style={{ color: data.forecast.net >= 0 ? "var(--success)" : "var(--danger)" }}>{money(data.forecast.net, { sign: true, compact: true })}</div><div className="d">day {s.day_of_month} of {s.days_in_month}</div></div>
        </div>

        {coach && (
          <section className="card card-pad" style={{ borderColor: "color-mix(in srgb, var(--brand) 35%, var(--line))" }}>
            <div className="row" style={{ marginBottom: 8 }}><Sparkles size={18} color="var(--brand)" /><strong>{coach.source === "llm" ? `AI coach · ${config?.llm.model}` : "PayMinute's analysis"}</strong></div>
            {coach.summary && <p style={{ margin: "0 0 8px" }}>{coach.summary}</p>}
            {coach.tips?.length ? <ul style={{ margin: 0, paddingLeft: 20 }}>{coach.tips.map((t) => <li key={t} style={{ marginBottom: 6 }}>{t}</li>)}</ul> :
              <div className="small muted">Showing rule-based findings below.</div>}
          </section>
        )}

        <div className="grid half">
          <Card title="Money in vs out" action={<div className="legend"><span><i className="sw" style={{ background: colors.in }} />In</span><span><i className="sw" style={{ background: colors.out }} />Out</span></div>}>
            <div className="chart-wrap" role="img" aria-label="Bar chart of money in and out over the last four months">
              <ResponsiveContainer>
                <BarChart data={trend} barGap={4} margin={{ top: 8, right: 4, left: -8, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke={colors.grid} />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: colors.ink, fontSize: 12 }} />
                  <YAxis tickLine={false} axisLine={false} tick={{ fill: colors.ink, fontSize: 12 }} tickFormatter={(v) => `${currencySymbol()}${v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v}`} />
                  <Tooltip cursor={{ fill: "rgba(127,127,127,0.08)" }} formatter={(v: number) => money(v)} contentStyle={{ borderRadius: 10, border: `1px solid ${colors.grid}` }} />
                  <Bar dataKey="money_in" name="Money in" fill={colors.in} radius={[6, 6, 0, 0]} maxBarSize={28} />
                  <Bar dataKey="money_out" name="Money out" fill={colors.out} radius={[6, 6, 0, 0]} maxBarSize={28} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card title="Spending by category" action={<span className="tiny muted">bar = this month · line = last month</span>}>
            {s.by_category.length === 0 ? <EmptyState title="No spending yet this month" /> : s.by_category.slice(0, 8).map((c) => (
              <div className="cat-bar" key={c.category}>
                <span className="small" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.category}</span>
                <div className="track"><span style={{ width: `${(100 * c.amount) / maxCat}%` }} />{c.previous > 0 && <i style={{ left: `${(100 * c.previous) / maxCat}%` }} />}</div>
                <span className="small num" style={{ textAlign: "right" }}>{money(c.amount, { compact: true })}</span>
              </div>
            ))}
          </Card>
        </div>

        <div className="grid half">
          <section className="card" id="budgets">
            <div className="card-head"><h2>Budgets</h2>
              {Object.keys(data.suggested_budgets).length > 0 && data.budgets.length === 0 && <button className="btn ghost sm" onClick={applySuggested}>Use suggested</button>}
            </div>
            <div className="card-body">
              {data.budgets.map((b) => (
                <div className="budget" key={b.category}>
                  <div className="row between">
                    <span className="strong small">{b.category}</span>
                    <button className="btn ghost sm" onClick={() => { setEditCat(b.category); setEditLimit(String(b.limit)); }} aria-label={`Edit ${b.category} budget`}>Edit</button>
                  </div>
                  <div className="row between small"><span className="num">{money(b.spent)} of {money(b.limit)}</span><span className="num muted">forecast {money(b.forecast, { compact: true })}</span></div>
                  <div className="bar"><div className={`fill ${b.state}`} style={{ width: `${Math.min(100, b.percent)}%` }} /><div className="fc" style={{ left: `${Math.min(99, (100 * b.forecast) / b.limit)}%` }} /></div>
                </div>
              ))}
              <form className="row wrap" style={{ marginTop: 14 }} onSubmit={(e) => { e.preventDefault(); if (editCat) saveBudget(editCat, parseFloat(editLimit) || 0); }}>
                <select className="select" style={{ flex: "1 1 180px", height: 42 }} value={editCat} onChange={(e) => setEditCat(e.target.value)} aria-label="Budget category">
                  <option value="">Choose category…</option>
                  {config?.categories.expense.map((c) => <option key={c}>{c}</option>)}
                </select>
                <input className="input num" style={{ width: 120, height: 42 }} placeholder="Monthly $" inputMode="decimal" value={editLimit} onChange={(e) => setEditLimit(e.target.value.replace(/[^0-9.]/g, ""))} aria-label="Monthly limit" />
                <button className="btn primary sm" disabled={!editCat}>Save</button>
              </form>
              {Object.keys(data.suggested_budgets).length > 0 && (
                <div className="tiny muted" style={{ marginTop: 10 }}>Suggested from your last two months (10% under average): {Object.entries(data.suggested_budgets).map(([c, v]) => `${c} ${money(v, { compact: true })}`).join(" · ")}</div>
              )}
            </div>
          </section>

          <Card title={<h2 className="row" style={{ gap: 8 }}><Repeat size={16} /> Recurring payments</h2>}>
            {data.recurring.length === 0 ? <div className="small muted">No recurring payments detected yet. PayMinute looks for weekly or monthly repeats.</div> : (
              <table className="simple">
                <thead><tr><th>To</th><th>Cadence</th><th style={{ textAlign: "right" }}>Per month</th></tr></thead>
                <tbody>
                  {data.recurring.map((r) => (
                    <tr key={r.counterparty}><td><div className="strong small">{r.counterparty}</div><div className="tiny muted">{r.category} · next ≈ {new Date(r.next_expected).toLocaleDateString(getLocale(), { month: "short", day: "numeric" })}</div></td>
                      <td className="small">{r.cadence}</td><td className="num small" style={{ textAlign: "right" }}>{money(r.monthly_cost)}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>

        <Card title="Recommendations">
          {data.recommendations.length === 0 ? <div className="small muted">Everything looks on track.</div> : data.recommendations.map((r) => (
            <div className="tip" key={r.title}>
              <div className="t"><Lightbulb size={16} color={r.severity === "high" ? "var(--danger)" : r.severity === "medium" ? "var(--warn)" : "var(--brand)"} />{r.title}</div>
              <div className="b">{r.body}</div>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
