import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { RefreshCw, Search } from "lucide-react";
import { api, Tx } from "../api";
import { useApp } from "../components/ui";
import { PayRow } from "./shared";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "in", label: "Received" },
  { key: "out", label: "Paid" },
  { key: "progress", label: "Pending" },
  { key: "risk", label: "Flagged" },
] as const;

export default function GHistory() {
  const { version, openTx, toast, bump } = useApp();
  const [rows, setRows] = useState<Tx[] | null>(null);
  const [q, setQ] = useState("");
  const [params] = useSearchParams();
  const [f, setF] = useState<(typeof FILTERS)[number]["key"]>((params.get("f") as "progress") || "all");
  const [syncing, setSyncing] = useState(false);

  useEffect(() => { api.get<Tx[]>("/transactions?limit=500").then(setRows).catch((e) => toast(e.message, "error")); }, [version, toast]);

  const groups = useMemo(() => {
    const list = (rows ?? []).filter((t) => {
      if (f === "in" || f === "out") { if (t.direction !== f) return false; }
      if (f === "progress" && !["PENDING", "UNCLAIMED", "ONHOLD", "HELD_FOR_REVIEW", "AUTHORIZED"].includes(t.status)) return false;
      if (f === "risk" && !(t.risk_level === "high" || t.risk_level === "medium")) return false;
      const s = q.trim().toLowerCase();
      return !s || [t.counterparty, t.counterparty_email, t.counterparty_phone, t.memo, t.category].some((v) => (v || "").toLowerCase().includes(s));
    });
    const out: { label: string; items: Tx[] }[] = [];
    for (const t of list) {
      const label = new Date(t.created_at).toLocaleDateString(undefined, { month: "long", year: "numeric" });
      if (!out.length || out[out.length - 1].label !== label) out.push({ label, items: [] });
      out[out.length - 1].items.push(t);
    }
    return out;
  }, [rows, q, f]);

  async function sync() {
    setSyncing(true);
    try {
      const r = await api.post<{ fetched: number; added: number; note?: string | null }>("/sync");
      toast(r.note ? `PayPal: ${r.fetched} found, ${r.added} new. ${r.note}` : `PayPal: ${r.fetched} found, ${r.added} new`);
      bump();
    } catch (e) { toast((e as Error).message, "error"); }
    finally { setSyncing(false); }
  }

  return (
    <div className="space-y-3 max-w-4xl mx-auto">
      <div className="flex flex-col lg:flex-row lg:items-center gap-3">
      <div className="flex items-center gap-2 lg:flex-1">
        <div className="flex-1 bg-white rounded-full border border-slate-200 flex items-center gap-2 px-4">
          <Search className="w-4 h-4 text-slate-500" />
          <input className="flex-1 bg-transparent border-0 outline-none py-3 text-sm text-black" placeholder="Search payments" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search payments" />
        </div>
        <button className="gbtn w-11 h-11 rounded-full bg-white border border-slate-200 flex items-center justify-center text-slate-700" onClick={sync} disabled={syncing} title="Sync from PayPal" aria-label="Sync from PayPal">
          <RefreshCw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} />
        </button>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1 lg:pb-0">
        {FILTERS.map((x) => (
          <button key={x.key} onClick={() => setF(x.key)}
            className={`gbtn shrink-0 px-4 py-1.5 rounded-full text-sm border ${f === x.key ? "bg-blue-50 border-blue-200 text-[#1558b0]" : "bg-white border-slate-200 text-black"}`}>
            {x.label}
          </button>
        ))}
      </div>
      </div>
      {!rows ? <div className="text-sm text-slate-500 p-4">Loading…</div> : groups.length === 0 ? (
        <div className="bg-white rounded-3xl border border-slate-200 p-8 text-center text-sm text-slate-500">No payments found.</div>
      ) : groups.map((g) => (
        <section key={g.label} className="bg-white rounded-3xl border border-slate-200 overflow-hidden">
          <div className="px-4 pt-3 pb-1 text-xs text-slate-500">{g.label}</div>
          {g.items.map((t) => <PayRow key={t.id} tx={t} onOpen={openTx} />)}
        </section>
      ))}
    </div>
  );
}
