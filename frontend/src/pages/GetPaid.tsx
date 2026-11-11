import { FormEvent, useEffect, useRef, useState } from "react";
import { CheckCircle2, Clock, Loader2, ShieldAlert } from "lucide-react";
import { api, ApiError, newRef, Risk, Tx } from "../api";
import { currencySymbol, money } from "../format";
import PayPalButton from "../components/PayPalButton";
import { Card, EmptyState, InsightCard, RiskPanel, StatusBadge, TxList, useApp } from "../components/ui";
import { Errors, FieldError, amountError, collect, invalid, report } from "../components/form";

interface CompleteResult { transaction: Tx; risk: Risk | null; decision: "captured" | "held" | "already_processed" }

export default function GetPaid() {
  const { config, toast, bump, openTx, version } = useApp();
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [ready, setReady] = useState(false);
  const [orderId, setOrderId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CompleteResult | null>(null);
  const [recent, setRecent] = useState<Tx[]>([]);
  const ref = useRef(newRef());
  const [err, setErr] = useState<Errors>({});

  useEffect(() => {
    api.get<Tx[]>("/transactions?direction=in&limit=8").then(setRecent).catch(() => undefined);
  }, [version]);

  const amt = parseFloat(amount);
  const valid = amt > 0 && amt <= 10000 && description.trim().length > 0;

  function prepare(e: FormEvent) {
    e.preventDefault();
    const errors = collect({ amount: amountError(amount, "an amount", 10000), description: !description.trim() && "Say what the payment is for, e.g. Logo design." });
    setErr(errors);
    if (!report(errors, toast, e.currentTarget as HTMLElement) || !valid) return;
    ref.current = newRef();
    setOrderId(null);
    setResult(null);
    setReady(true);
  }

  async function createOrder(): Promise<{ orderId: string }> {
    const res = await api.post<{ order_id: string }>("/checkout/orders", { amount: amt, description: description.trim(), client_ref: ref.current });
    setOrderId(res.order_id);
    return { orderId: res.order_id };
  }

  async function complete(id: string) {
    setBusy(true);
    try {
      const res = await api.post<CompleteResult>(`/checkout/orders/${id}/complete`);
      setResult(res);
      setReady(false);
      bump();
    } catch (e) {
      toast(`Payment not completed: ${e instanceof ApiError ? e.message : String(e)}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function decide(approve: boolean) {
    if (!result) return;
    setBusy(true);
    try {
      const tx = await api.post<Tx>(`/transactions/${result.transaction.id}/review`, { approve });
      setResult({ ...result, transaction: tx });
      toast(approve ? "Captured. The money is yours." : "Voided. The buyer was not charged.");
      bump();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  const tx = result?.transaction;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Get paid</h1>
          <p>Create a PayPal checkout for a client. PayMinute authorizes first, checks the buyer, then captures, or holds it for your review.</p>
        </div>
      </div>

      <div className="grid half">
        <div className="stack">
          <section className="card card-pad">
            <form onSubmit={prepare} noValidate>
              <div className="field">
                <label htmlFor="amt">Amount ({config?.currency})</label>
                <div className="amount-input">
                  <span className="cur">{currencySymbol()}</span>
                  <input id="amt" data-field="amount" className={"input num" + invalid(err.amount)} inputMode="decimal" placeholder="0.00" value={amount} onChange={(e) => { setAmount(e.target.value.replace(/[^0-9.]/g, "")); setErr({ ...err, amount: "" }); }} />
                </div>
                <FieldError msg={err.amount} />
              </div>
              <div className="field">
                <label htmlFor="desc">What it's for</label>
                <input id="desc" data-field="description" className={"input" + invalid(err.description)} maxLength={127} placeholder="e.g. Logo design, first draft" value={description} onChange={(e) => { setDescription(e.target.value); setErr({ ...err, description: "" }); }} />
                <FieldError msg={err.description} />
              </div>
              <button className="btn primary block">Create checkout</button>
            </form>
          </section>

          <Card title="How PayMinute protects you">
            <ol className="small" style={{ margin: 0, paddingLeft: 18, color: "var(--ink-2)", display: "grid", gap: 6 }}>
              <li>Order is created server-side with <code>intent: AUTHORIZE</code> (Orders API v2).</li>
              <li>The buyer approves in PayPal. PayMinute authorizes, so funds are reserved but not yet taken.</li>
              <li>PayMinute scores the buyer: new country, throwaway email, rapid repeat payments, amount anomaly (ML), duplicates.</li>
              <li>Low risk: captured automatically. Risky: held for you to <strong>capture</strong> or <strong>void</strong>.</li>
              <li>Pending captures (eCheck, PayPal review) are tracked via webhooks and polling.</li>
            </ol>
          </Card>
        </div>

        <div className="stack">
          {ready && (
            <div className="checkout">
              <div className="small muted" style={{ textAlign: "center", marginBottom: 8 }}>What your client sees</div>
              <div className="checkout-card">
                <div className="checkout-top">
                  <div className="merchant">{config?.business_name}</div>
                  <div className="small muted">{description}</div>
                  <div className="amt num">{money(amt)}</div>
                </div>
                <div className="checkout-body">
                  {config?.paypal_client_id ? (
                    <>
                      <PayPalButton clientId={config.paypal_client_id} sdkUrl={config.paypal_sdk_url} currency={config.currency}
                        amount={amt.toFixed(2)} createOrder={createOrder} onApprove={complete}
                        onCancel={() => toast("The buyer closed PayPal before approving.")}
                        onError={(m) => toast(m, "error")} />
                      <div className="tiny muted" style={{ textAlign: "center" }}>Your client pays with their PayPal account (in testing: a <strong>sandbox personal</strong> account).</div>
                    </>
                  ) : (
                    <div className="small muted" style={{ textAlign: "center" }}>PayPal isn't connected on the server yet.</div>
                  )}
                  {busy && <div className="row small muted"><Loader2 size={16} /> Authorizing and running PayMinute's risk check…</div>}
                  {orderId && <div className="tiny muted" style={{ textAlign: "center" }}>PayPal order {orderId}</div>}
                </div>
              </div>
            </div>
          )}

          {tx && result && (
            <section className="card card-pad stack" style={{ gap: 14 }}>
              <div className="row" style={{ alignItems: "flex-start" }}>
                {tx.status === "HELD_FOR_REVIEW" ? <ShieldAlert size={30} color="var(--danger)" /> :
                  tx.status === "PENDING" ? <Clock size={30} color="var(--warn)" /> : <CheckCircle2 size={30} color="var(--success)" />}
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>
                    {tx.status === "HELD_FOR_REVIEW" ? "Held for your review" : tx.status === "PENDING" ? "Payment pending" :
                      tx.status === "VOIDED" ? "Voided" : `${money(tx.amount)} received`}
                  </div>
                  <div className="row wrap small" style={{ marginTop: 4 }}>
                    <span className="muted">from {tx.counterparty}{tx.counterparty_country ? ` · ${tx.counterparty_country}` : ""}</span>
                    <StatusBadge status={tx.status} />
                  </div>
                </div>
              </div>
              {result.risk && <RiskPanel risk={result.risk} heldThreshold={tx.status === "HELD_FOR_REVIEW"} />}
              {tx.insight && tx.status !== "HELD_FOR_REVIEW" && <InsightCard insight={tx.insight} />}
              {tx.status === "HELD_FOR_REVIEW" && (
                <div className="row">
                  <button className="btn primary" disabled={busy} onClick={() => decide(true)}>Capture {money(tx.amount)}</button>
                  <button className="btn danger-outline" disabled={busy} onClick={() => decide(false)}>Void</button>
                </div>
              )}
              <button className="btn ghost sm" style={{ alignSelf: "flex-start" }} onClick={() => openTx(tx.id)}>Open payment details</button>
            </section>
          )}

          {!ready && !tx && (
            <Card title="Recent customer payments">
              <div style={{ margin: "0 -22px -22px" }}>
                <TxList items={recent} grouped={false} empty={<EmptyState title="No customer payments yet" body="Create a checkout to get paid." />} />
              </div>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
