import { createContext, useContext } from "react";
import { partyKey, partyLabel, type Tx } from "../api";
import { money, statusLabel } from "../format";
import Avatar from "./Avatar";
import type { SendPrefill } from "./SendSheet";

export const SheetCtx = createContext<{ openSend: (p?: SendPrefill) => void }>({
  openSend: () => undefined,
});
export const useSheets = () => useContext(SheetCtx);

const STATUS_TONE: Record<string, string> = {
  COMPLETED: "text-slate-500", SUCCESS: "text-slate-500", PENDING: "text-amber-700", UNCLAIMED: "text-amber-700",
  ONHOLD: "text-amber-700", HELD_FOR_REVIEW: "text-red-700", FAILED: "text-red-700", VOIDED: "text-slate-500",
};

export function when(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `${time} · Today`;
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return `${time} · Yesterday`;
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: d.getFullYear() !== now.getFullYear() ? "numeric" : undefined });
}

// one payment row in a list
export function PayRow({ tx, onOpen }: { tx: Tx; onOpen: (id: string) => void }) {
  const credit = tx.direction === "in";
  const flagged = tx.risk_level === "high" || tx.status === "HELD_FOR_REVIEW";
  return (
    <button className="gbtn w-full flex items-center justify-between gap-3 px-4 py-3 hover:bg-slate-50 text-left" onClick={() => onOpen(tx.id)}>
      <div className="flex items-center gap-3 min-w-0">
        <Avatar name={partyLabel(tx)} seed={partyKey(tx)} size={44} />
        <div className="min-w-0">
          <div className="text-[15px] text-black truncate flex items-center gap-1.5">
            {partyLabel(tx)}
            {flagged && <span className="bg-red-100 text-red-700 text-[10px] px-1.5 py-0.5 rounded">RISK</span>}
          </div>
          <div className="text-xs text-slate-500 truncate">{tx.memo || tx.category || (credit ? "Received" : "Paid")} · {when(tx.created_at)}</div>
        </div>
      </div>
      <div className="text-right shrink-0">
        <div className={`text-[15px] ${credit ? "text-emerald-700" : "text-black"}`}>{credit ? "+" : "−"}{money(tx.amount, { cur: tx.currency })}</div>
        <div className={`text-[11px] ${STATUS_TONE[tx.status] ?? "text-slate-500"}`}>{statusLabel(tx.status)}</div>
      </div>
    </button>
  );
}
