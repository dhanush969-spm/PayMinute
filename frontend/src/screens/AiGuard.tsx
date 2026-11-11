import { useEffect, useRef, useState } from "react";
import { Bot, Check, CheckCircle2, Mic, MicOff, RefreshCw, SendHorizontal, ShieldCheck, Volume2, VolumeX, X, XCircle } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { AgentAction, AgentStep, api, newRef } from "../api";
import { Markdown, RiskPanel, useApp } from "../components/ui";

interface Msg { role: "user" | "bot"; text: string; steps?: AgentStep[]; actions?: AgentAction[]; by?: string }
interface AiEvent { at: string; kind: "rate_limit" | "error"; feature: string; provider?: string; model?: string; detail: string }
const welcome = (name?: string): Msg => ({ role: "bot", text: `Hi${name ? ` ${name.split(" ")[0]}` : ""}! I'm PayMinute AI Guard. I check payments to PayPal emails for fraud, explain stuck payments, and act for you through PayPal: pay contractors when jobs are done, run scheduled payments, and negotiate prices with sellers' agents. Anything that moves money waits for your approval.` });
// "Try asking": plain prompts with no sample data. Ones that need details (who, how much) are put in the box
// for you to finish; the rest are sent straight away.
const SUGGESTIONS: { label: string; start?: string }[] = [
  { label: "Verify a payment", start: "Is it safe to pay " },
  { label: "Pay someone", start: "Send " },
  { label: "Create a job", start: "Create a job " },
  { label: "Schedule a payment", start: "Schedule a payment to " },
  { label: "Negotiate a price", start: "Negotiate " },
  { label: "Buy something", start: "Buy " },
  { label: "Split a bill", start: "Split " },
  { label: "Which payments are stuck?" },
  { label: "Where did my money go this month?" },
  { label: "What did my agents do?" },
];

function sid(): string {
  try { const v = sessionStorage.getItem("payminute.session"); if (v) return v; const n = newRef(); sessionStorage.setItem("payminute.session", n); return n; }
  catch { return newRef(); }
}

export default function GAiGuard() {
  const { bump, toast, config } = useApp();
  const [msgs, setMsgs] = useState<Msg[]>(() => [welcome(config?.user?.name)]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const session = useRef(sid());
  const navigate = useNavigate();
  // voice: speak a request, optionally hear the reply (browser speech APIs, nothing leaves the device but the text)
  const Speech = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
  const [listening, setListening] = useState(false);
  const [speak, setSpeak] = useState(false);
  const recog = useRef<any>(null);
  function listen() {
    if (!Speech) { toast("Voice needs Chrome or Edge", "error"); return; }
    if (listening) { recog.current?.stop(); return; }
    const r = new Speech();
    r.lang = navigator.language || "en-US";
    r.interimResults = true;
    r.onresult = (e: any) => {
      const text = Array.from(e.results).map((x: any) => x[0].transcript).join(" ");
      setInput(text);
      if (e.results[e.results.length - 1].isFinal) { r.stop(); ask(text); }
    };
    r.onend = () => setListening(false);
    r.onerror = () => setListening(false);
    recog.current = r;
    setListening(true);
    r.start();
  }
  const [aiStatus, setAiStatus] = useState<{ ok: boolean; provider: string; model?: string; detail: string; rate_limited?: boolean; events?: AiEvent[] } | null>(null);
  useEffect(() => { api.get<typeof aiStatus>("/ai/status").then(setAiStatus).catch(() => setAiStatus({ ok: false, provider: "?", detail: "AI status check failed" })); }, []);
  // the latest rate limit in the last 15 minutes, from any agent (bargaining, chat, pricing)
  const limit = aiStatus?.events?.find((e) => e.kind === "rate_limit" && Date.now() - Date.parse(e.at) < 15 * 60 * 1000);
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }); }, [msgs, busy]);

  const box = useRef<HTMLInputElement>(null);
  // a prompt that needs details goes in the box for you to finish; a complete one is sent
  function pick(s: { label: string; start?: string }) {
    if (!s.start) { ask(s.label); return; }
    setInput(s.start);
    setTimeout(() => { const el = box.current; if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } }, 0);
  }

  async function ask(text: string) {
    if (!text.trim() || busy) return;
    setMsgs((m) => [...m, { role: "user", text }]);
    setInput("");
    setBusy(true);
    try {
      const r = await api.post<{ reply: string; steps: AgentStep[]; pending_actions: AgentAction[]; navigate?: string | null; mode?: string; model?: string; notice?: { kind: string; text: string } }>("/agent/chat", { session_id: session.current, message: text });
      setMsgs((m) => [...m, { role: "bot", text: r.reply, steps: r.steps, actions: r.pending_actions, by: r.mode === "llm" ? r.model : undefined }]);
      if (r.notice) { toast(r.notice.text, "error"); api.get<typeof aiStatus>("/ai/status").then(setAiStatus).catch(() => undefined); }
      // the shopping agent made a deal: open the locked checkout (only our own app routes)
      if (r.navigate && /^\/checkout\/quo_[A-Za-z0-9_-]+$/.test(r.navigate)) setTimeout(() => navigate(r.navigate!), 2500);
      if (speak && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(new SpeechSynthesisUtterance(r.reply.replace(/[*_#>`]/g, "").split("\n\n")[0].slice(0, 300)));
      }
    } catch (e) {
      setMsgs((m) => [...m, { role: "bot", text: `Something went wrong: ${(e as Error).message}` }]);
    } finally { setBusy(false); }
  }

  async function decide(a: AgentAction, approve: boolean) {
    setBusy(true);
    try {
      const r = await api.post<{ action: AgentAction; reply: string }>(`/agent/actions/${a.id}`, { approve });
      setMsgs((m) => [...m.map((x): Msg => ({ ...x, actions: x.actions?.map((y) => (y.id === a.id ? r.action : y)) })), { role: "bot", text: r.reply }]);
      if (approve) bump();
    } catch (e) { toast((e as Error).message, "error"); }
    finally { setBusy(false); }
  }

  return (
    <div className="flex gap-4 h-[calc(100dvh-190px)] lg:h-[calc(100dvh-112px)] min-h-[460px]">
    <div className="flex flex-col flex-1 min-w-0 gap-3">
      <div className="flex items-center justify-between bg-white p-3.5 rounded-2xl border border-slate-200">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-xl bg-purple-50 text-purple-700"><Bot className="w-5 h-5" /></div>
          <div>
            <div className="text-sm text-black">PayMinute AI Guard</div>
            <div className={`text-[11px] flex items-center gap-1 ${aiStatus && !aiStatus.ok ? "text-amber-700" : "text-emerald-700"}`} title={aiStatus?.detail}>
              <span className={`w-1.5 h-1.5 rounded-full ${aiStatus && !aiStatus.ok ? "bg-amber-600" : "bg-emerald-600 animate-pulse"}`} />
              {aiStatus == null ? "Checking…" : aiStatus.provider === "planner" ? "PayPal Agent Toolkit · PayPal MCP server" : aiStatus.ok ? `${aiStatus.provider === "gemini" ? "Google Gemini" : aiStatus.provider} ${aiStatus.model ?? ""} · PayPal Agent Toolkit` : aiStatus.rate_limited ? `${aiStatus.model} rate limit hit · built-in planner for now` : "AI model offline · built-in planner"}
            </div>
            {limit && <div className="text-[11px] text-amber-800 mt-0.5" title={limit.detail}>Rate limit hit at {new Date(limit.at).toLocaleTimeString()} ({limit.feature}). PayMinute used its built-in rules for that step.</div>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="gbtn p-1.5 rounded-full text-slate-600 hover:bg-slate-100" onClick={() => setSpeak(!speak)} aria-label={speak ? "Stop reading replies aloud" : "Read replies aloud"} title={speak ? "Replies are read aloud" : "Read replies aloud"}>
            {speak ? <Volume2 className="w-4 h-4" /> : <VolumeX className="w-4 h-4" />}
          </button>
          <button className="gbtn text-xs text-slate-600" onClick={() => setMsgs([welcome(config?.user?.name)])}>Clear</button>
        </div>
      </div>

      <div ref={scroller} className="flex-1 overflow-y-auto space-y-3 pr-1" aria-live="polite">
        {msgs.map((m, i) => (
          <div key={i} className={`flex flex-col ${m.role === "user" ? "items-end" : "items-start"} gap-1.5`}>
            <div className={`max-w-[85%] px-4 py-3 rounded-2xl text-sm leading-relaxed ${m.role === "user" ? "bg-[#1a73e8] text-white rounded-tr-md" : "bg-white border border-slate-200 text-black rounded-tl-md"}`}>
              {m.role === "bot" ? <div className="gp-md"><Markdown text={m.text} /></div> : m.text}
              {m.role === "bot" && m.by && <div className="text-[10px] text-slate-400 mt-1">Answered by {m.by}</div>}
            </div>
            {m.steps && m.steps.length > 0 && (
              <div className="flex flex-wrap gap-1.5 max-w-[85%]">
                {m.steps.map((s, j) => (
                  <span key={j} className={`text-[11px] px-2 py-0.5 rounded-full flex items-center gap-1 ${!s.ok ? "bg-red-50 text-red-700" : s.source === "payminute" ? "bg-slate-100 text-slate-700" : "bg-blue-50 text-[#1558b0]"}`} title={s.error}>
                    {s.ok ? <Check className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                    {s.source === "paypal-agent-toolkit" ? "PayPal Toolkit" : s.source.startsWith("mcp:") ? `${s.source.slice(4)} MCP` : "PayMinute"} · {s.tool}{s.queued ? " · needs you" : ""}
                  </span>
                ))}
              </div>
            )}
            {m.actions?.map((a) => (
              <div key={a.id} className="w-full max-w-[85%] bg-white border border-slate-200 rounded-2xl overflow-hidden">
                <div className="p-3.5 flex gap-2.5 items-start">
                  <ShieldCheck className="w-5 h-5 text-[#1a73e8] shrink-0 mt-0.5" />
                  <div><div className="text-[11px] text-slate-500">Approval needed</div><div className="text-sm text-black">{a.summary}</div></div>
                </div>
                {a.risk && <div className="px-3.5 pb-3"><RiskPanel risk={a.risk} compact /></div>}
                <div className="px-3.5 pb-3.5 flex gap-2">
                  {a.status === "pending" ? (
                    <>
                      <button className={`gbtn px-4 py-2 rounded-full text-sm text-white flex items-center gap-1 ${a.risk?.level === "high" ? "bg-red-600" : "bg-[#1a73e8]"}`} disabled={busy} onClick={() => decide(a, true)}><Check className="w-4 h-4" /> Approve</button>
                      <button className="gbtn px-4 py-2 rounded-full text-sm bg-slate-100 text-black flex items-center gap-1" disabled={busy} onClick={() => decide(a, false)}><X className="w-4 h-4" /> Decline</button>
                    </>
                  ) : (
                    <span className={`text-xs px-2.5 py-1 rounded-full flex items-center gap-1 ${a.status === "executed" ? "bg-emerald-50 text-emerald-700" : a.status === "rejected" ? "bg-slate-100 text-slate-600" : "bg-red-50 text-red-700"}`}>
                      {a.status === "executed" ? <><CheckCircle2 className="w-3.5 h-3.5" /> Approved and done</> : a.status === "rejected" ? "Declined" : "Failed"}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        ))}
        {busy && <div className="bg-white border border-slate-200 text-slate-600 px-3.5 py-2.5 rounded-2xl text-sm inline-flex items-center gap-2"><RefreshCw className="w-4 h-4 animate-spin text-purple-600" /> Checking with PayPal…</div>}
      </div>

      {msgs.length <= 1 && (
        <div className="flex flex-wrap gap-2 lg:hidden">
          {SUGGESTIONS.map((s) => <button key={s.label} className="gbtn text-xs px-3 py-1.5 rounded-full bg-white border border-slate-200 text-black hover:border-[#1a73e8]" onClick={() => pick(s)}>{s.label}</button>)}
        </div>
      )}
      <form className="flex gap-2 bg-white p-2 rounded-full border border-slate-200" onSubmit={(e) => { e.preventDefault(); ask(input); }}>
        <input ref={box} className="flex-1 bg-transparent border-0 px-3 py-2 text-sm text-black outline-none" placeholder="Ask about payments, risk or spending…" value={input} onChange={(e) => setInput(e.target.value)} aria-label="Message" />
        <button type="button" className={`gbtn w-10 h-10 rounded-full flex items-center justify-center ${listening ? "bg-red-600 text-white animate-pulse" : "bg-slate-100 text-black"}`}
          onClick={listen} disabled={busy} aria-label={listening ? "Stop listening" : "Speak your request"} title="Speak your request">
          {listening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
        </button>
        <button className="gbtn w-10 h-10 bg-[#1a73e8] text-white rounded-full flex items-center justify-center disabled:opacity-50" disabled={busy || !input.trim()} aria-label="Send"><SendHorizontal className="w-4 h-4" /></button>
      </form>
    </div>

    {/* desktop: what to ask, always in view */}
    <aside className="hidden lg:flex w-80 shrink-0 flex-col gap-3 overflow-y-auto">
      <section className="bg-white rounded-2xl border border-slate-200 p-4 space-y-2">
        <div className="text-sm text-black">Try asking</div>
        {SUGGESTIONS.map((s) => (
          <button key={s.label} className="gbtn w-full text-left text-sm px-3 py-2 rounded-xl bg-slate-50 border border-slate-200 text-black hover:border-[#1a73e8] disabled:opacity-50" disabled={busy} onClick={() => pick(s)}>{s.label}</button>
        ))}
      </section>
      <section className="bg-white rounded-2xl border border-slate-200 p-4 space-y-2 text-xs text-slate-600">
        <div className="text-sm text-black flex items-center gap-1.5"><ShieldCheck className="w-4 h-4 text-emerald-600" /> How AI Guard keeps you safe</div>
        <div>Payments only go to PayPal sandbox emails (…@personal.example.com), never phone numbers.</div>
        <div>Every payment is scored for scams, duplicates and unusual amounts.</div>
        <div>Anything that moves money waits for you to approve it.</div>
      </section>
    </aside>
    </div>
  );
}
