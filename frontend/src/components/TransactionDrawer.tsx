import { useEffect, useState } from "react";
import { RefreshCw, RotateCcw, Undo2 } from "lucide-react";
import { api, ApiError, partyKey, partyLabel, Tx } from "../api";
import { dateTime, kindLabel, money } from "../format";
import Avatar from "../screens/Avatar";
import { Drawer, InsightCard, ReasonList, Skeleton, StatusBadge, Timeline, useApp } from "./ui";

export default function TransactionDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { config, toast, bump } = useApp();
  const [tx, setTx] = useState<Tx | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    setTx(null);
    if (!id) return;
    api.get<Tx>(`/transactions/${id}`).then(setTx).catch((e) => toast(e.message, "error"));
  }, [id, toast]);

  async function act(label: string, fn: () => Promise<Tx>, done: string) {
    setBusy(label);
    try {
      await fn();
      setTx(await api.get<Tx>(`/transactions/${id}`));
      toast(done);
      bump();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  }

  async function changeCategory(category: string) {
    if (!tx) return;
    try {
      await api.patch(`/transactions/${tx.id}/category`, { category });
      setTx({ ...tx, category, category_source: "user" });
      toast(`PayMinute will remember: ${tx.counterparty} → ${category}`);
      bump();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  // ask PayPal again: Payments API plus PayPal's transaction ledger
  async function check() {
    if (!tx) return;
    setBusy("check");
    try {
      const before = tx.status;
      const r = await api.post<Tx>(`/transactions/${tx.id}/check`);
      setTx(await api.get<Tx>(`/transactions/${tx.id}`));
      const d = r.paypal_detail;
      if (r.status !== before) toast(`PayPal now reports this payment as ${r.status.toLowerCase()}.`);
      else if (d?.check_error) toast(`Couldn't check with PayPal: ${d.check_error}`, "error");
      else toast(`PayPal still reports ${r.status.toLowerCase()}${r.status_reason ? ` (${r.status_reason})` : ""}.`, r.status === "PENDING" ? "error" : "info");
      bump();
    } catch (e) {
      toast(`Couldn't check with PayPal: ${e instanceof ApiError ? e.message : String(e)}`, "error");
    } finally {
      setBusy(null);
    }
  }

  const LEDGER: Record<string, string> = { S: "Settled", P: "Pending", D: "Denied", V: "Reversed" };
  const own = tx?.mine !== false; // only the account that made a payment can act on it
  const cats = tx ? (tx.direction === "in" ? config?.categories.income : config?.categories.expense) ?? [] : [];
  const sign = tx?.direction === "in" ? 1 : -1;

  return (
    <Drawer
      open={!!id}
      onClose={onClose}
      title={tx ? (
        <div className="row">
          <Avatar name={partyLabel(tx)} seed={partyKey(tx)} size={44} />
          <div style={{ minWidth: 0 }}>
            <div className="strong" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{partyLabel(tx)}</div>
            <div className="small muted">{kindLabel(tx.kind, tx.direction)}</div>
          </div>
        </div>
      ) : <Skeleton w={200} />}
    >
      {!tx ? (
        <div className="stack"><Skeleton h={60} /><Skeleton h={120} /><Skeleton h={160} /></div>
      ) : (
        <>
          <div>
            <div className={`num`} style={{ fontSize: 34, fontWeight: 750, letterSpacing: "-0.02em", color: tx.direction === "in" ? "var(--success)" : "var(--ink)" }}>
              {money(sign * tx.amount, { sign: true, cur: tx.currency })}
            </div>
            <div className="row wrap" style={{ marginTop: 6 }}>
              <StatusBadge status={tx.status} />
              <span className="small muted">{dateTime(tx.created_at)}</span>
            </div>
          </div>

          {tx.insight && tx.insight.state !== "done" && <InsightCard insight={tx.insight} />}
          {!own && (
            <div className="small muted">{tx.direction === "in" ? `${tx.counterparty} sent you this through PayMinute.` : `You paid ${tx.counterparty} through their PayMinute checkout.`} Only they can change it.</div>
          )}
          {own && ["PENDING", "UNCLAIMED", "ONHOLD"].includes(tx.status) && (tx.kind === "payout" || tx.kind === "checkout") && (
            <button className="btn primary" disabled={!!busy} onClick={check}>
              <RefreshCw size={16} className={busy === "check" ? "spin" : ""} /> {busy === "check" ? "Checking with PayPal…" : "Check with PayPal"}
            </button>
          )}

          {own && tx.status === "HELD_FOR_REVIEW" && (
            <div className="row">
              <button className="btn primary" disabled={!!busy} onClick={() => act("cap", () => api.post(`/transactions/${tx.id}/review`, { approve: true }), "Payment captured")}>
                Capture payment
              </button>
              <button className="btn danger-outline" disabled={!!busy} onClick={() => act("void", () => api.post(`/transactions/${tx.id}/review`, { approve: false }), "Authorization voided. The buyer was not charged.")}>
                Void
              </button>
            </div>
          )}
          {own && tx.insight?.can_cancel && (
            <button className="btn secondary" disabled={!!busy} onClick={() => act("cancel", () => api.post(`/transactions/${tx.id}/cancel`), "Payout cancelled. Money returned to your balance.")}>
              <Undo2 size={16} /> Cancel and get money back
            </button>
          )}

          {tx.risk_reasons?.length > 0 && (
            <section>
              <div className="section-title">{tx.risk_level === "low" ? "PayMinute's risk check" : "Why PayMinute flagged this"} · {tx.risk_level} risk ({Math.round((tx.risk_score ?? 0) * 100)}%)</div>
              <div className="risk" style={{ borderRadius: 12 }}><ReasonList reasons={tx.risk_reasons} /></div>
            </section>
          )}

          {own && <section>
            <div className="section-title">Category</div>
            <select className="select" value={tx.category ?? ""} onChange={(e) => changeCategory(e.target.value)} aria-label="Category">
              {[...new Set([tx.category ?? "", ...cats])].filter(Boolean).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <div className="tiny muted" style={{ marginTop: 6 }}>
              {tx.category_source === "user" ? "Set by you." : tx.category_source === "ai" ? "Categorised by AI." : tx.category_source === "memory" ? "Learned from your earlier correction." : "Categorised automatically."} Changing it teaches PayMinute for this contact.
            </div>
          </section>}

          <section>
            <div className="section-title">Details</div>
            <dl className="kv">
              {tx.counterparty_phone && <><dt>Phone</dt><dd>{tx.counterparty_phone}</dd></>}
              {tx.counterparty_email && <><dt>Email</dt><dd>{tx.counterparty_email}</dd></>}
              {tx.counterparty_country && <><dt>Country</dt><dd>{tx.counterparty_country}</dd></>}
              {tx.memo && <><dt>Note</dt><dd>{tx.memo}</dd></>}
              {tx.paypal_order_id && <><dt>PayPal order</dt><dd className="num">{tx.paypal_order_id}</dd></>}
              {tx.paypal_authorization_id && <><dt>Authorization</dt><dd className="num">{tx.paypal_authorization_id}</dd></>}
              {tx.paypal_capture_id && <><dt>Capture</dt><dd className="num">{tx.paypal_capture_id}</dd></>}
              {tx.paypal_payout_batch_id && <><dt>Payout batch</dt><dd className="num">{tx.paypal_payout_batch_id}</dd></>}
              {tx.paypal_payout_item_id && <><dt>Payout item</dt><dd className="num">{tx.paypal_payout_item_id}</dd></>}
              {tx.paypal_detail?.capture_status && <><dt>PayPal payment status</dt><dd>{tx.paypal_detail.capture_status}{tx.paypal_detail.capture_reason ? ` (${tx.paypal_detail.capture_reason})` : ""}</dd></>}
              {tx.paypal_detail?.capture_status && <><dt>PayPal ledger</dt><dd>{tx.paypal_detail.report_status ? LEDGER[tx.paypal_detail.report_status] ?? tx.paypal_detail.report_status : tx.paypal_detail.report_error ? `Couldn't read: ${tx.paypal_detail.report_error}` : "Not listed yet (sandbox can lag up to 3 h)"}</dd></>}
              {tx.paypal_detail?.net != null && <><dt>You receive</dt><dd>{money(tx.paypal_detail.net, { cur: tx.currency })}{tx.paypal_detail.fee != null ? ` after ${money(tx.paypal_detail.fee, { cur: tx.currency })} PayPal fee` : ""}</dd></>}
              {tx.paypal_detail?.transaction_status && !tx.paypal_detail?.capture_status && <><dt>PayPal item status</dt><dd>{tx.paypal_detail.transaction_status}</dd></>}
              {tx.paypal_detail?.batch_status && <><dt>PayPal batch status</dt><dd>{tx.paypal_detail.batch_status}</dd></>}
              {(tx.paypal_detail?.error_name || tx.paypal_detail?.error_message) && <><dt>PayPal error</dt><dd>{tx.paypal_detail.error_name}{tx.paypal_detail.error_message ? `: ${tx.paypal_detail.error_message}` : ""}</dd></>}
              {tx.paypal_detail?.check_error && <><dt>Last check failed</dt><dd>{tx.paypal_detail.check_error}{tx.paypal_detail.debug_id ? ` (debug_id ${tx.paypal_detail.debug_id})` : ""}</dd></>}
              {tx.paypal_detail?.checked_at && <><dt>Last checked</dt><dd>{new Date(tx.paypal_detail.checked_at).toLocaleString()}</dd></>}
              {tx.paypal_invoice_id && <><dt>Invoice</dt><dd className="num">{tx.paypal_invoice_id}</dd></>}
              {tx.paypal_transaction_id && !tx.paypal_capture_id && <><dt>Transaction</dt><dd className="num">{tx.paypal_transaction_id}</dd></>}
              <dt>PayMinute ID</dt><dd className="num">{tx.id}</dd>
            </dl>
          </section>

          {tx.history && tx.history.length > 0 && (
            <section>
              <div className="section-title">Status history</div>
              <Timeline history={tx.history} />
            </section>
          )}

          {own && (
            <div className="row wrap">
              {(tx.kind === "payout" || tx.kind === "checkout") && (
                <button className="btn secondary sm" disabled={!!busy} onClick={() => check}>
                  <RefreshCw size={14} /> Refresh from PayPal
                </button>
              )}
              {tx.kind === "checkout" && tx.status === "COMPLETED" && (
                <button className="btn danger-outline sm" disabled={!!busy} onClick={() => confirm(`Refund ${money(tx.amount)} to ${tx.counterparty}?`) && act("refund", () => api.post(`/transactions/${tx.id}/refund`), "Refund issued")}>
                  <RotateCcw size={14} /> Refund
                </button>
              )}
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}
