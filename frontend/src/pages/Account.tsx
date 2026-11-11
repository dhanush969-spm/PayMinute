import { useEffect, useState } from "react";
import { BadgeCheck, ExternalLink, FileText, Mail, MapPin, RefreshCw, User, Wallet } from "lucide-react";
import { api } from "../api";
import { money } from "../format";
import { useApp } from "../components/ui";
import { FormAlert } from "../components/form";

interface Balance { currency: string; primary: boolean; total: number | null; available: number | null; withheld: number | null }
interface AccountData {
  profile: { name?: string; email?: string; verified?: boolean; payer_id?: string; account?: string; address?: string };
  missing_scopes: { scope: string; gives: string }[];
  recommended_scopes: string;
  business: { account_id?: string; as_of?: string; balances: Balance[] } | null;
  business_error: string | null;
}
export interface PaymentRequest {
  id: string; number?: string; status: string; open: boolean; to_name?: string; to_email?: string; to_me: boolean;
  amount: number | null; due: number | null; currency?: string; note?: string; created?: string; link?: string;
}
const TONE: Record<string, string> = {
  PAID: "bg-emerald-50 text-emerald-800 border-emerald-200", MARKED_AS_PAID: "bg-emerald-50 text-emerald-800 border-emerald-200",
  SENT: "bg-amber-50 text-amber-900 border-amber-200", UNPAID: "bg-amber-50 text-amber-900 border-amber-200",
  PARTIALLY_PAID: "bg-amber-50 text-amber-900 border-amber-200", PAYMENT_PENDING: "bg-amber-50 text-amber-900 border-amber-200",
  CANCELLED: "bg-slate-50 text-slate-600 border-slate-200", DRAFT: "bg-slate-50 text-slate-600 border-slate-200",
};
const card = "bg-white rounded-3xl border border-slate-200 p-4 lg:p-5";

function Row({ icon: Icon, label, value, note }: { icon: typeof User; label: string; value?: string | null; note?: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 py-2.5 border-t border-slate-100 first:border-0">
      <Icon className="w-4 h-4 text-slate-500 mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="text-[11px] text-slate-500">{label}</div>
        <div className="text-sm text-black break-words">{value || <span className="text-slate-400">Not shared by PayPal</span>}</div>
        {note}
      </div>
    </div>
  );
}

export default function Account() {
  const { toast, version } = useApp();
  const [acct, setAcct] = useState<AccountData | null>(null);
  const [reqs, setReqs] = useState<{ items: PaymentRequest[]; open_count: number; open_total: number } | null>(null);
  const [reqError, setReqError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setReqError(null);
    await Promise.all([
      api.get<AccountData>("/account").then(setAcct).catch((e) => toast(`Couldn't load your PayPal account: ${e.message}`, "error")),
      api.get<typeof reqs>("/requests").then(setReqs).catch((e) => { setReqError(e.message); toast(e.message, "error"); }),
    ]);
    setLoading(false);
  }
  useEffect(() => { load(); }, [version]); // eslint-disable-line react-hooks/exhaustive-deps

  const p = acct?.profile;
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button className="gbtn px-4 py-2 rounded-full bg-white border border-slate-200 text-sm text-black flex items-center gap-2" disabled={loading} onClick={load}>
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Refresh from PayPal
        </button>
      </div>

      <div className="grid gap-4 lg:grid-cols-2 lg:items-start">
        <section className={card}>
          <div className="text-sm text-black mb-1 flex items-center gap-2"><User className="w-4 h-4 text-[#1a73e8]" /> Your PayPal profile</div>
          <div className="text-xs text-slate-500 mb-2">What Log in with PayPal shared with PayMinute.</div>
          {!acct ? <div className="skeleton" style={{ height: 160 }} /> : (
            <>
              <Row icon={User} label="Name" value={p?.name && p.name !== "PayPal user" ? p.name : null}
                note={p?.verified ? <span className="text-xs text-emerald-700 flex items-center gap-1 mt-0.5"><BadgeCheck className="w-3.5 h-3.5" /> Verified PayPal account</span> : null} />
              <Row icon={Mail} label="PayPal email" value={p?.email} />
              <Row icon={MapPin} label="Location" value={p?.address} />
              <Row icon={Wallet} label="PayPal account ID" value={p?.payer_id || p?.account} />
              {acct.missing_scopes.length > 0 && (
                <div className="mt-3 p-3 rounded-2xl bg-blue-50 border border-blue-200 text-xs text-[#174ea6] space-y-1">
                  <div className="text-sm">Show more of your profile</div>
                  <div>Your PayPal app only shares some details. To also get {acct.missing_scopes.map((m) => m.gives).join(", ")}:</div>
                  <ol className="list-decimal pl-4 space-y-0.5">
                    <li>Developer Dashboard → Apps &amp; Credentials (Sandbox) → your app → Log in with PayPal → Advanced options.</li>
                    <li>Tick Full name, Email, Address and Account verification status / PayPal account ID, then Save.</li>
                    <li>In <code>.env</code> set <code className="break-all">PAYPAL_LOGIN_SCOPES="{acct.recommended_scopes}"</code>, restart the backend and log in again.</li>
                  </ol>
                </div>
              )}
            </>
          )}
        </section>

        <section className={card}>
          <div className="text-sm text-black mb-1 flex items-center gap-2"><Wallet className="w-4 h-4 text-emerald-700" /> PayPal balance</div>
          <div className="text-xs text-slate-500 mb-3">
            The PayPal business account behind this app{acct?.business?.account_id ? ` (${acct.business.account_id})` : ""}: it receives Get paid payments and sends payouts.
            PayPal doesn't let apps read a personal account's balance.
          </div>
          {!acct ? <div className="skeleton" style={{ height: 120 }} /> : acct.business_error ? <FormAlert msg={`PayPal didn't return the balance: ${acct.business_error}`} /> : (
            <div className="space-y-3">
              {(acct.business?.balances ?? []).length === 0 && <div className="text-sm text-slate-500">PayPal reports no balances for this account.</div>}
              {acct.business?.balances.map((b) => (
                <div key={b.currency} className="rounded-2xl bg-slate-50 border border-slate-200 p-4">
                  <div className="flex items-baseline justify-between">
                    <span className="text-xs text-slate-500">{b.currency}{b.primary ? " · primary" : ""}</span>
                    <span className="text-2xl text-black">{b.total != null ? money(b.total, { cur: b.currency }) : "—"}</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 mt-2 text-xs">
                    <div><div className="text-slate-500">Available</div><div className="text-emerald-700 text-sm">{b.available != null ? money(b.available, { cur: b.currency }) : "—"}</div></div>
                    <div><div className="text-slate-500">On hold (pending or in review)</div><div className="text-amber-700 text-sm">{b.withheld != null ? money(b.withheld, { cur: b.currency }) : "—"}</div></div>
                  </div>
                </div>
              ))}
              {acct.business?.as_of && <div className="text-[11px] text-slate-500">PayPal's figures as of {new Date(acct.business.as_of).toLocaleString()}.</div>}
            </div>
          )}
        </section>
      </div>

      <section className={card}>
        <div className="flex items-center justify-between gap-2 mb-1">
          <div className="text-sm text-black flex items-center gap-2"><FileText className="w-4 h-4 text-amber-700" /> Payment requests</div>
          {reqs && reqs.open_count > 0 && <span className="text-xs text-amber-800">{reqs.open_count} waiting · {money(reqs.open_total)}</span>}
        </div>
        <div className="text-xs text-slate-500 mb-2">PayPal invoices sent from this account (bill splits, invoices from AI Guard), with PayPal's live status.</div>
        <FormAlert msg={reqError} />
        {!reqs && !reqError ? <div className="skeleton" style={{ height: 80 }} /> : reqs && reqs.items.length === 0 ? (
          <div className="text-sm text-slate-500 py-2">No payment requests yet. Ask AI Guard: “Split $90 dinner with Priya” or “Invoice maya@example.com $250 for logo design”.</div>
        ) : reqs && (
          <div className="divide-y divide-slate-100">
            {reqs.items.map((r) => (
              <div key={r.id} className="py-3 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4">
                <div className="min-w-0 flex-1">
                  <div className="text-sm text-black truncate">{r.to_name || r.to_email || "No recipient"}{r.to_me && <span className="text-xs text-[#1a73e8]"> · to you</span>}</div>
                  <div className="text-xs text-slate-500 truncate">{[r.number && `#${r.number}`, r.note, r.created && new Date(r.created).toLocaleDateString()].filter(Boolean).join(" · ")}</div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <span className="text-sm text-black">{r.amount != null ? money(r.amount, { cur: r.currency }) : "—"}</span>
                  <span className={`text-[11px] px-2 py-0.5 rounded-full border ${TONE[r.status] ?? TONE.DRAFT}`}>{r.status.replace(/_/g, " ").toLowerCase()}</span>
                  {r.link && /^https:\/\/www\.(sandbox\.)?paypal\.com\//.test(r.link) && <a className="text-xs text-[#1a73e8] flex items-center gap-1" href={r.link} target="_blank" rel="noreferrer noopener">Open <ExternalLink className="w-3 h-3" /></a>}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
