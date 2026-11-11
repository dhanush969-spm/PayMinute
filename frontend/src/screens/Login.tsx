import { useState } from "react";
import { BadgeCheck, ShieldCheck, Phone, Sparkles, TriangleAlert } from "lucide-react";
import { api, type AppConfig, type PayPalUser } from "../api";

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[#f1f3f4] text-black font-jakarta phone flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-white rounded-3xl border border-slate-200 p-7 space-y-6">
        <div className="flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="w-11 h-11 rounded-2xl" onError={(e) => (e.currentTarget.style.display = "none")} />
          <div>
            <div className="text-xl text-black">PayMinute</div>
            <div className="text-xs text-slate-500">AI payment guardian on PayPal</div>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

// the server runs the PayPal OAuth flow
export function LoginScreen({ notice }: { notice?: string | null }) {
  const err = new URLSearchParams(window.location.search).get("login_error");
  return (
    <Shell>
      <div className="space-y-3">
        {[
          { icon: Phone, text: "Pay anyone by phone number through PayPal Payouts" },
          { icon: ShieldCheck, text: "AI Guard verifies the number and checks every payment for scams before money moves" },
          { icon: Sparkles, text: "Ask the assistant about stuck payments, spending and invoices" },
        ].map((f) => (
          <div key={f.text} className="flex items-start gap-3 text-sm text-slate-700">
            <f.icon className="w-5 h-5 text-[#1a73e8] shrink-0" /> {f.text}
          </div>
        ))}
      </div>
      {notice && !err && <div className="p-3 rounded-2xl bg-amber-50 border border-amber-200 text-sm text-amber-900" role="status">{notice}</div>}
      {err && <div className="p-3 rounded-2xl bg-red-50 border border-red-200 text-sm text-red-800" role="alert">{err}</div>}
      <a href="/api/auth/login"
        className="flex items-center justify-center gap-2 w-full py-3.5 rounded-full bg-[#ffc439] hover:bg-[#f2ba36] text-[#111] text-sm no-underline">
        <svg width="18" height="20" viewBox="0 0 24 28" aria-hidden="true"><path fill="#003087" d="M20.9 6.6c.3-2-.0-3.4-1.1-4.7C18.6.6 16.4 0 13.6 0H5.5c-.6 0-1 .4-1.1 1L1 22.4c-.1.4.3.8.7.8h5l-.3 2.2c-.1.4.2.7.6.7h4.2c.5 0 .9-.4 1-.9l.9-5.7c.1-.5.5-.9 1-.9h.6c4.1 0 7.3-1.7 8.2-6.5.4-2 .2-3.7-.8-4.9-.3-.3-.7-.6-1.2-.6z"/><path fill="#0070e0" d="M20.9 6.6c-.9 4.9-4.1 6.6-8.2 6.6h-2.1c-.5 0-.9.4-1 .9l-1.4 8.8-.4 2.5c-.1.4.2.7.6.7h3.7c.5 0 .8-.3.9-.8l.9-5.6c.1-.5.5-.8.9-.8h.6c3.6 0 6.4-1.5 7.2-5.7.4-1.8.2-3.2-.8-4.3-.3-.2-.6-.4-.9-.3z"/></svg>
        Log in with PayPal
      </a>
      <div className="text-[11px] text-slate-500 text-center">
        PayPal sandbox · use a sandbox personal account from developer.paypal.com → Testing tools → Sandbox accounts.
        PayMinute only receives your name, email and PayPal ID; your password stays with PayPal.
      </div>
    </Shell>
  );
}

// shown when the server has no PayPal keys
export function SetupScreen({ config }: { config: AppConfig }) {
  return (
    <Shell>
      <div className="p-4 rounded-2xl bg-amber-50 border border-amber-200 text-sm text-amber-900 flex gap-2">
        <TriangleAlert className="w-5 h-5 shrink-0" /> PayMinute isn't connected to PayPal yet.
      </div>
      <ol className="m-0 pl-5 text-sm text-slate-700 space-y-2">
        <li>Open developer.paypal.com → Apps &amp; Credentials (Sandbox) → your <strong>PayMinute</strong> app.</li>
        <li>Copy the Client ID and Secret into <code>PAYPAL_CLIENT_ID</code> and <code>PAYPAL_CLIENT_SECRET</code> in <code>.env</code> (or Render's environment).</li>
        <li>Under <strong>Log in with PayPal</strong>, turn it on and set the Return URL to <code className="break-all">{config.login_redirect_uri}</code>.</li>
        <li>Restart the server and reload this page.</li>
      </ol>
    </Shell>
  );
}

// your phone number, so others can pay you by phone
export function MyPhone({ user, onSaved, toast }: { user: PayPalUser; onSaved: () => void; toast: (m: string, k?: "error") => void }) {
  const [value, setValue] = useState("");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  if (user.phone) {
    return (
      <div className="text-xs text-black flex items-center gap-1.5">
        <Phone className="w-3.5 h-3.5 text-slate-500" /> {user.phone}
        {user.phone_source === "paypal"
          ? <span className="text-emerald-700 flex items-center gap-0.5"><BadgeCheck className="w-3.5 h-3.5" /> from PayPal</span>
          : <span className="text-slate-500">(added by you)</span>}
      </div>
    );
  }
  const needEmail = !user.email;
  const needName = !user.name || user.name === "PayPal user";
  async function save() {
    const digits = value.match(/\d/g)?.length ?? 0;
    const problem = needName && !name.trim() ? "Enter your name."
      : needEmail && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim()) ? "Enter the email of your PayPal account."
      : digits < 7 ? "Enter your phone number with its country code, e.g. +1 408 555 0134." : null;
    if (problem) { toast(problem, "error"); return; }
    setBusy(true);
    try {
      await api.post("/auth/phone", { phone: value, email: needEmail ? email : undefined, name: needName ? name : undefined });
      toast("Your number is linked to your PayPal account");
      onSaved();
    } catch (e) { toast(`Couldn't save your number: ${(e as Error).message}`, "error"); }
    finally { setBusy(false); }
  }
  const field = "w-full min-w-0 bg-slate-50 border border-slate-200 rounded-xl px-2.5 py-1.5 text-xs text-black outline-none";
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] text-slate-500">Add your phone so people can pay you by phone:</div>
      {needName && <input className={field} placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)} aria-label="Your name" />}
      {needEmail && <input className={field} type="email" placeholder="Your PayPal email" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Your PayPal email" />}
      <div className="flex gap-1.5">
        <input className={`${field} flex-1`} type="tel" placeholder="+1 408 555 0134" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Your phone number" />
        <button className="gbtn px-3 rounded-xl bg-[#1a73e8] text-white text-xs disabled:opacity-50"
          disabled={busy} onClick={save}>Save</button>
      </div>
    </div>
  );
}
