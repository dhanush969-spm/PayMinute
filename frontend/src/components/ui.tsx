import { ReactNode, createContext, useCallback, useContext, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Clock, Info, ShieldAlert, ShieldCheck, ShieldQuestion, X } from "lucide-react";
import { partyKey, partyLabel } from "../api";
import type { AppConfig, Insight, Risk, RiskReason, Tx } from "../api";
import { dateTime, dayLabel, getLocale, money, statusLabel, statusTone } from "../format";
import Avatar from "../screens/Avatar";

interface Ctx {
  config: AppConfig | null;
  toast: (msg: string, kind?: "info" | "error") => void;
  openTx: (id: string) => void;
  bump: () => void;
  version: number;
  reloadConfig: () => void;
}
export const AppCtx = createContext<Ctx>({} as Ctx);
export const useApp = () => useContext(AppCtx);

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <path d="M32 4 9 12.5v17.8C9 45.4 18.8 56.6 32 60c13.2-3.4 23-14.6 23-29.7V12.5L32 4Z" fill="var(--brand)" />
      <path d="m21.5 32.5 7 7 14-15" fill="none" stroke="var(--brand-contrast)" strokeWidth="5.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone = statusTone(status);
  return <span className={`badge ${tone}`}>{statusLabel(status)}</span>;
}

export function RiskBadge({ level, score }: { level: string | null; score?: number | null }) {
  if (!level || level === "low") return null;
  const Icon = level === "high" ? ShieldAlert : ShieldQuestion;
  return (
    <span className={`badge ${level === "high" ? "danger" : "warn"}`} title={score != null ? `Risk score ${Math.round(score * 100)}%` : undefined}>
      <Icon size={13} /> {level === "high" ? "High risk" : "Check"}
    </span>
  );
}

const RISK_COPY = {
  low: { title: "Looks safe", Icon: ShieldCheck, color: "var(--success)" },
  medium: { title: "Double-check this payment", Icon: ShieldQuestion, color: "var(--warn)" },
  high: { title: "High risk — stop and verify", Icon: ShieldAlert, color: "var(--danger)" },
};

export function RiskPanel({ risk, compact = false, heldThreshold }: { risk: Risk; compact?: boolean; heldThreshold?: boolean }) {
  const c = RISK_COPY[risk.level];
  const pct = Math.round(risk.score * 100);
  return (
    <section className={`risk ${risk.level}`} aria-live="polite">
      <div className="risk-head">
        <div className="risk-icon"><c.Icon size={22} /></div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="row between">
            <span className="risk-title">{heldThreshold ? "Held for your review" : c.title}</span>
            <span className="small strong num" style={{ color: c.color }}>Risk {pct}%</span>
          </div>
          <div className="risk-sub">{risk.recommendation}</div>
          <div className="meter" aria-hidden="true"><span style={{ width: `${Math.max(pct, 3)}%`, background: c.color }} /></div>
        </div>
      </div>
      {!compact && risk.reasons.length > 0 && <ReasonList reasons={risk.reasons} />}
      {!compact && risk.model && (
        <div className="risk-foot">
          <Info size={14} />
          {risk.model.anomaly != null
            ? <>Rules + Isolation Forest trained on your last {risk.model.trained_on} payments · anomaly {risk.model.anomaly.toFixed(2)}</>
            : <>Rules-based check · the ML model starts after {risk.model.min_samples} payments ({risk.model.trained_on} so far)</>}
        </div>
      )}
    </section>
  );
}

export function ReasonList({ reasons }: { reasons: RiskReason[] }) {
  return (
    <ul className="risk-reasons">
      {reasons.map((r) => (
        <li key={r.code}>
          <AlertTriangle size={16} color={r.weight >= 0.25 ? "var(--danger)" : "var(--warn)"} style={{ marginTop: 2 }} />
          <div><div className="lbl">{r.label}</div><div className="det">{r.detail}</div></div>
        </li>
      ))}
    </ul>
  );
}

export function InsightCard({ insight }: { insight: Insight }) {
  if (insight.state === "done") return null;
  const Icon = insight.state === "overdue" ? AlertTriangle : insight.state === "final" ? Info : Clock;
  return (
    <div className={`insight ${insight.state}`}>
      <div className="row" style={{ alignItems: "flex-start", gap: 10 }}>
        <Icon size={18} style={{ marginTop: 2, flex: "none" }} />
        <div style={{ minWidth: 0 }}>
          <h4>{insight.title}</h4>
          <p>{insight.explain}</p>
          {insight.estimate && (
            <p className="small"><strong>Estimate:</strong> {insight.estimate}{insight.elapsed ? ` (waiting ${insight.elapsed} so far)` : ""}</p>
          )}
          {insight.likely_outcome && <p className="small"><strong>Likely outcome:</strong> {insight.likely_outcome}</p>}
          {insight.actions.length > 0 && <ul>{insight.actions.map((a) => <li key={a}>{a}</li>)}</ul>}
        </div>
      </div>
    </div>
  );
}

export function TxRow({ tx, onOpen }: { tx: Tx; onOpen: (id: string) => void }) {
  const sign = tx.direction === "in" ? 1 : -1;
  return (
    <button className="tx" onClick={() => onOpen(tx.id)} aria-label={`${tx.counterparty ?? "Payment"} ${money(sign * tx.amount)} ${statusLabel(tx.status)}`}>
      <Avatar name={partyLabel(tx)} seed={partyKey(tx)} size={44} />
      <div style={{ minWidth: 0 }}>
        <div className="who">{partyLabel(tx)}</div>
        <div className="what">{tx.memo || (tx.direction === "in" ? "Payment received" : "Payment sent")}</div>
        <div className="meta">
          {tx.category && <span className="badge">{tx.category}</span>}
          {!["COMPLETED", "SUCCESS"].includes(tx.status) && <StatusBadge status={tx.status} />}
          <RiskBadge level={tx.risk_level} score={tx.risk_score} />
          {tx.source === "agent" && <span className="badge brand">Via assistant</span>}
        </div>
      </div>
      <div className={`amt ${tx.direction}`}>
        {money(sign * tx.amount, { sign: true, cur: tx.currency })}
        <span className="sub">{new Date(tx.created_at).toLocaleDateString(getLocale(), { month: "short", day: "numeric" })}</span>
      </div>
    </button>
  );
}

export function TxList({ items, grouped = true, empty }: { items: Tx[]; grouped?: boolean; empty?: ReactNode }) {
  const { openTx } = useApp();
  if (!items.length) return <>{empty ?? <EmptyState title="No payments yet" body="Payments you send or receive will appear here." />}</>;
  if (!grouped) return <div className="tx-list">{items.map((t) => <TxRow key={t.id} tx={t} onOpen={openTx} />)}</div>;
  const groups: { label: string; rows: Tx[] }[] = [];
  for (const t of items) {
    const label = dayLabel(t.created_at);
    if (!groups.length || groups[groups.length - 1].label !== label) groups.push({ label, rows: [] });
    groups[groups.length - 1].rows.push(t);
  }
  return (
    <div>
      {groups.map((g) => (
        <section key={g.label} aria-label={g.label}>
          <div className="tx-day">{g.label}</div>
          {g.rows.map((t) => <TxRow key={t.id} tx={t} onOpen={openTx} />)}
        </section>
      ))}
    </div>
  );
}

export function Timeline({ history }: { history: NonNullable<Tx["history"]> }) {
  return (
    <ul className="timeline">
      {history.map((h, i) => (
        <li key={i}>
          <div className="strong">{statusLabel(h.status)}{h.reason ? <span className="muted"> · {h.reason.replaceAll("_", " ").toLowerCase()}</span> : null}</div>
          <div className="tiny muted">{dateTime(h.at)} · {h.source}</div>
        </li>
      ))}
    </ul>
  );
}

export function EmptyState({ title, body, icon, action }: { title: string; body?: string; icon?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="ic">{icon ?? <CheckCircle2 size={24} />}</div>
      <div className="strong" style={{ color: "var(--ink)" }}>{title}</div>
      {body && <div className="small" style={{ marginTop: 4 }}>{body}</div>}
      {action && <div style={{ marginTop: 14 }}>{action}</div>}
    </div>
  );
}

export function Card({ title, action, children, pad = false }: { title?: ReactNode; action?: ReactNode; children: ReactNode; pad?: boolean }) {
  return (
    <section className="card">
      {(title || action) && <div className="card-head">{typeof title === "string" ? <h2>{title}</h2> : title}{action}</div>}
      <div className={pad ? "card-pad" : "card-body"}>{children}</div>
    </section>
  );
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Payment details">
        <div className="drawer-head">
          <div style={{ flex: 1, minWidth: 0 }}>{title}</div>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={20} /></button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}

export function useToasts() {
  const [toasts, setToasts] = useState<{ id: number; msg: string; kind: string }[]>([]);
  const toast = useCallback((msg: string, kind: "info" | "error" = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 4000);
  }, []);
  const view = (
    <div className="toast-wrap" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>{t.kind === "error" ? <AlertTriangle size={18} /> : <CheckCircle2 size={18} />}<span>{t.msg}</span></div>
      ))}
    </div>
  );
  return { toast, view };
}

export function Skeleton({ h = 18, w = "100%" }: { h?: number; w?: number | string }) {
  return <div className="skeleton" style={{ height: h, width: w }} />;
}

// minimal markdown for assistant replies
export function Markdown({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/);
  const inline = (s: string, key: number) => {
    const parts = s.split(/(\*\*[^*]+\*\*|_[^_]+_)/g);
    return <span key={key}>{parts.map((p, i) => p.startsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : p.startsWith("_") && p.endsWith("_") && p.length > 2 ? <em key={i}>{p.slice(1, -1)}</em> : p)}</span>;
  };
  return (
    <>
      {blocks.map((b, i) => {
        const lines = b.split("\n");
        const bullets = lines.filter((l) => /^\s*[-*•]\s+/.test(l));
        if (bullets.length && bullets.length >= lines.length - 1) {
          const head = lines.find((l) => !/^\s*[-*•]\s+/.test(l));
          return (
            <div key={i}>
              {head && <p>{inline(head, 0)}</p>}
              <ul>{bullets.map((l, j) => <li key={j}>{inline(l.replace(/^\s*[-*•]\s+/, ""), j)}</li>)}</ul>
            </div>
          );
        }
        return <p key={i}>{lines.map((l, j) => <span key={j}>{inline(l, j)}{j < lines.length - 1 && <br />}</span>)}</p>;
      })}
    </>
  );
}
