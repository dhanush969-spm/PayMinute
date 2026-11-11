import { FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Check, Minus, Plus, Search, ShoppingBag, Sparkles, Trophy } from "lucide-react";
import { api } from "../api";
import { money } from "../format";
import { useApp } from "../components/ui";
import { Errors, FieldError, FormAlert, amountError, collect, invalid, report } from "../components/form";
import { Candidate, Chips, Listing, Msg, Parsed, PriceHint, PriceInput, Quote, TrustBadge, field, parsePrompt, primary } from "./Market";

type Ev =
  | { type: "understood"; request: Parsed["request"]; budget: number | null; fair: PriceHint | null }
  | { type: "stage"; text: string }
  | { type: "candidates"; candidates: Candidate[] }
  | { type: "pick"; listing: Candidate; fair: number | null; reason: string; text: string }
  | ({ type: "message" } & Msg)
  | { type: "done"; outcome: string; summary: string; quote?: Quote; price?: number; reason?: string; listing?: Candidate };

const EXAMPLES = ["Camera with 2 lenses, like new, with box, under $300", "Road bike, used is fine, under $250", "Headphones with noise cancelling, good condition"];
const spec = (k: string, v: number) => `${v} ${v !== 1 ? k : k.endsWith("lenses") ? k.slice(0, -2) : k.replace(/s$/, "")}`;

function CompareCard({ c, rank, winner }: { c: Candidate; rank: number; winner: boolean }) {
  const specs = Object.entries(c.features.specs);
  return (
    <div className={`p-3.5 rounded-2xl border space-y-2 ${winner ? "border-[#1a73e8] bg-blue-50/40" : "border-slate-200 bg-white"}`}>
      <div className="flex justify-between gap-2 items-start">
        <div className="min-w-0">
          <div className="text-sm text-black flex items-center gap-1.5">
            {winner ? <Trophy className="w-4 h-4 text-[#1a73e8] shrink-0" /> : <span className="text-xs text-slate-400 w-4 text-center">{rank}</span>}
            <span className="truncate">{c.title}</span>
          </div>
          <div className="flex items-center gap-2 mt-1 text-xs text-slate-500 flex-wrap">{c.seller_name || "Seller"} <TrustBadge trust={c.trust} /></div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-sm text-black">{money(c.price)}</div>
          <div className="text-[11px] text-slate-500">score {Math.round(c.score * 100)}</div>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">{c.features.condition}</span>
        {specs.map(([k, v]) => <span key={k} className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">{spec(k, v)}</span>)}
      </div>
      {(c.has?.length || c.lacks?.length) ? (
        <div className="space-y-1">
          {!!c.has?.length && <Chips items={c.has.map((x) => `✓ ${x}`)} tone="green" />}
          {!!c.lacks?.length && <Chips items={c.lacks.map((x) => `✗ ${x}`)} tone="red" />}
        </div>
      ) : null}
      <div className="grid gap-1 text-xs">
        {c.pros.slice(0, 3).map((p) => <div key={p} className="flex gap-1.5 text-emerald-800"><Plus className="w-3.5 h-3.5 shrink-0" />{p}</div>)}
        {c.cons.filter((x) => !x.startsWith("you asked")).slice(0, 3).map((p) => <div key={p} className="flex gap-1.5 text-red-700"><Minus className="w-3.5 h-3.5 shrink-0" />{p}</div>)}
      </div>
    </div>
  );
}

function Bubble({ m }: { m: Msg }) {
  const mine = m.agent === "buyer";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"} animate-[fadeIn_.25s_ease-out]`}>
      <div className={`max-w-[85%] px-3.5 py-2 rounded-2xl text-sm ${mine ? "bg-[#1a73e8] text-white rounded-br-md" : "bg-slate-100 text-black rounded-bl-md"}`}>
        <div className="text-[10px] opacity-75 flex justify-between gap-3"><span>{mine ? "Your agent" : "Seller's agent"}</span>{m.action !== "ask" && m.amount ? <span>{money(m.amount)}</span> : null}</div>
        {m.text}
      </div>
    </div>
  );
}

export default function Buy() {
  const { toast, bump } = useApp();
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState("");
  const [budget, setBudget] = useState("");
  const [budgetTouched, setBudgetTouched] = useState(false);
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [err, setErr] = useState<Errors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<string[]>([]);
  const [cands, setCands] = useState<Candidate[]>([]);
  const [pick, setPick] = useState<Extract<Ev, { type: "pick" }> | null>(null);
  const [talk, setTalk] = useState<Msg[]>([]);
  const [done, setDone] = useState<Extract<Ev, { type: "done" }> | null>(null);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [forSale, setForSale] = useState<Listing[]>([]);
  const formRef = useRef<HTMLFormElement>(null);
  const chatEnd = useRef<HTMLDivElement>(null);

  useEffect(() => { api.get<{ listings: Listing[] }>("/market").then((d) => setForSale(d.listings)).catch(() => undefined); }, []);
  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [talk]);

  // read the prompt as it's typed: item, wanted features, budget, and the AI's fair price
  useEffect(() => {
    if (prompt.trim().length < 3) { setParsed(null); return; }
    const t = setTimeout(() => {
      parsePrompt(prompt, "buy").then((p) => {
        setParsed(p);
        if (!budgetTouched) {
          const b = p.request.price ?? p.suggestion?.price;
          setBudget(b ? String(b) : "");
        }
      }).catch(() => undefined);
    }, 450);
    return () => clearTimeout(t);
  }, [prompt]); // eslint-disable-line react-hooks/exhaustive-deps

  // after a deal, count down and open the locked checkout
  useEffect(() => {
    if (countdown === null || !done?.quote) return;
    if (countdown <= 0) { navigate(`/checkout/${done.quote.id}`); return; }
    const t = window.setTimeout(() => setCountdown(countdown - 1), 1000);
    return () => window.clearTimeout(t);
  }, [countdown, done, navigate]);

  // the agents' messages are shown one at a time, with a short "typing" pause that grows with the message,
  // however fast the server sends them
  const queue = useRef<Ev[]>([]);
  const pumping = useRef(false);
  const alive = useRef(true);
  const [revealing, setRevealing] = useState(false);
  const [typingWho, setTypingWho] = useState<"buyer" | "seller" | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []); // StrictMode mounts twice in dev
  const typingDelay = (m: Msg, first: boolean) =>
    first ? 700 : Math.min(2600, 900 + m.text.length * 14) * (0.85 + Math.random() * 0.3);

  function enqueue(ev: Ev) {
    queue.current.push(ev);
    if (!pumping.current) void pump();
  }

  async function pump() {
    pumping.current = true;
    setRevealing(true);
    let first = true;
    while (queue.current.length && alive.current) {
      const ev = queue.current.shift()!;
      if (ev.type === "message") {
        setTypingWho(ev.agent);
        await new Promise((ok) => setTimeout(ok, typingDelay(ev, first && ev.action === "ask")));
        first = false;
        setTypingWho(null);
      } else if (ev.type === "done" || ev.type === "stage") {
        await new Promise((ok) => setTimeout(ok, 500));
      }
      if (alive.current) handle(ev);
    }
    pumping.current = false;
    setRevealing(false);
  }

  function handle(ev: Ev) {
    if (ev.type === "stage") setStages((s) => [...s, ev.text]);
    else if (ev.type === "candidates") setCands(ev.candidates);
    else if (ev.type === "pick") { setPick(ev); setTalk([]); setStages((s) => [...s, ev.text]); }
    else if (ev.type === "message") setTalk((t) => [...t, ev]);
    else if (ev.type === "done") {
      setDone(ev);
      if (ev.outcome === "quote") setCountdown(5);
      else toast(ev.summary, "error");
    }
  }

  async function start(e: FormEvent) {
    e.preventDefault();
    setFailure(null);
    const errors = collect({
      prompt: !prompt.trim() ? "Tell your agent what to buy, e.g. “camera with 2 lenses, like new, under $300”." : prompt.trim().length < 3 && "Describe the item in a bit more detail.",
      budget: amountError(budget, "your max budget"),
    });
    setErr(errors);
    if (!report(errors, toast, formRef.current)) return;
    setRunning(true); setStages([]); setCands([]); setPick(null); setTalk([]); setDone(null); setCountdown(null);
    queue.current = [];
    try {
      const res = await fetch("/api/market/shop/stream", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: prompt.trim(), budget: Number(budget) }),
      });
      if (!res.ok || !res.body) {
        const detail = await res.json().then((d) => d.detail).catch(() => res.statusText);
        if (res.status === 401) window.dispatchEvent(new Event("payminute:logged-out"));
        throw new Error(typeof detail === "string" ? detail : "The shopping agent couldn't start.");
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done: end, value } = await reader.read();
        if (end) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, i).trim();
          buf = buf.slice(i + 1);
          if (line) enqueue(JSON.parse(line) as Ev);
        }
      }
      bump();
    } catch (ex) {
      const msg = `Your agent couldn't shop: ${(ex as Error).message}`;
      setFailure(msg); toast(msg, "error");
    } finally {
      setRunning(false);
    }
  }

  const hint = parsed?.suggestion;
  const req = parsed?.request;
  const active = running || revealing || !!done || talk.length > 0;
  const busy = running || revealing;
  const typing = !!typingWho;
  return (
    <div className="space-y-4">
      <form ref={formRef} onSubmit={start} noValidate className="bg-white rounded-3xl border border-slate-200 p-4 lg:p-5 space-y-3">
        <div className="text-sm text-black flex items-center gap-2"><ShoppingBag className="w-4 h-4 text-[#1a73e8]" /> Buy with an agent</div>
        <div className="text-xs text-slate-500">Describe what you want in one line: the item, the features and condition you need, and your budget. Your agent compares every listing, bargains live with the seller's agent, and opens a checkout with the seller and price filled in.</div>
        <div>
          <textarea data-field="prompt" className={`${field} min-h-[64px]${invalid(err.prompt)}`} placeholder="e.g. Camera with 2 lenses, like new, with box, under $300"
            value={prompt} onChange={(e) => { setPrompt(e.target.value); setErr({ ...err, prompt: "" }); }} aria-label="What to buy" maxLength={300} disabled={busy} />
          <FieldError msg={err.prompt} />
          {!prompt && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {EXAMPLES.map((x) => <button type="button" key={x} className="gbtn text-xs px-2.5 py-1 rounded-full bg-slate-50 border border-slate-200 text-black hover:border-[#1a73e8]" onClick={() => setPrompt(x)}>{x}</button>)}
            </div>
          )}
        </div>

        {req && (
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_340px] p-3 rounded-2xl bg-slate-50 border border-slate-200">
            <div className="space-y-1.5 min-w-0">
              <div className="text-xs text-slate-500">Your agent understood</div>
              <div className="text-sm text-black">{req.title || "—"}</div>
              {req.wanted.length > 0 ? <Chips items={req.wanted} /> : <div className="text-xs text-slate-500">No specific features or condition. Add some (e.g. “like new, 2 lenses”) and the agent will check every listing for them.</div>}
              {hint && (
                <div className="text-xs text-[#174ea6] flex items-start gap-1.5 pt-1">
                  <Sparkles className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  <span>AI fair price ≈ <b className="font-normal text-black">{money(hint.price)}</b> ({money(hint.low)}–{money(hint.high)}). {hint.basis}.</span>
                </div>
              )}
            </div>
            <div className="space-y-1">
              <div className="text-xs text-slate-500">Your max budget{!req.price && hint ? " (AI suggestion — raise it if you like)" : ""}</div>
              <PriceInput dataField="budget" label="Max budget" value={budget} error={err.budget} onChange={(v) => { setBudget(v); setBudgetTouched(true); setErr({ ...err, budget: "" }); }} />
              <FieldError msg={err.budget} />
              {hint && Number(budget) > 0 && Number(budget) < hint.low && <div className="text-[11px] text-amber-700">That's below the usual price, so sellers may refuse.</div>}
            </div>
          </div>
        )}
        <FormAlert msg={failure} onClose={() => setFailure(null)} />
        <button className={primary} disabled={busy}><Search className="w-4 h-4 inline -mt-0.5" /> {busy ? "Agent is working…" : "Start bargaining"}</button>
      </form>

      {active && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px] lg:items-start">
          <section className="space-y-2 min-w-0">
            <div className="bg-white rounded-2xl border border-slate-200 p-3 space-y-1.5" aria-live="polite">
              {stages.map((s, i) => (
                <div key={i} className="flex items-center gap-2 text-sm text-black">
                  {i < stages.length - 1 || done ? <Check className="w-4 h-4 text-emerald-600 shrink-0" /> : <span className="w-4 h-4 rounded-full border-2 border-[#1a73e8] border-t-transparent animate-spin shrink-0" />}
                  {s}
                </div>
              ))}
              {busy && !stages.length && <div className="text-sm text-slate-500">Reading your request…</div>}
            </div>
            {cands.length > 0 && <div className="text-xs text-slate-500 px-1">Compared {cands.length} listing{cands.length > 1 ? "s" : ""}: features you asked for are ✓ / ✗</div>}
            <div className="grid gap-3 md:grid-cols-2">
              {cands.map((c, i) => <CompareCard key={c.id} c={c} rank={i + 1} winner={c.id === pick?.listing.id} />)}
            </div>
          </section>

          <div className="space-y-3 lg:sticky lg:top-24">
            {pick && <div className="p-3 rounded-2xl border border-blue-200 bg-blue-50 text-sm text-[#1558b0]">{pick.reason}{pick.fair ? ` AI fair price for it: ${money(pick.fair)}.` : ""}</div>}
            {(talk.length > 0 || typing) && (
              <section className="bg-white rounded-3xl border border-slate-200 p-4 space-y-2 max-h-[60vh] overflow-y-auto">
                <div className="text-xs text-slate-500 flex items-center gap-1.5">{busy && <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />} {busy ? "Live: agents bargaining" : "How the agents bargained"}</div>
                {talk.map((m, i) => <Bubble key={i} m={m} />)}
                {typing && (
                  <div className={`flex ${typingWho === "buyer" ? "justify-end" : "justify-start"}`}>
                    <div className="px-3.5 py-2 rounded-2xl bg-slate-100 text-slate-500 text-sm flex gap-1" aria-label="Typing">
                      <span className="animate-bounce">•</span><span className="animate-bounce [animation-delay:150ms]">•</span><span className="animate-bounce [animation-delay:300ms]">•</span>
                    </div>
                  </div>
                )}
                <div ref={chatEnd} />
              </section>
            )}
            {done && <div className={`p-3 rounded-2xl border text-sm ${done.outcome === "quote" ? "bg-emerald-50 text-emerald-800 border-emerald-200" : "bg-amber-50 text-amber-900 border-amber-200"}`}>{done.summary}</div>}
            {done?.quote && (
              <button className={`${primary} w-full py-3`} onClick={() => navigate(`/checkout/${done.quote!.id}`)}>
                Go to checkout{countdown !== null && countdown > 0 ? ` (${countdown})` : ""} <ArrowRight className="w-4 h-4 inline -mt-0.5" />
              </button>
            )}
            {done && done.outcome !== "quote" && (
              <button className="gbtn w-full py-2.5 rounded-full bg-white border border-slate-200 text-sm text-black" onClick={() => { setBudgetTouched(true); setBudget(String(Math.round(Number(budget) * 1.1))); toast("Budget raised by 10%. Start bargaining again when you're ready."); }}>
                Raise my budget by 10%
              </button>
            )}
          </div>
        </div>
      )}

      {!active && (
        <section className="space-y-2">
          <div className="text-sm text-black px-1">For sale now</div>
          {!forSale.length ? <div className="bg-white rounded-3xl border border-slate-200 p-4 text-sm text-slate-500">Nothing listed yet. Log in as another PayPal account and list something on the Sell page.</div> : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {forSale.map((x) => (
                <button key={x.id} className="gbtn w-full text-left p-4 bg-white rounded-2xl border border-slate-200 hover:border-[#1a73e8]"
                  onClick={() => setPrompt(`${x.title}${x.description ? `, ${x.description}` : ""}`)}>
                  <div className="flex justify-between gap-2"><span className="text-sm text-black">{x.title}</span><span className="text-sm text-black">{money(x.price)}</span></div>
                  <div className="flex items-center gap-2 mt-1 text-xs text-slate-500">{x.seller_name || "Seller"} <TrustBadge trust={x.trust} /></div>
                  {x.description && <div className="text-xs text-slate-600 mt-1">{x.description}</div>}
                </button>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
