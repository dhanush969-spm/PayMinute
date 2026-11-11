import { FormEvent, useEffect, useRef, useState } from "react";
import { Sparkles, Store, Trash2, Wallet } from "lucide-react";
import { api } from "../api";
import { money } from "../format";
import { useApp } from "../components/ui";
import { EMAIL_RE, Errors, FieldError, FormAlert, amountError, collect, invalid, report } from "../components/form";
import { Chips, Listing, Parsed, PriceInput, decimal, field, parsePrompt, primary } from "./Market";

const STATUS: Record<string, string> = {
  active: "bg-blue-50 text-[#1558b0] border-blue-200", reserved: "bg-amber-50 text-amber-900 border-amber-200",
  sold: "bg-emerald-50 text-emerald-800 border-emerald-200",
};
const EMPTY = { prompt: "", title: "", price: "", floor: "", description: "", email: "", phone: "", name: "" };

export default function Sell() {
  const { toast, bump, config, reloadConfig } = useApp();
  const [busy, setBusy] = useState(false);
  const [mine, setMine] = useState<Listing[]>([]);
  const [form, setForm] = useState(EMPTY);
  const [err, setErr] = useState<Errors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const ref = useRef<HTMLFormElement>(null);
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [touched, setTouched] = useState<{ title?: boolean; price?: boolean; floor?: boolean }>({});
  const user = config?.user;
  const payTo = user?.phone || user?.email;
  // no phone or email on the PayPal login yet: ask for it here, so buyers can be paid
  const needEmail = !!user && !payTo && !user.email;
  const needPhone = !!user && !payTo;
  const needName = needPhone && (!user?.name || user.name === "PayPal user");

  const load = () => api.get<{ mine: Listing[] }>("/market").then((d) => setMine(d.mine)).catch((e) => toast(e.message, "error"));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (k: keyof typeof EMPTY, v: string) => {
    setForm((f) => ({ ...f, [k]: v }));
    if (k === "title" || k === "price" || k === "floor") setTouched((t) => ({ ...t, [k]: true }));
    if (err[k]) setErr({ ...err, [k]: "" });
  };

  // one line describes the item: read it as it's typed, fill the name, features and price, and ask the AI for a price
  useEffect(() => {
    if (form.prompt.trim().length < 3) { setParsed(null); return; }
    const t = setTimeout(() => {
      parsePrompt(form.prompt, "sell").then((p) => {
        setParsed(p);
        setForm((f) => ({
          ...f,
          title: touched.title ? f.title : p.request.title ?? "",
          description: p.request.details ?? "",
          price: touched.price ? f.price : String(p.request.price ?? p.suggestion?.price ?? ""),
          floor: touched.floor ? f.floor : p.request.floor ? String(p.request.floor) : "",
        }));
      }).catch(() => undefined);
    }, 450);
    return () => clearTimeout(t);
  }, [form.prompt]); // eslint-disable-line react-hooks/exhaustive-deps

  function validate(): Errors {
    const price = Number(form.price);
    const floorMsg = !form.floor.trim() ? null
      : amountError(form.floor, "a lowest price") ?? (Number(form.floor) > price ? "Your lowest price can't be higher than the price." : null);
    return collect({
      account: !user && "Log in with PayPal first, so buyers can pay your account.",
      name: needName && !form.name.trim() && "Enter your name, so buyers know who they're paying.",
      email: needEmail && (!form.email.trim() ? "Enter the email of your PayPal account; that's where buyers' money goes."
        : !EMAIL_RE.test(form.email.trim()) && "That PayPal email doesn't look right."),
      phone: needPhone && ((form.phone.match(/\d/g)?.length ?? 0) < 7) && "Enter your phone number with its country code, e.g. +1 408 555 0134.",
      prompt: !form.prompt.trim() && "Describe what you're selling, e.g. “Canon camera, 24MP, 2 lenses, like new with box, $300”.",
      title: !form.title.trim() ? "Enter the item's name." : form.title.trim().length < 2 && "The item name is too short.",
      price: amountError(form.price, "a price"),
      floor: floorMsg,
    });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setFailure(null);
    const errors = validate();
    setErr(errors);
    if (!report(errors, toast, ref.current)) return;
    setBusy(true);
    try {
      if (needPhone) {
        await api.post("/auth/phone", { phone: form.phone, email: needEmail ? form.email.trim() : undefined, name: needName ? form.name.trim() : undefined });
        reloadConfig();
      }
      await api.post("/market/listings", {
        title: form.title.trim(), price: Number(form.price), floor: form.floor.trim() ? Number(form.floor) : null,
        description: (form.description || form.prompt).trim() || null,
      });
      setForm(EMPTY); setParsed(null); setTouched({});
      toast("Listed. Buyers' agents can find it now.");
      await load(); bump();
    } catch (ex) {
      const msg = (ex as Error).message;
      setFailure(`Couldn't list it: ${msg}`);
      toast(`Couldn't list it: ${msg}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function remove(x: Listing) {
    setBusy(true);
    try { await api.del(`/market/listings/${x.id}`); toast(`Removed “${x.title}”.`); await load(); bump(); }
    catch (ex) { toast(`Couldn't remove it: ${(ex as Error).message}`, "error"); }
    finally { setBusy(false); }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:items-start">
      <form ref={ref} onSubmit={submit} noValidate className="bg-white rounded-3xl border border-slate-200 p-4 space-y-3 lg:sticky lg:top-24">
        <div className="text-sm text-black flex items-center gap-2"><Store className="w-4 h-4 text-emerald-700" /> Sell with an agent</div>
        <div className="text-xs text-slate-500">
          Your seller agent bargains with buyers' agents and never accepts less than your lowest price, which buyers can't see.
          {payTo && <> Buyers pay your PayPal account at <span className="text-black">{payTo}</span>; nobody can change that.</>}
        </div>
        <FieldError msg={err.account} />

        {needPhone && (
          <div className="p-3 rounded-2xl bg-amber-50 border border-amber-200 space-y-2">
            <div className="text-xs text-amber-900 flex items-center gap-1.5"><Wallet className="w-3.5 h-3.5" /> Where should buyers pay you? Your PayPal login didn't share a phone or email, so add them once.</div>
            {needName && <div><input data-field="name" className={field + invalid(err.name)} placeholder="Your name" value={form.name} onChange={(e) => set("name", e.target.value)} aria-label="Your name" /><FieldError msg={err.name} /></div>}
            {needEmail && <div><input data-field="email" type="email" className={field + invalid(err.email)} placeholder="Your PayPal account email" value={form.email} onChange={(e) => set("email", e.target.value)} aria-label="Your PayPal email" /><FieldError msg={err.email} /></div>}
            <div><input data-field="phone" type="tel" className={field + invalid(err.phone)} placeholder="Your phone, e.g. +1 408 555 0134" value={form.phone} onChange={(e) => set("phone", e.target.value)} aria-label="Your phone number" /><FieldError msg={err.phone} /></div>
          </div>
        )}

        <div>
          <textarea data-field="prompt" className={`${field} min-h-[72px]${invalid(err.prompt)}`} maxLength={400} aria-label="Describe what you're selling"
            placeholder="Describe it in one line: name, features, condition and price. e.g. Canon EOS camera, 24MP, 2 lenses, like new with box and charger, $300, lowest $260"
            value={form.prompt} onChange={(e) => set("prompt", e.target.value)} />
          <FieldError msg={err.prompt} />
        </div>

        {parsed && (
          <div className="p-3 rounded-2xl bg-slate-50 border border-slate-200 space-y-2.5">
            <div className="text-xs text-slate-500">Your agent read</div>
            <div>
              <input data-field="title" className={field + invalid(err.title)} placeholder="Item name" value={form.title} onChange={(e) => set("title", e.target.value)} aria-label="Item" maxLength={80} />
              <FieldError msg={err.title} />
            </div>
            {(parsed.request.wanted.length > 0 || parsed.flaws.length > 0) ? (
              <div className="space-y-1">
                {parsed.request.wanted.length > 0 && <Chips items={parsed.request.wanted} tone="green" />}
                {parsed.flaws.length > 0 && <Chips items={parsed.flaws.map((x) => `mentions ${x}`)} tone="red" />}
              </div>
            ) : <div className="text-xs text-amber-800">Add the condition and features (e.g. “like new, 2 lenses, with box”): buyers' agents compare them and your agent uses them to hold the price.</div>}
            {parsed.suggestion && (
              <div className="text-xs text-[#174ea6] flex items-start gap-1.5">
                <Sparkles className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                <span className="flex-1">AI suggests <b className="font-normal text-black">{money(parsed.suggestion.price)}</b> ({money(parsed.suggestion.low)}–{money(parsed.suggestion.high)}). {parsed.suggestion.basis}.</span>
                {String(parsed.suggestion.price) !== form.price && <button type="button" className="gbtn text-xs text-[#1a73e8] shrink-0" onClick={() => set("price", String(parsed.suggestion!.price))}>Use it</button>}
              </div>
            )}
          </div>
        )}

        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <div className="text-[11px] text-slate-500 mb-1">Price (you can raise the AI's suggestion)</div>
            <PriceInput dataField="price" label="Price" value={form.price} error={err.price} onChange={(v) => set("price", v)} />
            <FieldError msg={err.price} />
          </div>
          <div>
            <div className="text-[11px] text-slate-500 mb-1">Lowest price (private)</div>
            <input data-field="floor" className={field + invalid(err.floor)} inputMode="decimal" placeholder={Number(form.price) > 0 ? `Default ${money(Number(form.price) * 0.9)}` : "Optional"} value={form.floor} onChange={(e) => set("floor", decimal(e.target.value))} aria-label="Lowest price" />
            <FieldError msg={err.floor} />
          </div>
        </div>
        <div className="text-[11px] text-slate-500">Your agent is stubborn: it holds the price, gives ground slowly and never goes under your lowest price (90% if you leave it empty).</div>
        <FormAlert msg={failure} onClose={() => setFailure(null)} />
        <button className={primary} disabled={busy}>{busy ? "Listing…" : "List it"}</button>
      </form>

      <section className="bg-white rounded-3xl border border-slate-200 overflow-hidden">
        <div className="px-4 pt-4 pb-1 text-sm text-black">Your listings</div>
        {!mine.length ? <div className="p-4 text-sm text-slate-500">You haven't listed anything yet.</div> : mine.map((x) => (
          <div key={x.id} className="px-4 py-3 border-t border-slate-100 first-of-type:border-0 flex justify-between items-center gap-2 text-sm">
            <div className="min-w-0">
              <div className="text-black truncate">{x.title}</div>
              <div className="text-xs text-slate-500">{x.status === "sold" ? `Sold for ${money(x.sold_price ?? x.price)}` : `${money(x.price)} · lowest ${money(x.floor ?? x.price)}`}</div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <span className={`text-[11px] px-2 py-0.5 rounded-full border ${STATUS[x.status] ?? STATUS.active}`}>{x.status}</span>
              {x.status === "active" && <button className="gbtn p-2 text-slate-500" disabled={busy} onClick={() => remove(x)} aria-label={`Remove ${x.title}`}><Trash2 className="w-4 h-4" /></button>}
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}
